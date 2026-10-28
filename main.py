"""Offline task preparation and image archiving. No API calls."""
import argparse
import csv
from pathlib import Path
import sys
from uuid import uuid4

import numpy as np
from PIL import Image, ImageOps
from skimage.metrics import structural_similarity

FIELDS = ['row', 'id', 'seq', 'prompt', 'ref_img_path', 'generated_img_path',
          'status', 'similarity', 'save_path', 'error']


def load_image(path):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert('RGBA')
        image.load()
    return image


def calc_similarity(first, second):
    def gray(image):
        rgba = image.convert('RGBA')
        white = Image.new('RGBA', rgba.size, 'white')
        white.alpha_composite(rgba)
        return np.asarray(white.convert('L').resize((512, 512), Image.Resampling.LANCZOS))
    return round(float(structural_similarity(gray(first), gray(second), data_range=255)), 6)


def read_table(path, required):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not set(required).issubset(reader.fieldnames):
            raise ValueError('CSV 必须包含：' + ','.join(required))
        rows = list(reader)
    if not rows:
        raise ValueError('CSV 没有任务行')
    return rows


def resolve_path(value, table):
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (table.parent / path).resolve()


def validate_row(row, required):
    if None in row or any(not (row.get(k) or '').strip() for k in required):
        raise ValueError('字段为空或 CSV 列数不匹配')


def new_run(output):
    directory = Path(output).resolve() / ('run_' + uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def prepare(table, output):
    table = Path(table).resolve()
    required = ['id', 'prompt', 'ref_img_path']
    rows = read_table(table, required)
    directory = new_run(output)
    prompts = directory / 'prompts'
    prompts.mkdir()
    failures = 0
    fields = required + ['generated_img_path', 'status', 'prompt_file', 'error']
    with (directory / 'tasks.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        handle.flush()
        for number, row in enumerate(rows, 1):
            result = {k: row.get(k, '') for k in required}
            result['generated_img_path'] = ''
            try:
                validate_row(row, required)
                reference = resolve_path(row['ref_img_path'], table)
                load_image(reference)
                result['ref_img_path'] = str(reference)
                prompt_file = prompts / f'{number:06d}.txt'
                prompt_file.write_text(row['prompt'], encoding='utf-8')
                result.update(status='ready', prompt_file=str(prompt_file))
            except Exception as exc:
                failures += 1
                result.update(status='error', error=str(exc))
            writer.writerow(result)
            handle.flush()
    return directory, failures


def collect(table, output):
    table = Path(table).resolve()
    required = ['id', 'prompt', 'ref_img_path', 'generated_img_path']
    rows = read_table(table, required)
    directory = new_run(output)
    images = directory / 'images'
    images.mkdir()
    failures = 0
    with (directory / 'result.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        handle.flush()
        for number, row in enumerate(rows, 1):
            result = {k: row.get(k, '') for k in required}
            result.update(row=number, seq=row.get('seq', '') or 1, status='error')
            try:
                validate_row(row, required)
                generated = load_image(resolve_path(row['generated_img_path'], table))
                target = images / f'{number:06d}.png'
                temporary = target.with_suffix('.tmp')
                try:
                    generated.save(temporary, format='PNG')
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
                result['save_path'] = str(target)
                # Archive first: a missing reference or metric failure must not lose the output.
                try:
                    reference = load_image(resolve_path(row['ref_img_path'], table))
                    result['similarity'] = calc_similarity(reference, generated)
                    result['status'] = 'success'
                except Exception as exc:
                    result.update(status='saved_without_similarity', error=str(exc))
                    failures += 1
            except Exception as exc:
                result['error'] = str(exc)
                failures += 1
            writer.writerow(result)
            handle.flush()
            print(f"row={number} {result['status']}")
    return directory, failures


def main(argv=None):
    parser = argparse.ArgumentParser(description='ChatGPT 手动生图任务整理与离线图片比对，无需 API Key')
    parser.add_argument('command', choices=['prepare', 'collect'])
    parser.add_argument('--csv', required=True, type=Path)
    parser.add_argument('--output-dir', default='outputs', type=Path)
    args = parser.parse_args(argv)
    try:
        directory, failures = (prepare if args.command == 'prepare' else collect)(args.csv, args.output_dir)
    except (OSError, ValueError, csv.Error) as exc:
        print(f'输入或输出失败：{exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('已中断，已写入的文件保留。', file=sys.stderr)
        return 130
    print(f'输出：{directory}；异常数：{failures}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
