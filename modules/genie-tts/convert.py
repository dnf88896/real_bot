import os,sys,importlib.util
from pathlib import Path
root=Path('/opt/genie-tts-lab');os.chdir(root)
site=root/'venv/lib/python3.11/site-packages';sys.path.insert(0,str(site))
# Conversion needs only the converter, not playback or downloaded frontend resources.
spec=importlib.util.spec_from_file_location('genie_tts',site/'genie_tts/__init__.py',submodule_search_locations=[str(site/'genie_tts')])
pkg=importlib.util.module_from_spec(spec);sys.modules['genie_tts']=pkg
import torch
torch.set_num_threads(2)
from genie_tts.Converter.Converter import convert
convert(torch_ckpt_path='/opt/tomori-tts/models/高松灯V2.5-e20.ckpt',torch_pth_path='/opt/tomori-tts/models/高松灯V2.5_e60_s600.pth',output_dir=str(root/'tomori-onnx'))
required=['t2s_encoder_fp32.bin','t2s_encoder_fp32.onnx','t2s_first_stage_decoder_fp32.onnx','t2s_shared_fp16.bin','t2s_stage_decoder_fp32.onnx','vits_fp16.bin','vits_fp32.onnx']
for name in required:assert (root/'tomori-onnx'/name).stat().st_size>0
print('CONVERSION VERIFIED',flush=True)
