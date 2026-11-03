"""在线 Demo：上传 PDF → 返回可编辑 PPTX。

启动：
    uvicorn app:app --host 0.0.0.0 --port 7860
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from converter.pipeline import convert_pdf

app = FastAPI(title="PDF → 可编辑 PPTX")
WORK = os.path.join(tempfile.gettempdir(), "pdf2pptx_web")
os.makedirs(WORK, exist_ok=True)

INDEX = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>PDF → 可编辑 PPTX</title>
<style>
  body{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#f5f7fa;margin:0;color:#1f2937}
  .wrap{max-width:720px;margin:64px auto;background:#fff;border-radius:14px;padding:36px 40px;
        box-shadow:0 8px 28px rgba(15,23,42,.08)}
  h1{font-size:22px;margin:0 0 8px}
  p.tip{color:#6b7280;font-size:14px;line-height:1.7;margin:0 0 24px}
  input[type=file]{display:block;margin:18px 0}
  button{background:#2563eb;color:#fff;border:0;border-radius:8px;padding:11px 22px;font-size:15px;cursor:pointer}
  button:disabled{opacity:.5;cursor:not-allowed}
  #msg{margin-top:18px;font-size:14px;color:#6b7280;white-space:pre-wrap}
  .ok{color:#059669}
  .err{color:#dc2626}
</style>
</head>
<body>
<div class="wrap">
  <h1>PDF → 可编辑 PPTX</h1>
  <p class="tip">上传以「整页图片」形式保存的 PDF，服务会把页面拆解为
    <b>可编辑文本框</b>、<b>独立图片对象</b>、<b>原生表格</b>与<b>背景层</b>，
    输出可用 PowerPoint / WPS 打开的 PPTX。</p>
  <input id="f" type="file" accept="application/pdf"/>
  <button id="b" onclick="go()">开始转换</button>
  <div id="msg"></div>
</div>
<script>
async function go(){
  const f=document.getElementById('f').files[0];
  const msg=document.getElementById('msg'), b=document.getElementById('b');
  if(!f){msg.className='err';msg.textContent='请先选择 PDF 文件';return;}
  const fd=new FormData(); fd.append('file',f);
  b.disabled=true; msg.className=''; msg.textContent='转换中，约 5~10 秒/页…';
  try{
    const r=await fetch('/convert',{method:'POST',body:fd});
    if(!r.ok){throw new Error(await r.text());}
    const d=await r.json();
    msg.className='ok';
    msg.innerHTML='完成：'+d.filename+'<br/>页数 '+d.pages+' ｜ 文本框 '+d.stats.text_boxes+
      ' ｜ 图片 '+d.stats.figures+' ｜ 表格 '+d.stats.tables+' ｜ 用时 '+d.elapsed_sec+'s<br/>'+
      '<a href="'+d.download+'" download>⬇ 下载 PPTX</a>';
  }catch(e){msg.className='err';msg.textContent='转换失败：'+e.message;}
  b.disabled=false;
}
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX


@app.get("/healthz")
def health():
    return {"ok": True}


@app.post("/convert")
def convert(background: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "只支持 PDF 文件")
    job = uuid.uuid4().hex[:12]
    job_dir = os.path.join(WORK, job)
    os.makedirs(job_dir, exist_ok=True)
    src = os.path.join(job_dir, "input.pdf")
    with open(src, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        out = os.path.join(job_dir, os.path.splitext(os.path.basename(file.filename))[0] + ".pptx")
        rep = convert_pdf(src, out, work_dir=job_dir, verbose=False)
    except Exception as e:      # 转换失败时给出明确错误
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, f"转换失败：{e}")

    return {
        "filename": os.path.basename(out),
        "pages": len(rep["pages"]),
        "stats": rep["stats"],
        "elapsed_sec": rep["elapsed_sec"],
        "download": f"/download/{job}/{os.path.basename(out)}",
    }


@app.get("/download/{job}/{name}")
def download(job: str, name: str, background: BackgroundTasks):
    path = os.path.join(WORK, job, name)
    if not os.path.isfile(path) or not name.endswith(".pptx"):
        raise HTTPException(404, "文件不存在")
    # 文件发送完再删临时目录，避免磁盘堆积
    background.add_task(shutil.rmtree, os.path.join(WORK, job), True)
    return FileResponse(path, filename=name,
                        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
