"""Lab-only disk-backed loader; weights remain FP32 as in Genie's stock CPU loader."""
from pathlib import Path
import os
import numpy as np
import onnx
import onnxruntime as ort
CACHE=Path('/opt/genie-tts-lab/disk-cache')

def install():
 import importlib
 mm=importlib.import_module('genie_tts.ModelManager')
 original=ort.InferenceSession
 def session(path,*args,**kwargs):
  options=kwargs.get('sess_options') or ort.SessionOptions()
  options.intra_op_num_threads=2;options.inter_op_num_threads=1
  kwargs['sess_options']=options
  return original(path,*args,**kwargs)
 ort.InferenceSession=session
 def disk_load(onnx_path,fp16_bin_path,providers,sess_options=None):
  CACHE.mkdir(exist_ok=True)
  weights=CACHE/(Path(fp16_bin_path).name+'.fp32')
  if not weights.exists() or weights.stat().st_size!=Path(fp16_bin_path).stat().st_size*2:
   temp=weights.with_suffix('.new')
   with open(fp16_bin_path,'rb') as src,open(temp,'wb') as dst:
    while data:=src.read(1024*1024):dst.write(np.frombuffer(data,dtype=np.float16).astype(np.float32).tobytes())
   os.replace(temp,weights)
  graph=CACHE/(Path(onnx_path).name+'.disk.onnx')
  if not graph.exists():
   model=onnx.load(onnx_path,load_external_data=False)
   for tensor in model.graph.initializer:
    if tensor.data_location==onnx.TensorProto.EXTERNAL:
     for item in tensor.external_data:
      if item.key=='location':item.value=str(weights)
   onnx.save(model,str(graph))
  result=session(str(graph),providers=providers,sess_options=sess_options)
  print('DISK LOADED',Path(onnx_path).name,flush=True)
  return result
 mm.load_session_with_fp16_conversion=disk_load
