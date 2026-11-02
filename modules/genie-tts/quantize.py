from pathlib import Path
import os,shutil,gc,onnx
from onnxruntime.quantization import quantize_dynamic,QuantType
root=Path('/opt/genie-tts-lab');out=root/'tomori-onnx-int8';out.mkdir(exist_ok=True)
for p in (root/'tomori-onnx').iterdir():
 if p.is_file() and p.name not in ['t2s_first_stage_decoder_fp32.onnx','t2s_stage_decoder_fp32.onnx']:
  target=out/p.name
  if not target.exists():os.link(p,target)
for name in ['t2s_first_stage_decoder_fp32.onnx','t2s_stage_decoder_fp32.onnx']:
 print('QUANTIZE',name,flush=True)
 source=root/'disk-cache'/(name+'.disk.onnx')
 model=onnx.load(str(source),load_external_data=False)
 for tensor in model.graph.initializer:
  for item in tensor.external_data:
   if item.key=='location':item.value=Path(item.value).name
 relative=root/'disk-cache'/(name+'.relative.onnx');onnx.save(model,str(relative));del model
 quantize_dynamic(str(relative),str(out/name),op_types_to_quantize=['MatMul','Gemm'],weight_type=QuantType.QInt8,per_channel=True,reduce_range=True,use_external_data_format=True,extra_options={'MatMulConstBOnly':True})
 gc.collect();print('DONE',name,flush=True)
print('QUANTIZATION COMPLETE',flush=True)
