"""CSV-driven image editing. No network or filesystem work on import."""
import argparse
import base64
import csv
from io import BytesIO
import math
import os
from pathlib import Path
import sys
from uuid import uuid4

import numpy as np
from PIL import Image, ImageOps
import requests
from skimage.metrics import structural_similarity

FIELDS = ['row', 'id', 'seq', 'prompt', 'ref_img_path', 'model', 'status',
          'similarity', 'save_path', 'error', 'request_id']


def load_reference(path):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert('RGBA')
        image.load()
    buf = BytesIO()
    image.save(buf, format='PNG')
    raw = buf.getvalue()
    if len(raw) >= 20 * 1024 * 1024:
        raise ValueError('参考图转 PNG 后须小于 20 MiB；请先压缩或缩小图片')
    return image, raw


def calc_similarity(first, second):
    def gray(image):
        rgba = image.convert('RGBA')
        white = Image.new('RGBA', rgba.size, 'white')
        white.alpha_composite(rgba)
        return np.asarray(white.convert('L').resize((512, 512), Image.Resampling.LANCZOS))
    return round(float(structural_similarity(gray(first), gray(second), data_range=255)), 6)


class APIError(RuntimeError):
    def __init__(self, message, request_id=''):
        super().__init__(message)
        self.request_id = request_id


def generate_image(session, prompt, reference, *, key, base_url, model, size, timeout):
    # Multipart boundary must be created by requests; do not set Content-Type manually.
    try:
        response = session.post(
            base_url.rstrip('/') + '/images/edits',
            headers={'Authorization': f'Bearer {key}'},
            data={'model': model, 'prompt': prompt, 'n': '1',
                  'size': size, 'output_format': 'png'},
            files={'image[]': ('reference.png', reference, 'image/png')},
            timeout=(10, timeout), allow_redirects=False)
    except requests.RequestException as exc:
        # Do not include raw exception bodies, which may contain signed URLs or credentials.
        raise APIError(f'网络错误 {type(exc).__name__}；未自动重试，请先确认计费状态') from None
    with response:
        request_id = response.headers.get('x-request-id', '')
        if not 200 <= response.status_code < 300:
            hints = {400: '参数或内容不受支持', 401: 'API Key 无效',
                     403: '无模型权限或需组织验证', 404: '模型或编辑接口不存在',
                     429: '限流或额度不足'}
            raise APIError(f'HTTP {response.status_code}: '
                           + hints.get(response.status_code, '服务异常；请检查服务商日志'), request_id)
        try:
            payload = response.json()
            items = payload.get('data') if isinstance(payload, dict) else None
            if not isinstance(items, list) or len(items) != 1:
                raise ValueError('期望 data 恰好包含 1 张图')
            encoded = items[0].get('b64_json')
            if not isinstance(encoded, str) or not encoded:
                raise ValueError('缺少 b64_json；本版本不下载 URL 返回值')
            raw = base64.b64decode(encoded, validate=True)
            with Image.open(BytesIO(raw)) as source:
                source.load()
                image = source.copy()
            return image, request_id
        except (ValueError, TypeError, AttributeError, OSError, Image.DecompressionBombError) as exc:
            raise APIError('返回结果无效：' + str(exc), request_id) from None


