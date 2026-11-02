"""Compatibility entry point for the standalone Chinese operator scripts."""
from pathlib import Path
import importlib.util
spec=importlib.util.spec_from_file_location('_real_bot_ssh',Path(__file__).resolve().parents[1]/'deploy_cloud.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
connect,upload,run=module.connect,module.upload,module.run
