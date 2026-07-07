# DJI Mic 录音整理工具

这是一个用于整理 DJI Mic WAV 录音的桌面工具。它会扫描录音文件夹，按录音时间把 20 分钟左右的 WAV 分段自动还原成多个录音会话，并批量导出为体积更小的 M4A、MP3 或 WAV。

## 功能

- 文件夹扫描，也支持手动添加 WAV 文件
- 按文件时间和音频时长自动分组
- 可调整分组间隔，默认 2 分钟
- 可手动合并会话、从某个文件拆分会话、移除误选文件
- 可将选中文件或选中会话的源 WAV 移到废纸篓/回收站
- 批量导出，每个会话生成一个文件
- 可选择导出成功后自动移除源 WAV
- 默认推荐 M4A/AAC，适合人声录音压缩
- 转换在后台执行，界面保持可用

## 使用

首次运行：

```bash
cd /Users/songquan/Codes/wav-merger
./setup.sh
./run_wav_merger.sh
```

之后运行：

```bash
cd /Users/songquan/Codes/wav-merger
./run_wav_merger.sh
```

## 推荐设置

- 格式：M4A / AAC
- 码率：64 kbps
- 转单声道：开启
- 分组间隔：2 分钟

这些设置适合访谈、会议、口播等 DJI Mic 人声录音，体积会比 WAV 小很多。

## 源码运行依赖

- Python 3 + Tkinter
- Python 包：`imageio-ffmpeg`、`send2trash`

macOS 上 `setup.sh` 会自动检查并安装缺失的 `python-tk@3.11`，并安装运行所需的 Python 包。Release 里的 macOS App 已经内置 ffmpeg。

## 打包

本机打包 macOS App：

```bash
./build_app.sh
```

打包产物会生成在 `release/` 目录。PyInstaller 需要在对应系统上构建对应系统的应用；仓库已包含 GitHub Actions workflow，推送 `v*` tag 时会自动构建 macOS、Windows 和 Linux release 包。

## 致谢

- FFmpeg / imageio-ffmpeg：音频转码
- send2trash：安全删除到废纸篓/回收站
- Python / Tkinter：桌面界面

## 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。

## 问题反馈

如果你发现任何问题或有改进建议，欢迎：
1. 提交 [Issue](https://github.com/songquanxu/wav-merger/issues)
2. 提交 Pull Request
