"""Hugging Face Gradio Space 入口：上传 PDF → 转换 → 下载可编辑 PPTX。

OCR 默认走进程内 RapidOCR（只有设置了 OCR_API_URL 才转到外部 HTTP 服务），
因此单进程即可运行，适合免费 Gradio Space。
"""
import os
import tempfile
import traceback

import gradio as gr

from converter.pdf_doc import PdfDocument
from converter.pipeline import convert_pdf

MAX_PAGES = int(os.environ.get("DEMO_MAX_PAGES", "10"))


def convert(pdf_path: str, max_pages: int):
    if not pdf_path:
        return None, "请先上传一个 PDF 文件。"

    try:
        doc = PdfDocument(pdf_path)
        total = doc.page_count
        doc.close()

        pages = list(range(min(total, int(max_pages))))
        out_dir = tempfile.mkdtemp(prefix="pdf2pptx_demo_")
        stem = os.path.splitext(os.path.basename(pdf_path))[0] or "output"
        out_path = os.path.join(out_dir, f"{stem}.pptx")

        report = convert_pdf(pdf_path, out_path, pages=pages, verbose=False)
        stats = report["stats"]
        msg = (
            f"**转换完成**：{len(pages)}/{total} 页 ｜ "
            f"文本框 {stats['text_boxes']} ｜ 图形/图片 {stats['figures']} ｜ "
            f"表格 {stats['tables']} ｜ 可编辑字符 {stats['editable_text_chars']} ｜ "
            f"用时 {report['elapsed_sec']}s"
        )
        return out_path, msg
    except Exception as e:  # noqa: BLE001 - 界面需要把失败原因透出给用户
        traceback.print_exc()
        return None, f"转换失败：{e}"


with gr.Blocks(title="PDF → 可编辑 PPTX") as demo:
    gr.Markdown(
        "# PDF → 可编辑 PPTX\n"
        "上传 PDF，输出中的**文字、图片、图形、表格**均为独立可编辑对象，"
        "而不是把整页当成一张图片。\n\n"
        "> 免费 CPU 实例较慢，建议单次不超过 10 页。"
    )
    with gr.Row():
        with gr.Column():
            pdf_in = gr.File(label="上传 PDF", file_types=[".pdf"], type="filepath")
            pages_slider = gr.Slider(1, 30, value=MAX_PAGES, step=1, label="最多转换页数")
            run_btn = gr.Button("开始转换", variant="primary")
        with gr.Column():
            pptx_out = gr.File(label="下载 PPTX")
            status = gr.Markdown("等待上传…")

    run_btn.click(convert, [pdf_in, pages_slider], [pptx_out, status])

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1).launch()
