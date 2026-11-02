"""Private line protocol. One OCR model per worker; no model API requests."""
import os
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import base64,io,json,sys,time
from PIL import Image
import numpy as np
from rapidocr_onnxruntime import RapidOCR

def main():
    engine=RapidOCR(intra_op_num_threads=1,inter_op_num_threads=1)
    print(json.dumps({'ready':True}),flush=True)
    for line in sys.stdin:
        try:
            request=json.loads(line);started=time.monotonic()
            im=Image.open(io.BytesIO(base64.b64decode(request['png'],validate=True))).convert('RGB')
            if im.width*im.height>2000000:raise ValueError('crop too large')
            rows,_=engine(np.asarray(im)[:,:,::-1].copy());result=[]
            for box,text,score in rows or []:
                if score<0.45:continue
                xs=[v[0] for v in box];ys=[v[1] for v in box]
                result.append({'text':text,'score':float(score),'box':[min(xs),min(ys),max(xs),max(ys)]})
            print(json.dumps({'lines':result,'seconds':time.monotonic()-started},ensure_ascii=False),flush=True)
        except Exception as exc:print(json.dumps({'error':type(exc).__name__}),flush=True)

if __name__=='__main__':main()