def read_table(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        required = {'id', 'prompt', 'ref_img_path'}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError('CSV 必须包含 id,prompt,ref_img_path 表头')
        rows = list(reader)
    if not rows:
        raise ValueError('CSV 没有任务行')
    return rows


def run_from_table(csv_path, output_dir, *, key, model, count=1,
                   base_url='https://api.openai.com/v1', size='1024x1024', timeout=180):
    csv_path = Path(csv_path).resolve()
    rows = read_table(csv_path)
    run_dir = Path(output_dir).resolve() / ('run_' + uuid4().hex)
    run_dir.mkdir(parents=True, exist_ok=False)
    images_dir = run_dir / 'images'
    images_dir.mkdir()
    failures = 0
    with requests.Session() as session, (run_dir / 'result.csv').open(
            'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        handle.flush()
        for row_number, row in enumerate(rows, 1):
            common = dict(row=row_number, id=row.get('id', ''), prompt=row.get('prompt', ''),
                          ref_img_path=row.get('ref_img_path', ''), model=model)
            try:
                if None in row or any(not (row.get(k) or '').strip()
                                      for k in ('id', 'prompt', 'ref_img_path')):
                    raise ValueError('字段为空或 CSV 列数不匹配')
                path = Path(row['ref_img_path']).expanduser()
                if not path.is_absolute():
                    path = csv_path.parent / path
                reference, raw = load_reference(path)
            except Exception as exc:
                failures += 1
                writer.writerow({**common, 'status': 'error', 'error': str(exc)})
                handle.flush()
                continue
            for seq in range(1, count + 1):
                result = {**common, 'seq': seq, 'status': 'error'}
                try:
                    image, request_id = generate_image(
                        session, row['prompt'], raw, key=key, base_url=base_url,
                        model=model, size=size, timeout=timeout)
                    result['request_id'] = request_id
                    # Numeric row identity avoids path traversal and duplicate-ID overwrites.
                    target = images_dir / f'{row_number:06d}_{seq:02d}.png'
                    temporary = target.with_suffix('.tmp')
                    try:
                        image.save(temporary, format='PNG')
                        temporary.replace(target)
                    finally:
                        temporary.unlink(missing_ok=True)
                    result['save_path'] = str(target)
                    try:
                        result['similarity'] = calc_similarity(reference, image)
                        result['status'] = 'success'
                    except Exception as exc:
                        result.update(status='saved_without_similarity', error=str(exc))
                        failures += 1
                except Exception as exc:
                    result['error'] = str(exc).replace(key, '[REDACTED]') if key else str(exc)
                    result['request_id'] = getattr(exc, 'request_id', '')
                    failures += 1
                writer.writerow(result)
                handle.flush()
                print(f"row={row_number} seq={seq} {result['status']}")
    return run_dir, failures


def main(argv=None):
    parser = argparse.ArgumentParser(description='参考图 + CSV 批量生图与 SSIM 比对')
    parser.add_argument('--csv', required=True, type=Path)
    parser.add_argument('--output-dir', default='outputs', type=Path)
    parser.add_argument('--model', default=os.getenv('OPENAI_IMAGE_MODEL', '').strip())
    parser.add_argument('--count', type=int, default=1, help='每行生成张数，1~10；逐张请求')
    parser.add_argument('--size', choices=['1024x1024', '1536x1024', '1024x1536'], default='1024x1024')
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args(argv)
    key = os.getenv('OPENAI_API_KEY', '').strip()
    base = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1').strip().rstrip('/')
    if not key:
        parser.error('缺少 OPENAI_API_KEY；请先设置环境变量（不要写入代码或 CSV）')
    if not args.model:
        parser.error('请设置 OPENAI_IMAGE_MODEL 或 --model，指定有权限的图片编辑模型')
    if args.model.lower() in {'dall-e-2', 'dall-e-3', 'gpt-image-1'}:
        parser.error('该旧模型已下线或不适用于本编辑流程，请确认可用的图片编辑模型')
    if not 1 <= args.count <= 10 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--count 必须为 1~10，--timeout 必须为有限正数')
    from urllib.parse import urlsplit
    parts = urlsplit(base)
    if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
        parser.error('OPENAI_BASE_URL 必须是无凭据、查询参数和片段的 HTTPS API 根地址（含 /v1）')
    try:
        directory, failures = run_from_table(args.csv, args.output_dir, key=key,
            model=args.model, count=args.count, base_url=base, size=args.size, timeout=args.timeout)
    except (OSError, ValueError, csv.Error) as exc:
        print(f'启动或输出失败：{exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('已中断；已写入的 result.csv 和图片保留。', file=sys.stderr)
        return 130
    print(f'结果：{directory / "result.csv"}；异常数：{failures}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
