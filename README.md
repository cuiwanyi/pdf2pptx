# PDF → 可编辑 PPTX（整页位图型 PDF 的版面重建）

把「每一页其实是一张整页截图」的 PDF，还原成**元素可单独编辑**的 PPTX：文字是真正的文本对象，图片是独立的图片对象，表格是 PowerPoint 原生表格，只有确实无法结构化的复杂视觉（图表、渐变底纹、装饰矢量图）才退化为图片对象。

> 本仓库的 3 套测试素材（`samples/`）全部是「整页 JPEG」型 PDF：每页只有 1 张 852×1212 的图片，没有文字层、没有矢量层。因此本工具走的是 **OCR + 版面分析 + 分层重建** 路线，而不是 PDF 图层直接映射。

## 1. 转换结果一览

| 素材 | 页数 | 文本框 | 独立图片 | 原生表格 | 可编辑字符数 | 平均 SSIM | 耗时 |
|---|---|---|---|---|---|---|---|
| 测试1.pdf | 10 | 194 | 105 | 1 | 3125 | **0.9443** | 59.4s |
| 测试2.pdf | 10 | 154 | 69 | 0 | 2503 | **0.9341** | 55.7s |
| 测试3.pdf | 10 | 117 | 33 | 0 | 1591 | **0.9604** | 43.7s |

- 输出文件：`output/测试1.pptx`、`output/测试2.pptx`、`output/测试3.pptx`（PowerPoint / WPS 均可正常打开）
- 每页对象明细：`output/*.pptx.report.json`
- 逐页对比图（左原图 / 右转换结果）：`output/verify/测试X/compare_01.jpg … compare_10.jpg`
- 逐页相似度数据：`output/verify/测试X/verify.json`

SSIM 为「把生成的 PPTX 渲染回图片」后与原 PDF 页面做的结构相似度，1.0 表示像素级一致。

## 2. 目录结构

```
pdf2pptx/
├── cli.py                 # 命令行入口（单文件 / 整个目录批量）
├── app.py                 # FastAPI 在线 Demo（上传 PDF → 下载 PPTX）
├── verify.py              # 验证：渲染 PPTX → 与原 PDF 逐页 SSIM + 对象清单
├── requirements.txt
├── Dockerfile
├── converter/
│   ├── pdf_doc.py         # PDF 读取与栅格化（坐标统一用 pt）
│   ├── ocr.py             # 文本行检测+识别（调用外部 OCR HTTP API）
│   ├── analyze.py         # 版面分析：段落、字号、颜色、表格、图形区域、背景层
│   ├── build.py           # 组装 PPTX（文本框/图片/表格/背景，z 序、EMU 换算）
│   └── pipeline.py        # 串起流水线并产出报告
├── samples/               # 3 套测试 PDF
├── output/                # 生成的 3 个 PPTX + 报告 + 验证对比图
└── docs/
    ├── 还原情况说明.md      # 各类元素的还原方式与无法还原项
    └── 可编辑性验证.md      # 可编辑性验证方法与实测数据
```

## 3. 环境与安装

- Python 3.10+
- Windows / Linux 均可（渲染验证那一步在 Windows 上需要 WPS，在 Linux 上需要 LibreOffice，**转换本身不需要**）

```bash
pip install -r requirements.txt
```

OCR 不在本地加载模型，而是调用外部 OCR HTTP 服务，所以**必须先有一个可用的 OCR API** 才能跑转换（本地不再安装 rapidocr / onnxruntime）。

### 3.1 OCR API 配置

全部通过环境变量配置（见 `converter/ocr.py`）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OCR_API_URL` | `http://127.0.0.1:8866/ocr` | OCR 接口地址 |
| `OCR_API_TIMEOUT` | `30` | 单页请求超时（秒），只能填数字 |
| `OCR_API_KEY` | 空 | 可选；非空时加 `Authorization: Bearer <key>` |

```bash
# Linux / macOS
export OCR_API_URL=http://127.0.0.1:8866/ocr
export OCR_API_TIMEOUT=60
```

```powershell
# Windows PowerShell
$env:OCR_API_URL = "http://127.0.0.1:8866/ocr"
$env:OCR_API_TIMEOUT = "60"
```

接口契约：`POST` + `multipart/form-data`，字段名 `image`，值为 PNG 二进制。

```json
{"details": [{"box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "text": "识别文本", "score": 0.97}]}
```

- `box` 是 4 点像素坐标（px），内部按 `scale` 换算成 pt
- 响应也可以直接是数组 `[{...}, ...]`
- 字段兼容 `box`/`bounding_box`、`text`/`content`、`score`/`confidence`
- 置信度低于 `min_score`（默认 0.45）的行会被丢弃
- 接口不可达、非 200、或返回非法 JSON 时，该页直接抛 `RuntimeError`，错误信息带 URL 与状态码

后端可以是 RapidOCR 官方 HTTP 服务，或任意满足上述契约的 OCR 网关。

## 4. 使用方式

### 4.1 命令行

```bash
# 单个文件
python cli.py samples/测试1.pdf -o output/测试1.pptx

# 只转指定页（调试用）
python cli.py samples/测试1.pdf -o out.pptx -p 1,4,8

# 整个目录批量
python cli.py samples -o output
```

### 4.2 在线 Demo（上传 PDF → 下载 PPTX）

```bash
uvicorn app:app --host 0.0.0.0 --port 7860
# 浏览器打开 http://127.0.0.1:7860
```

接口：`POST /convert`（multipart 字段 `file`）→ 返回 JSON（含 `download` 地址）；`GET /download/{job}/{name}` 下载 pptx。

### 4.3 Docker / 公网部署

