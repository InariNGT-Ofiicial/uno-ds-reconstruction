# 固定资产复现与生成记录

## 两种复现是不同的

**合成复现**：使用同一组通过稿、图形、时间线和匹配的源输入，重放当前成片。

**重新生成图稿**：使用提示词、职责明确的参考图和内置生图工具重新执行一次。工具没有暴露seed和每次调用的底层版本，因此不保证得到相同像素。

本仓库提供前者的代码与工作素材，也提供后者的过程记录；没有把公开提示词包装成一个自动训练模型或统一的命令行生图接口。

## 1. 安装依赖

使用Python 3.10或以上。生产端实际环境记录在 `evidence/environment.json`，它是本机记录，不代表这些版本在所有平台都有同样的轮子或编码结果。

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
```

FFmpeg须可从PATH调用，或显式设置 `FFMPEG_BINARY`。本仓库不捆绑FFmpeg二进制。

## 2. 安装工作素材

```bash
python tools/install_assets.py --download
# 或者先从Release手动下载：
python tools/install_assets.py --archive /path/to/uno-ds-render-assets-v1.zip
```

安装器先核对整个ZIP的SHA256，只接收素材清单声明的 `project/` 文件，再逐文件验哈希。拒绝越界路径和不匹配的现有文件，不会递归删除目录。

素材包包含最终renderer读取的图片和三份身份参考。它不包含原始MV／音轨、不包含按原片生成的86MB导航缓存，也不包含所有退稿的原始返图和历史参考。运行图稿与公开提示词的范围不是同一个集合。

## 3. 准备原片和音轨

来源：[本地项目使用的B站版本](https://www.bilibili.com/video/BV1Y9iZYUE6y/)。源字节哈希见 `project/source-inputs.json`。

```bash
python tools/import_source.py --video /path/to/video.mp4 --audio /path/to/audio.m4s
```

导入器在复制前核对哈希，输出固定的 `inputs/source-video.mp4` 和 `inputs/source-audio.m4a` 文件名。改扩展名不改音轨字节。不同清晰度、编码、裁剪或转录版本要重新对齐，不能跳过校验后仍声称重放了本项目。

## 4. 建立导航缓存并构建计划

```bash
python tools/catalog_source.py
python tools/build_plan.py
python tools/validate_project.py
```

`catalog_source.py`生成全率分析缓存及两级联系表。它不自动批准动作帧。`build_plan.py`使用已经审过的镜头、核心稿、嘴型、显式修订编译当前计划；这一步是重放已有选择，不是对任意新MV的一键语义抽帧。

如果只想检查仓库数据结构而尚未下载素材和原片：

```bash
python tools/validate_project.py --metadata-only
```

## 5. 先验证短片，再渲染全片

```bash
python tools/render.py --label bubble-smoke --start 1628 --end 1647 --encode --checkpoint-frames 1632,1646
python tools/render.py --label whitepaper-smoke --start 2824 --end 2984 --encode --checkpoint-frames 2870,2940,2970
python tools/render.py --label full-rebuild --encode
```

输出在 `project/renders/<label>/`，包含MP4、逐帧采用记录、输入哈希、实际使用的图文事件快照和检查点。label对应目录已经存在时拒绝覆盖。

可选的 `--checkpoints-only`只生成选定画面，用来诊断，不会输出成片。报告中不能把检查点数量冒充完整编码帧数。

## 6. 核对复现结果

原交付为5245帧、24 fps、218.541667秒、1280 × 720。原交付MP4的SHA256记录在README和 `evidence/delivery-manifest.json`。

2026-10-26发布前，已在独立公开目录从源输入重建缓存与时间线，再顺序编码完整5245帧；得到与原交付相同的MP4哈希。这次使用本机已安装的生产依赖和FFmpeg，不等同于已在其他平台重新安装并测试。

固定资产与同一编码环境可以核对输出哈希；换FFmpeg、平台或编码参数后，文件哈希可能改变，仍应检查实际帧数、时间戳、音轨和画面。不是因为图像观感相同，就能声称字节完全相同。

公开脚本相对生产脚本的改动范围和哈希见 `evidence/portable-source-adaptations.json`：目录绑定、依赖导入、FFmpeg发现和导航字体回退。最终公开目录的实际复现结果写入 `evidence/publication-validation.json`。

## 阅读提示词记录

`evidence/generation-ledger.json`逐项指向 `prompts/`，保留原始提示词hash、公开脱敏版本hash、输出hash、参考职责、状态与是否在素材包中。原始返图在本地核过130次，但公开包并不含每一个历史退稿；缺少的历史参考不能被假定为已经提供。

`model_version`和`seed`均为 `not_exposed`。不要从画风、文件名或参数名推断某次返回的隐藏厂家与模型版本。
