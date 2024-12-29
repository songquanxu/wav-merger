# WAV音频文件合并工具 (WAV Audio Merger)

一个简单易用的 WAV 音频文件合并工具，提供图形界面操作。

## 应用场景

本工具特别适合处理需要合并多个 WAV 音频文件的场景，例如：

- **DJI 麦克风录音文件合并**：DJI 麦克风在长时间录音时会每隔约30分钟自动分段生成一个 WAV 文件，使用本工具可以方便地将这些分段文件合并成一个完整的音频文件，并可选择导出为 MP3 格式以节省存储空间。

## 三步快速使用（推荐）

1. 下载项目：
打开终端（以下以macOS为例，windows请使用powershell或cmd命令提示符），执行以下命令：
```bash
git clone https://github.com/songquanxu/wav-merger.git
cd wav-merger
```

2. 运行安装脚本（自动安装所需环境）：
```bash
chmod +x setup.sh
./setup.sh
```

3. 运行程序：
```bash
chmod +x run_wav_merger.sh
./run_wav_merger.sh
```

就是这么简单！程序会自动安装所需的所有依赖，包括 Python、FFmpeg 等。

## 使用说明

1. 添加文件
   - 点击"添加WAV文件"选择一个或多个WAV文件
   - 支持多选文件

2. 管理文件
   - 选择文件后可以上移/下移调整顺序
   - 可以移除选中的文件
   - 可以清空整个列表

3. 预览音频
   - 选中文件后点击"预览"按钮
   - 使用进度条控制播放位置
   - 再次点击按钮停止预览

4. 合并文件
   - 添加完所有文件后
   - 选择输出格式（WAV/MP3）
   - 如果选择MP3可以设置比特率
   - 点击"合并文件"
   - 选择保存位置即可

## 功能特点

- 图形用户界面，操作简单直观
- 支持多个 WAV 文件合并
- 支持音频预览和进度控制
- 支持导出为 WAV 或 MP3 格式
- 支持自定义 MP3 比特率
- 显示详细的音频文件属性
- 支持文件拖拽排序
- 跨平台支持 (Windows/macOS/Linux)

## 手动配置方式（适合开发者）

如果你希望手动配置环境，可以参考以下步骤：

### 系统要求

- Python 3.13 或更高版本
- FFmpeg

### 依赖安装

1. 安装 Python 依赖：
```bash
python3 -m pip install pygame mutagen
```

2. 安装 FFmpeg：
   - macOS: `brew install ffmpeg`
   - Windows: 从 [FFmpeg官网](https://ffmpeg.org/download.html) 下载
   - Linux: `sudo apt-get install ffmpeg` (Ubuntu/Debian)

如果没有安装 homebrew：

1. 使用国内镜像快速安装 homebrew：
```bash
/bin/zsh -c "$(curl -fsSL https://gitee.com/cunkai/HomebrewCN/raw/master/Homebrew.sh)"
```

2. 配置 homebrew 环境变量：
```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

### 命令行使用方式（原生的ffmpeg功能，非本项目功能）

如果你熟悉命令行，也可以直接使用 FFmpeg 命令来合并文件：

1. **生成文件列表**：
```bash
for f in *.WAV; do echo "file '$PWD/$f'" >> filelist.txt; done
```

2. **合并文件**：
```bash
ffmpeg -f concat -safe 0 -i filelist.txt -c copy output.wav
```

3. **查看音频信息**：
```bash
ffmpeg -i output.wav -hide_banner
```

4. **压缩音频**：
```bash
ffmpeg -i output.wav -b:a 32k compressed_output.wav
```

## 开发说明

本程序是基于 FFmpeg 开发的图形界面工具，在 Cursor AI 的辅助下完成开发。

### 致谢

特别感谢以下开源项目：

- **FFmpeg** (https://ffmpeg.org/) - 本项目的核心功能基于 FFmpeg 实现
  - FFmpeg 是一个领先的多媒体框架，能够解码、编码、转码、混流、解复用等
  - 基于 LGPL/GPL 许可证开源
  - 项目主页：https://ffmpeg.org/
  - 源代码：https://github.com/FFmpeg/FFmpeg

### 其他使用的开源组件

- Python 3.13+ (https://www.python.org/)
- tkinter - Python 标准 GUI 库
- pygame 2.6.1+ (https://www.pygame.org/)
- mutagen (https://mutagen.readthedocs.io/)

## 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。

根据 FFmpeg 的许可要求，特此说明：
1. 本项目使用了 FFmpeg 的功能
2. FFmpeg 采用 LGPL/GPL 许可证
3. FFmpeg 的源代码可在 https://github.com/FFmpeg/FFmpeg 获取

## 问题反馈

如果你发现任何问题或有改进建议，欢迎：
1. 提交 [Issue](https://github.com/songquanxu/wav-merger/issues)
2. 提交 Pull Request