```bash
docker build -t pdf2pptx .
# 镜像内没有本地 OCR 模型，必须把 OCR 服务地址传进去
docker run -p 7860:7860 -e OCR_API_URL=http://host.docker.internal:8866/ocr pdf2pptx
```

Hugging Face Spaces（Docker SDK，免费公网可访问）：

1. 新建 Space，SDK 选 **Docker**；
2. 把本仓库内容推上去（`README.md` 顶部需加 Spaces 的 YAML 头，示例见 `deploy/README-space.md`）；
3. Space 会自动执行 `Dockerfile`，启动 `uvicorn` 监听 7860 端口。

### 4.4 验证（可编辑性 / 还原度）

```bash
python verify.py samples/测试1.pdf output/测试1.pptx output/verify/测试1
```

输出：逐页 SSIM + 每页对象清单（文本框 / 图片 / 表格 / 形状 / 字符数）+ 左右对比图。

## 5. 转换原理（为什么要这么拆）

整页图片里没有语义，只有像素，所以流水线是「先认字，再切块，最后分层组装」：

1. **栅格化**：按 2 倍分辨率渲染（原图 72dpi，2 倍上采样后 OCR 明显更准）。页面尺寸严格等于 PDF 页面尺寸（1pt = 12700 EMU），保证 1:1 版式。
2. **OCR**：调用外部 OCR HTTP 服务，拿回每行文字的四点框、文本、置信度（本地不再加载任何模型）。
3. **字号拟合**：不用 OCR 框高度直接换算，而是量出**页面里真实墨迹的高度**，再用目标字体（微软雅黑）的度量反推字号；宽度偏差过大时用墨迹宽度校正。这样避免 OCR 框留白导致字号偏大。
4. **空格还原**：OCR 常把词间空格吞掉（`BUILDABETTERTOMORROW`）。用墨迹的横向分段与字宽比例把空格补回，中文按字宽比例映射、纯西文按分段序号映射。
5. **段落合并**：按行距、左对齐/居中对齐、字号一致性把行合成段落，一个段落 = 一个文本框（内部保留换行）。
6. **表格还原**：横线定行、文字列投影（或竖线）定列 → 每个单元格单独再 OCR（解决跨列被识别成一行）→ 采样单元格底色与文字颜色 → 输出 PPT 原生表格。
7. **图形区域**：用「与背景的色差 + 边缘」生成内容掩码，去掉文字后做连通域，得到图片/图标/装饰块 → 每个块独立成图片对象。
8. **去字底图**：把文字区域用 inpaint 擦掉、图形区域填底色，剩下的部分作为背景层；若背景基本是纯色则直接用**原生矩形**，只有渐变/底纹才保留为背景图片。
9. **组装**：背景 → 图片 → 文本框 → 表格，按此顺序入栈保证 z 序；文本框 0 内边距、关闭自动换行与自动缩放，并用实际字宽反推框宽，避免 PowerPoint 二次排版把文字挤变形。

## 6. 各类元素还原情况（摘要）

| 元素 | 处理方式 | 可编辑性 |
|---|---|---|
| 标题 / 正文 / 注释 | OCR + 字号/颜色/加粗/对齐估计 → 文本框 | 可改字、改字号、改颜色、拖动 |
| 照片 / 插画 / Logo | 连通域切割 → 独立图片 | 可选中、移动、缩放、替换 |
| 表格 | 网格检测 + 单元格 OCR → 原生表格 | 单元格可编辑，可增删行列 |
| 图表（折线/柱状） | 整块作为图片（数据不可反推） | 仅能作为图片移动缩放 |
| 装饰矢量图形（色块、斜切、渐变、连接线） | 作为图片保留 | 仅能作为图片 |
| 页面背景 | 纯色 → 原生矩形；渐变/底纹 → 背景图 | 可改填充色或替换背景图 |

完整说明（含逐页统计、OCR 已知误差、无法还原项）见 [docs/还原情况说明.md](docs/还原情况说明.md)；
可编辑性验证方法与数据见 [docs/可编辑性验证.md](docs/可编辑性验证.md)。

## 7. 已知限制

1. **图表不是数据图表**：折线/柱状图无法从像素反推出数据系列，只能作为图片保留（符合题目「复杂视觉可用独立图片」的兜底要求）。
2. **字体替换**：素材里是设计字体/思源字体，输出统一映射为「微软雅黑」，字宽会有细微差异（已用墨迹宽度校正，但无法完全一致）。
3. **OCR 误差**：少量汉字识别错误（如「柯幻解猫」→「柯幻熊猫」未纠正、「按定制」漏字），英文空格偶有遗漏。
4. **半透明/发光/阴影**：这类效果在图片层里保留，无法拆成 PPT 阴影对象。
5. **极小图标**：面积过小的装饰会并入背景层，不再单独成对象。
6. 页面是整版大图（如深色封面）时，整页会作为「去字后的背景图」+ 文本对象，页面本身没有可拆分的图片对象。
7. **强依赖外部 OCR 服务**：转换质量直接受 OCR 接口返回的框位置与文本影响；接口不可用时没有本地模型可降级（本地 RapidOCR 已移除）。

## 8. 参数速查

| 参数 | 默认 | 说明 |
|---|---|---|
| `-s/--scale` | 2.0 | 分析用渲染倍率；原图 72dpi 时 2.0 最稳 |
| `-p/--pages` | 全部 | 只转指定页，1 起始 |
| OCR `min_score` | 0.45 | 低于该置信度的文本行丢弃（`converter/ocr.py`） |
| 图形面积阈值 | 0.04% 页面 | 小于该面积的碎片并入背景（`converter/analyze.py`）|
