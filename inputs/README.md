# 本地原始输入

原片和音轨不提交到Git。请按 `project/source-inputs.json` 取得匹配文件，然后运行：

```
python tools/import_source.py --video /path/to/video.mp4 --audio /path/to/audio.m4s
```

导入后本目录中的两个固定文件名是 `source-video.mp4` 和 `source-audio.m4a`。仅复制字节，不转换编码。
