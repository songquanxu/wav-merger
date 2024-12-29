#!/bin/bash

echo "开始设置 WAV 文件合并工具环境..."

# 检查是否已安装 Python 3
if ! command -v python3 &> /dev/null; then
    echo "未检测到 Python 3，请先安装 Python 3"
    exit 1
fi

# 检查并安装 Homebrew
install_homebrew() {
    if ! command -v brew &> /dev/null; then
        echo "正在安装 Homebrew..."
        # 首先尝试官方源
        if ! /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"; then
            echo "官方源安装失败，尝试使用国内镜像源..."
            # 使用国内镜像源
            /bin/zsh -c "$(curl -fsSL https://gitee.com/cunkai/HomebrewCN/raw/master/Homebrew.sh)"
        fi
        
        # 配置 Homebrew 环境变量
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
        eval "$(/opt/homebrew/bin/brew shellenv)"
        
        # 等待 Homebrew 完全配置
        sleep 2
    fi
}

# 安装 Homebrew
install_homebrew

# 检查并安装 ffmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "正在安装 ffmpeg..."
    brew install ffmpeg
fi

# 设置虚拟环境
ENV_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/wav_merger_env"
if [ ! -d "$ENV_PATH" ]; then
    echo "正在创建虚拟环境..."
    python3 -m venv "$ENV_PATH"
fi

# 激活虚拟环境并安装依赖
echo "正在安装必要的 Python 包..."
source "$ENV_PATH/bin/activate"

# 设置 pip 镜像源（如果需要）
pip3 config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 安装所需的 Python 包
pip3 install pygame mutagen

# 创建启动脚本
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_SCRIPT="$SCRIPT_DIR/run_wav_merger.sh"

cat > "$RUN_SCRIPT" << EOL
#!/bin/bash
source "$ENV_PATH/bin/activate"
python3 "\$(dirname "\$0")/wav_merger.py"
EOL

# 添加执行权限
chmod +x "$RUN_SCRIPT"

# 创建一个简单的README
cat > "$SCRIPT_DIR/README.md" << EOL
# WAV文件合并工具

这是一个用于合并WAV文件的图形界面工具。

## 功能特点
- 支持文件夹导入和单个文件添加
- 支持音频预览和进度控制
- 支持文件顺序调整
- 支持批量文件管理

## 使用方法
1. 双击 \`run_wav_merger.sh\` 启动程序
2. 或在终端中运行 \`./run_wav_merger.sh\`

## 故障排除
如果遇到权限问题，请运行：
\`\`\`bash
chmod +x run_wav_merger.sh
\`\`\`

如果遇到依赖问题，请运行：
\`\`\`bash
./setup.sh
\`\`\`
EOL

echo "设置完成！"
echo "你可以通过以下方式运行程序："
echo "1. 双击 run_wav_merger.sh"
echo "2. 在终端中运行 ./run_wav_merger.sh"
echo ""
echo "如果遇到问题："
echo "1. 确保已经给脚本添加了执行权限：chmod +x run_wav_merger.sh"
echo "2. 如果安装过程中断，可以重新运行 ./setup.sh"
echo "3. 如果遇到网络问题，脚本会自动尝试使用国内镜像源" 