"""pdf2pptx：把「整页位图型 PDF」拆成可编辑的 PPTX 对象。

模块划分：
- pdf_doc  : PDF 读取与页面栅格化（坐标统一用 PDF 点 pt）
- ocr      : 文字行检测 + 识别（RapidOCR / PP-OCR 模型）
- analyze  : 版面分析（段落分组、字体样式、表格、图形区域、背景层）
- build    : 组装 PPTX（文本框 / 图片 / 表格 / 背景），负责 z 序与 EMU 换算
- pipeline : 串起整条流水线，产出 pptx 与结构化报告
"""

__version__ = "0.1.0"
