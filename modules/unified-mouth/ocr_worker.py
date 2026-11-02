"""Bounded isolated CPU OCR worker. stdout is JSON; diagnostics go to stderr."""
import fcntl,json,os,sys
from pathlib import Path

def main():
    root=Path('/opt/astrbot-mouth')
    sys.path.insert(0,str(root))
    from image_library import checked_image
    path=checked_image(sys.argv[1]);confidence=float(sys.argv[2]);limit=int(sys.argv[3])
    # Bound concurrent ONNX engines across both QQ transport and Bilibili ingress.
    with (root/'image-ocr.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
        from PIL import Image
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
        with Image.open(path) as image:
            if image.width*image.height>24000000:raise ValueError('Image pixel limit')
            image=image.convert('RGB');image.thumbnail((2400,2400))
            pixels=np.asarray(image)[:,:,::-1].copy()
        engine=RapidOCR(intra_op_num_threads=1,inter_op_num_threads=1)
        rows,_=engine(pixels)
        lines=[{'text':str(r[1]),'confidence':round(float(r[2]),4)} for r in (rows or []) if float(r[2])>=confidence and str(r[1]).strip()]
        text='\n'.join(x['text'] for x in lines)
        result={'status':'ok' if text else 'empty','text':text[:limit],'truncated':len(text)>limit,
                'lines':lines if len(text)<=limit else []}
        print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
