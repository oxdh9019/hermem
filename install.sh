#!/bin/bash
# Hermem Plugin Installation Script
# 方向 C — 从 hermem 仓库自动配置 Hermes 插件目录
#
# 用法:
#   ./install.sh                    # 默认（检测 + 引导）
#   ./install.sh --setup-plugin     # 自动配置插件目录
#   ./install.sh --init-vectors     # 只初始化向量库
#
# 支持 macOS/Linux，检测 ~/.hermes/ 环境

set -e

HERMES_VENV="${HOME}/.hermes/hermes-agent/venv"
PLUGIN_DIR="${HOME}/.hermes/plugins/memory/hermem"
HERMEM_DIR="${HOME}/hermem"
HERMEM_REPO="${HOME}/hermem"  # 用户克隆到的目录

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
require() { if [ ! -f "$1" ]; then error "$2"; exit 1; fi }

# ── Helper: 检测 Hermes venv ─────────────────────────────────────────────────
find_hermes_python() {
    if [ -f "${HERMES_VENV}/bin/python" ]; then
        echo "${HERMES_VENV}/bin/python"
    elif [ -f "${HOME}/.hermes/venv/bin/python" ]; then
        echo "${HOME}/.hermes/venv/bin/python"
    else
        echo ""
    fi
}

# ── Step 1: 检测环境 ─────────────────────────────────────────────────────────
echo ""
echo "==> 环境检测"
echo ""

HERMES_PYTHON=$(find_hermes_python)
if [ -z "$HERMES_PYTHON" ]; then
    error "未找到 Hermes 虚拟环境"
    echo "  请先安装 Hermes Agent: https://github.com/NousResearch/hermes-agent"
    exit 1
fi
info "Hermes Python: $HERMES_PYTHON"

HERMEM_CLONE_DIR=""
for d in "$HERMEM_REPO" "$HOME/hermem" "/opt/hermem"; do
    if [ -d "$d/phase3/impl" ]; then
        HERMEM_CLONE_DIR="$d"
        break
    fi
done

if [ -z "$HERMEM_CLONE_DIR" ]; then
    error "未找到 Hermem 实现目录 (~/.hermem/phase3/impl 不存在)"
    echo ""
    echo "  请先克隆 Hermem 仓库:"
    echo "  git clone https://github.com/oxdh9019/hermem.git $HOME/hermem"
    echo ""
    error "或指定已有目录: export HERMEM_DIR=/path/to/hermem"
    exit 1
fi
info "Hermem 目录: $HERMEM_CLONE_DIR"

# ── Step 2: 安装依赖 ──────────────────────────────────────────────────────────
echo ""
echo "==> 安装依赖..."

VENV_PIP="$HERMES_PYTHON -m pip"
$VENV_PIP install --upgrade pip -q 2>/dev/null || true

REQUIREMENTS="${HERMEM_CLONE_DIR}/requirements.txt"
if [ -f "$REQUIREMENTS" ]; then
    $VENV_PIP install -r "$REQUIREMENTS" -q 2>/dev/null || true
    info "依赖安装完成"
else
    warn "requirements.txt 未找到，跳过"
fi

# ── Step 3: 初始化向量库 ──────────────────────────────────────────────────────
echo ""
echo "==> 检查向量库..."

NPY_FILE="${HOME}/.hermes/memory/hermem_embeddings.npy"
if [ ! -f "$NPY_FILE" ]; then
    warn "向量库文件不存在"
    echo "  运行: python3 ${HERMEM_CLONE_DIR}/phase3/scripts/batch_compute_embeddings.py"
    echo "  （预计 5-10 分钟）"
elif [ ! -f "${HERMEM_CLONE_DIR}/phase3/hermem_embeddings.meta.json" ]; then
    warn "向量库 meta 文件缺失，可能需要重新初始化"
else
    info "向量库已存在: $NPY_FILE"
fi

# ── Step 4: 配置插件目录 ──────────────────────────────────────────────────────
echo ""
echo "==> 配置 Hermes 插件目录..."

# 创建插件目录（如果不存在）
mkdir -p "$PLUGIN_DIR"

# 检查 impl 软链接
IMPL_LINK="$PLUGIN_DIR/impl"
if [ -L "$IMPL_LINK" ]; then
    TARGET=$(readlink "$IMPL_LINK")
    if [ "$TARGET" = "${HERMEM_CLONE_DIR}/phase3/impl" ]; then
        info "impl 软链接已正确配置: -> $TARGET"
    else
        warn "impl 软链接指向错误目标: $TARGET"
        echo "  重新创建..."
        rm -f "$IMPL_LINK"
        ln -sf "${HERMEM_CLONE_DIR}/phase3/impl" "$IMPL_LINK"
        info "impl 软链接已更新: -> $(readlink $IMPL_LINK)"
    fi
elif [ -d "$IMPL_LINK" ]; then
    info "impl 目录已存在（非软链接）"
else
    ln -sf "${HERMEM_CLONE_DIR}/phase3/impl" "$IMPL_LINK"
    info "impl 软链接已创建: -> $(readlink $IMPL_LINK)"
fi

# 复制插件入口文件
TEMPLATE_INIT="${HERMEM_CLONE_DIR}/templates/__init__.py"
CURRENT_INIT="${PLUGIN_DIR}/__init__.py"

if [ -f "$TEMPLATE_INIT" ]; then
    if [ -f "$CURRENT_INIT" ]; then
        # 简单比较：如果内容不同则备份并替换
        if ! diff -q "$TEMPLATE_INIT" "$CURRENT_INIT" > /dev/null 2>&1; then
            cp "$CURRENT_INIT" "${CURRENT_INIT}.bak"
            info "已备份现有 __init__.py -> __init__.py.bak"
        fi
        cp "$TEMPLATE_INIT" "$CURRENT_INIT"
        info "插件入口 __init__.py 已更新"
    else
        cp "$TEMPLATE_INIT" "$CURRENT_INIT"
        info "插件入口 __init__.py 已复制"
    fi
else
    warn "templates/__init__.py 未找到，跳过入口文件更新"
fi

# 创建数据目录
mkdir -p "${HOME}/.hermes/memory/l0_raw"

# ── 完成 ──────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
info  "安装完成！"
echo "============================================================"
echo ""
echo "下一步操作:"
echo ""
echo "  1. 配置 Hermes 使用 Hermem:"
echo "     编辑 ~/.hermes/config.yaml，添加:"
echo "     memory:"
echo "       provider: hermem"
echo ""
echo "  2. 重启 Hermes:"
echo "     hermes restart"
echo ""
echo "  3. 如需初始化向量库（首次）:"
echo "     python3 ${HERMEM_CLONE_DIR}/phase3/scripts/batch_compute_embeddings.py"
echo ""
echo "  详细指南: ${HERMEM_CLONE_DIR}/QUICKSTART.md"
echo "  问题排查: ${HERMEM_CLONE_DIR}/TROUBLESHOOTING.md"
echo ""