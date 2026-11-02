# 微信虚拟麦克风

`virtual_mic.py` 管理独立音频输入与播放；`voice_sender.py` 衔接准备好的音频和微信录音UI；`verify_audio.py` 提供本地音频验证。UI固定坐标和录音控件配置在 `config/examples/recording-controls.json`，需按实际桌面校准。

依赖PulseAudio、FFmpeg、X11和已识别麦克风的微信客户端。本模块不附真实录音、试音、截图或微信状态。所有会话切换和录音发送应服从统一UI队列及锁，不能另开并行点击程序。
