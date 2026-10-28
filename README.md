# nexo-ai-skill-v2

**ChatGPT 手动生图 + 本地图片归档与 SSIM 检查。** Python 3.10+。

本脚本完全离线，不调用 OpenAI API，不需要 API Key，不产生 API 生图费用。图片由你在 ChatGPT 中手动上传参考图、发送提示词并下载。脚本不操作聊天网页、不自动生成或下载图片，也不会扩充会员额度或取消会员使用限制。

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

本次修改尚在草稿 PR 的 `fix/runnable-image-batch` 分支，合并前请在安装依赖前切换：

```bash
git switch fix/runnable-image-batch
```

无需环境变量，也不读取 `.env`。移除了 requests 和 pandas。依赖使用兼容范围，未锁定全部传递依赖。

## 第一步：整理待生图任务

新建 UTF-8 CSV（可带 BOM），参考 `input.example.csv`：

```csv
id,prompt,ref_img_path
001,"精修产品，保留形状和材质，去除褶皱，保持原场景",refs/product.png
```

三个字段必填，参考图须为本地文件。相对路径以 **CSV 所在目录** 为基准，也支持绝对路径和 `~`。示例不附商品图片，请放入自己的图。含逗号/换行的字段须用双引号包裹，内部双引号写成两个双引号。ID 按字符串读取，保留前导零。

```bash
python main.py prepare --csv input.example.csv
```

输出 `outputs/run_<唯一ID>/tasks.csv` 及 `prompts/000001.txt` 等提示词文件。`tasks.csv` 保留 id、prompt、参考图绝对路径，并增加 generated_img_path（待填）、status、prompt_file、error。坏图/缺图标记 error，其他任务继续。

## 第二步：在 ChatGPT 中手动生图

1. 打开自己的 ChatGPT，逐项上传 tasks.csv 对应的参考图。
2. 复制相应 prompt 文本发送，查看并确认生成效果。
3. 将满意的结果下载到电脑，建议按任务命名，例如 `downloads/001_1.png`。
4. 在 tasks.csv 的 `generated_img_path` 列填入下载图片的本地路径。绝对路径最直观；相对路径以 tasks.csv 的目录为基准。**填写路径不会自动下载图片。**

每张结果占一行。同一任务有多张结果时，复制该任务行、分别填不同生成图片路径；可额外加 `seq` 列记录 1、2、3。行内 seq 未填写时默认为 1。prepare 输出的旧 status 不影响 collect，collect 会重新检查文件。

可参考 `completed.example.csv`：

```csv
id,prompt,ref_img_path,generated_img_path,seq
001,"精修产品，保留形状和材质",refs/product.png,downloads/001_1.png,1
001,"精修产品，保留形状和材质",refs/product.png,downloads/001_2.png,2
```

会员是否可继续生图以 ChatGPT 实际提示为准；本工具不承诺不限量或无人值守批量生成。不在这里填写账号密码、Cookie 或 API Key。

## 第三步：本地归档与比对

```bash
python main.py collect --csv outputs/run_实际ID/tasks.csv
# 或使用你自己填写完的表格
python main.py collect --csv completed.csv --output-dir outputs
```

程序逐行打开你填写的结果图片，转为 PNG 归档，再与参考图计算 SSIM。原下载文件和参考图不修改、不删除。每次新建独立 run 目录，图片名用任务行号，避免重复 ID、特殊字符及路径字符造成覆盖。重复运行会另建归档，不会重复调用生图服务。

输出：`outputs/run_<唯一ID>/images/000001.png` 和 `result.csv`。

| result.csv 字段 | 含义 |
| --- | --- |
| row | 本次输入 CSV 的任务行号，从 1 开始，不计表头 |
| id / seq | 原任务 ID / 图片序号 |
| prompt | 原提示词 |
| ref_img_path | 输入中的参考图路径 |
| generated_img_path | 输入中的下载图片路径 |
| status | success / error / saved_without_similarity |
| similarity | 灰度 SSIM，失败时为空 |
| save_path | 已归档 PNG 的绝对路径 |
| error | 失败原因 |

每行处理后立即 flush CSV，单行失败继续后续行。图片已归档但参考图不存在或比对失败时，标记 saved_without_similarity 并保留图片。生成图片路径为空、文件不存在或损坏时记 error。磁盘已满、输出目录无权限或 CSV 无法写入时可能停止；Ctrl+C 保留已写文件。

退出码：0 全部成功；1 有行失败/比对失败；2 输入表头或输出等全局错误；130 用户中断。

## SSIM 能检查什么

将透明图叠白底、转灰度、缩放为 512×512，再按 data_range=255 比较。SSIM 理论范围 -1~1，越接近 1 表示这种预处理后的像素结构越接近。

它不能验证颜色准确性、材质真实性、文字正确性或产品细节完全一致。背景、裁切、构图变化会影响分数。没有自动合格阈值；分数用于辅助筛选，产品图片仍需人工复核。本工具不设置画质或生图模型，也不决定 ChatGPT 的生成质量。

## 常见问题

- 找不到参考图/结果图：确认是本地文件；相对路径基于输入 CSV 所在目录。
- 下载结果路径为空：先在 ChatGPT 下载，再填表；脚本不会自动生图。
- CSV 表头错误：prepare 要求 id,prompt,ref_img_path；collect 还要求 generated_img_path。
- 图片损坏：尝试重新下载并打开验证；支持 Pillow 可识别的图片，归档统一输出 PNG。
- Permission denied / 磁盘已满：换可写输出目录，检查空间，关闭占用表格的程序。
- 同一商品多张图片：每张单独一行，填写各自路径和 seq。
- 尚无真实商品图：先用自己的两张图片跑 collect 检查流程。仓库测试使用合成图片，不代表实际商品图效果。

## 测试

```bash
python -m unittest discover -s tests -v
python main.py --help
```
