from __future__ import annotations
import os, tempfile, time, uuid
from pathlib import Path
import fitz, httpx

class WorkerError(RuntimeError): pass

def safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"PDF 下载失败（status={exc.response.status_code}）"
    if isinstance(exc, httpx.TimeoutException):
        return "网络请求超时"
    if isinstance(exc, httpx.NetworkError):
        return "网络连接失败"
    message = str(exc).strip() if isinstance(exc, WorkerError) else exc.__class__.__name__
    if "http://" in message or "https://" in message:
        return exc.__class__.__name__
    return (message or exc.__class__.__name__)[:700]

def normalize(text: str) -> str: return text.replace("\r\n","\n").replace("\r","\n").strip()
def has_raster(page: fitz.Page) -> bool:
    try:
        if page.get_image_info(): return True
    except Exception: pass
    try: return bool(page.get_images(full=True))
    except Exception: return False

def outline(pdf: fitz.Document) -> list[dict[str, object]]:
    out=[]
    for raw in pdf.get_toc(simple=True):
        if len(raw)>=3:
            level,title,page=int(raw[0])," ".join(str(raw[1]).split()),int(raw[2])
            if level>0 and title and 1<=page<=pdf.page_count: out.append({"level":level,"title":title[:300],"page_number":page})
    return out

class API:
    def __init__(self, base: str, key: str, doc: uuid.UUID, attempt: uuid.UUID):
        self.base=base.rstrip("/"); self.doc=doc; self.attempt=attempt
        self.client=httpx.Client(timeout=httpx.Timeout(120,connect=30),headers={"Authorization":f"Bearer {key}"})
    def post(self,path,payload,retries=4):
        last=None
        for n in range(retries):
            try:
                r=self.client.post(self.base+path,json=payload)
                if r.status_code<400: return r.json()
                if r.status_code not in {429,500,502,503,504}: raise WorkerError(f"API rejected status={r.status_code}: {r.text[:300]}")
                last=WorkerError(f"API unavailable status={r.status_code}")
            except (httpx.TimeoutException,httpx.NetworkError) as e: last=e
            time.sleep(min(8,2**n))
        raise WorkerError(str(last or "API request failed"))
    def claim(self): return self.post(f"/api/v1/internal/ocr/jobs/{self.doc}/claim",{"attempt_id":str(self.attempt)})
    def heartbeat(self,page): return self.post(f"/api/v1/internal/ocr/jobs/{self.doc}/heartbeat",{"attempt_id":str(self.attempt),"current_page":page})
    def page(self,n,text,method): return self.post(f"/api/v1/internal/ocr/jobs/{self.doc}/pages/{n}",{"attempt_id":str(self.attempt),"text":text,"extraction_method":method})
    def finish(self,toc): return self.post(f"/api/v1/internal/ocr/jobs/{self.doc}/finish",{"attempt_id":str(self.attempt),"outline":toc})
    def fail(self,page,message):
        try: self.post(f"/api/v1/internal/ocr/jobs/{self.doc}/fail",{"attempt_id":str(self.attempt),"page_number":page,"error_message":message[:1000]},retries=2)
        except Exception: pass

def build_engine(threads:int):
    for name in ("OMP_NUM_THREADS","OMP_THREAD_LIMIT","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS","ORT_NUM_THREADS"): os.environ[name]=str(threads)
    from rapidocr import RapidOCR
    return RapidOCR(params={"Global.log_level":"error","Global.max_side_len":2048,"Det.limit_type":"max","Det.limit_side_len":2048,"Cls.cls_batch_num":1,"Rec.rec_batch_num":1,"EngineConfig.onnxruntime.intra_op_num_threads":threads,"EngineConfig.onnxruntime.inter_op_num_threads":threads})

def main():
    base=os.environ["REVIA_API_BASE_URL"].strip(); key=os.environ["REVIA_OCR_WORKER_KEY"].strip()
    doc=uuid.UUID(os.environ["REVIA_DOCUMENT_ID"]); attempt=uuid.UUID(os.environ["REVIA_ATTEMPT_ID"])
    api=API(base,key,doc,attempt); current=None; tmp=None
    try:
        job=api.claim(); completed=set(int(x) for x in job["completed_pages"])
        with httpx.stream("GET",job["download_url"],timeout=300,follow_redirects=True) as r:
            r.raise_for_status(); fd,name=tempfile.mkstemp(suffix=".pdf"); os.close(fd); tmp=Path(name)
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes(1024*1024): f.write(chunk)
        if tmp.stat().st_size!=int(job["size_bytes"]): raise WorkerError("downloaded PDF size mismatch")
        engine=None; threshold=max(int(job["minimum_text_length"]),32); dpi=int(job["ocr_dpi"])
        with fitz.open(tmp) as pdf:
            if not pdf.is_pdf or pdf.page_count!=int(job["total_pages"]) or pdf.page_count>int(job["max_pdf_pages"]): raise WorkerError("PDF metadata mismatch")
            toc=outline(pdf)
            for n in range(1,pdf.page_count+1):
                if n in completed: continue
                current=n; api.heartbeat(n); page=pdf.load_page(n-1)
                text=normalize(page.get_text("text",sort=True)); method="text"
                if has_raster(page) and len(text)<threshold:
                    if engine is None: engine=build_engine(max(1,int(os.getenv("OCR_THREADS","2"))))
                    pix=page.get_pixmap(dpi=dpi,colorspace=fitz.csGRAY,alpha=False)
                    result=engine(pix.tobytes("png")); text=normalize("\n".join(str(x).strip() for x in (getattr(result,"txts",None) or ()) if str(x).strip())); method="ocr"
                    del pix,result
                api.page(n,text,method); del page
            api.finish(toc)
    except Exception as exc:
        message = "GitHub OCR 任务失败：" + safe_error(exc)
        api.fail(current, message)
        raise SystemExit(message) from None
    finally:
        if tmp is not None: tmp.unlink(missing_ok=True)

if __name__=="__main__": main()
