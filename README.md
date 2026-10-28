# nexo-ai-skill-v2

使用 CSV 中的提示词和本地参考图调用图片编辑 API，保存 PNG，并计算灰度结构相似度 SSIM。Python 3.10+。入口为 `main.py`，取代原 `main.txt`。

## 安装

```bash
git clone https://github.com/794762692-ui/nexo-ai-skill-v2.git
cd nexo-ai-skill-v2
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell 改用：.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

依赖采用兼容版本范围，尚不是锁定全部传递依赖的 lockfile。移除了未再使用的 pandas，CSV 使用 Python 标准库，保留 ID 前导零。

## 环境变量

| 变量 | 含义 |
| --- | --- |
| OPENAI_API_KEY | 必填；有计费和图片编辑权限的 API Key |
| OPENAI_IMAGE_MODEL | 必填，或用 `--model` 指定；必须支持图片编辑 |
| OPENAI_BASE_URL | 可选，默认 `https://api.openai.com/v1`；需包含 `/v1`，不要包含 `/images/edits` |

macOS / Linux：

```bash
export OPENAI_API_KEY='你的API密钥'
export OPENAI_IMAGE_MODEL='你的可用图片编辑模型ID'
```

Windows PowerShell：

```powershell
$env:OPENAI_API_KEY='你的API密钥'
$env:OPENAI_IMAGE_MODEL='你的可用图片编辑模型ID'
```

不自动读取 `.env`。不要提交密钥，也不要将密钥放在 CSV 或命令行参数中。自定义 BASE_URL 会收到你的密钥、提示词和参考图，只设置为你信任的服务商地址。

## CSV 示例

保存为 UTF-8（兼容 BOM），参考 `input.example.csv`：

```csv
id,prompt,ref_img_path
001,"精修产品，保留形状和材质，去除褶皱，保持原场景",refs/product.png
002,"保留产品细节，替换为浅灰色背景",refs/second.jpg
```

三个字段均必填；提示词含逗号或换行时必须用双引号包裹，内部双引号写成两个双引号。相对参考图路径以 **CSV 所在目录** 为基准；支持绝对路径和 `~`，不支持远程图片 URL。请放入你自己的参考图，示例路径本身不附图片。

## 运行

先用每行一张验证，再扩大数量：

```bash
python main.py --csv input.example.csv
python main.py --csv input.csv --count 10 --output-dir outputs --size 1024x1024 --timeout 180
python main.py --help
```

`--count` 为每行 1~10 张，默认 1。按行、按张串行，每次请求 `n=1`，因此一行十张会发十次请求；每张均使用原始参考图。无参考图时记录错误，不退回纯文字生图。

参考图经 EXIF 方向纠正后转为 RGBA PNG，以 multipart `image[]` 实际上传至 `/images/edits`，另发送 model/prompt/n/size/output_format。不发送 GPT Image 不支持的 `response_format`。本地统一限制转 PNG 后小于 20 MiB；这是保守的客户端限制，不代表所有模型的服务端限制。服务商可能还有分辨率、文件大小或内容限制。

## 输出

每次新建 `outputs/run_<唯一ID>/`，内含 `images/000001_01.png` 和 `result.csv`。图片名使用任务行号和序号，避免重复 ID、特殊字符或路径字符引起覆盖；ID 原样保留在 CSV。输出路径为绝对路径。重复运行会重新生成并重新计费，不提供断点续跑。

| 字段 | 含义 |
| --- | --- |
| row | CSV 任务行号，从 1 开始，不计表头 |
| id | 原始 ID 字符串，保留前导零 |
| seq | 行内图片序号；参考图/行校验失败时为空 |
| prompt / ref_img_path | 原输入提示词 / 路径 |
| model | 本次请求模型 |
| status | success / error / saved_without_similarity |
| similarity | 灰度 SSIM，失败时为空 |
| save_path | 已成功保存图片的绝对路径 |
| error | 错误信息；正常成功时为空 |
| request_id | 服务端 x-request-id，若返回 |

每张成功或失败后立即写入并 flush CSV；无效输入行记录一行错误，跳过该行全部生成；单张失败继续下一张和下一行。图片已保存但 SSIM 失败时保留图片并标记 `saved_without_similarity`。输出目录、磁盘或 CSV 写入失败会停止，不能承诺在存储不可用时继续保存。Ctrl+C 保留已写结果，当前请求可能已计费但尚未收到结果。

退出码：0 全部成功；1 有任务失败或相似度失败；2 配置/输入/输出错误；130 用户中断。

## 常见报错

- 缺少 Key / 模型：先设置环境变量；脚本会在发送请求前检查。
- HTTP 400：检查提示词、模型、尺寸、multipart 参数和内容限制。
- HTTP 401/403：检查密钥、项目权限、账户计费及组织验证要求。
- HTTP 404：检查 BASE_URL、模型 ID 及 `/images/edits` 支持情况。
- HTTP 429：检查余额/配额或限流；降低任务数量或稍后手动重跑。
- 超时/网络/5xx：检查服务状态和 request_id；必要时提高 `--timeout`。脚本不自动重试付费生成请求，避免响应丢失时重复计费；失败后会继续其余任务，因此全局鉴权失败也会产生后续错误记录。
- 返回结果无效：要求 `data` 恰好一项，带有效 `b64_json` 且能解码为图片；空结果、坏 JSON、坏 base64、URL-only 均记录错误，不将无效内容写成图片。
- 文件不存在：参考路径基于 CSV 目录，不是当前终端目录。
- Permission denied / 磁盘已满：换可写目录并检查空间。

## 能力边界与待确认事项

截至 2026-10-28，官方文档说明 `dall-e-3` 已从 API 移除，且本来只有图片输出能力，不能把参考图简单塞进文字生图请求。官方还标注 `gpt-image-1` 已于 2026-10-23 关闭。因此本工具不静默选择替代模型，要求明确配置支持编辑的模型；例如官方文档当前列出的 `gpt-image-2`，仍需确认你的账户权限。

请确认：实际使用官方 API 还是第三方兼容服务、可用编辑模型 ID、是否支持 multipart `image[]` 和 base64 返回、账户额度/并发/尺寸限制。第三方名为 dall-e-3 的接口可能只是别名，其真实能力无法从本仓库判断；本版本拒绝旧模型名。若服务只返回 URL 或要求其他 JSON schema，需依据其文档添加单独适配，不保证通用兼容。

SSIM 理论范围为 -1~1；这里先将透明图叠白底、转灰度、缩放至 512×512，再按 data_range=255 比较。它只衡量像素结构，不是产品一致性、材质真实性、文字正确性或商业可用性的判定。裁切、构图、背景变化均会影响分数，灰度比较不能检查颜色准确性。参考图参与请求也不保证模型完全保留尺寸、细节或材质，商品图须人工复核。

本次验证采用模拟 HTTP 响应，不消耗 API 额度；实际账户、真实生成质量和服务限流尚未实测。没有自动重试、并行生成、URL 下载、mask、多参考图或续跑能力。

官方资料：
- https://developers.openai.com/api/docs/models/dall-e-3
- https://developers.openai.com/api/reference/resources/images/methods/edit

## 离线测试

```bash
python -m unittest discover -s tests -v
```
