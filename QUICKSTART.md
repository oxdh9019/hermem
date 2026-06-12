# Hermem 公开试用版 — 快速开始指南

> **目标**: 5 分钟内让 Hermem 作为 Memory Provider 在 Hermes Agent 中运行。
>
> **系统**: macOS / Linux，已安装 Hermes Agent（`~/.hermes/hermes-agent/`）

---

## 前置要求

- Hermes Agent 已安装（`~/.hermes/hermes-agent/` 存在）
- Python 3.10+
- Ollama 已启动（`ollama serve`），bge-m3 模型已下载（`ollama pull bge-m3:latest`）

---

## 安装步骤

### 第一步：进入插件目录

```bash
cd ~/.hermes/plugins/memory/hermem
```

### 第二步：安装依赖

```bash
# 使用 Hermes 已有虚拟环境安装
~/.hermes/hermes-agent/venv/bin/python -m pip install --upgrade pip
~/.hermes/hermes-agent/venv/bin/python -m pip install -r requirements.txt
```

### 第三步：克隆 Hermem 实现仓库

```bash
git clone https://github.com/oxdh9019/hermem.git ~/hermem
```

> 如果你已经有 hermem 仓库在本地，确保 `~/hermem/phase3/impl/` 存在即可。

### 第四步：创建软链接

```bash
ln -s ~/hermem/phase3/impl ~/.hermes/plugins/memory/hermem/impl
```

这一步让插件能找到 Hermem 的实现代码。**软链接路径是相对 `~/.hermes/plugins/memory/hermem/` 的**。

验证：
```bash
ls -la impl  # 应该显示 -> ~/hermem/phase3/impl
```

### 第五步：初始化向量库

```bash
python3 ~/hermem/phase3/scripts/batch_compute_embeddings.py
```

预计 5-10 分钟（**1700+ 个 chunk** 截至 2026-06-10,每个约 50-80ms;早期估算 1637 已过时,实际值会因增量 session 持续增长）。运行完成后再继续。

### 第六步：配置 Hermes 使用 Hermem

编辑 `~/.hermes/config.yaml`，在 `memory.provider` 或 `plugins.memory` 部分添加：

```yaml
memory:
  provider: hermem
```

具体格式取决于你的 Hermes 配置版本。常见写法：

```yaml
# 方式 A（memory.provider）
memory:
  provider: hermem

# 方式 B（plugins）
plugins:
  memory:
    provider: hermem
```

如果不确定，看现有的 `config.yaml` 中 `memory` 或 `plugins` 段落，照着格式添加即可。

### 第七步：重启 Hermes

```bash
hermes restart
# 或
hermes gateway
```

观察启动日志，确认出现类似以下内容：

```
HermemMemoryProvider initialized (session=xxx)
```

---

## 验证安装

### 方法一：发送测试消息

1. 重启后向 Hermes 发送一条消息，观察正常回复
2. 发送一条明显涉及历史的话题，例如：

   ```
   帮我看看上次关于 Hermem 架构的讨论
   ```

3. 观察是否出现 `[自动回忆]` 注入信息（在 Agent 回复中出现）

### 方法二：运行健康检查

```bash
python3 ~/hermem/phase3/scripts/test_v5_e2e.py
```

应该显示 7/8 测试通过（格式验证、向量检索、防重复注入等）。

### 方法三：检查 Hermes 日志

```bash
tail -f ~/.hermes/logs/hermes.log
```

搜索 `Hermem`，观察插件加载状态和检索日志。

---

## 文件结构

安装完成后，插件目录结构如下：

```
~/.hermes/plugins/memory/hermem/
├── __init__.py          # HermemMemoryProvider（插件入口）
├── cli.py               # CLI 工具（hermem search / add / stats）
├── impl -> ~/hermem/phase3/impl/   # 软链接到 Hermem 实现
├── requirements.txt     # Python 依赖
├── install.sh          # 安装脚本
├── QUICKSTART.md       # 本文档
└── TROUBLESHOOTING.md  # 常见问题
```

---

## 更新 Hermem

```bash
cd ~/hermem
git pull
# 重新初始化向量库（如有新增 chunk）
python3 ~/hermem/phase3/scripts/fix_drift_and_fill_embeddings.py
```

---

## 卸载

```bash
# 移除软链接
rm ~/.hermes/plugins/memory/hermem/impl

# 从 config.yaml 中移除 hermem 配置
# 重启 Hermes
hermes restart
```

---

## 反馈渠道

- GitHub Issues: https://github.com/oxdh9019/hermem/issues
- 启动遇到问题先查 `TROUBLESHOOTING.md`，再开 Issue 并附上错误日志