# Hermem 常见问题排查

---

## Q1: 启动时报 `ModuleNotFoundError: No module named 'ollama'`

**原因**: 依赖未安装，或使用了系统 Python 而非 Hermes venv。

**解决**:
```bash
# 确认使用正确的 Python
~/.hermes/hermes-agent/venv/bin/python -c "import ollama; print('OK')"

# 如果报错，执行安装
~/.hermes/hermes-agent/venv/bin/python -m pip install ollama numpy pydantic
```

---

## Q2: 启动时报 `ModuleNotFoundError: No module named 'impl'`

**原因**: `impl` 软链接未创建或失效。

**解决**:
```bash
# 检查软链接状态
ls -la ~/.hermes/plugins/memory/hermem/impl

# 如果不存在或失效，重新创建
ln -sf ~/hermem/phase3/impl ~/.hermes/plugins/memory/hermem/impl

# 验证
ls -la ~/.hermes/plugins/memory/hermem/impl  # 应显示 -> ~/hermem/phase3/impl
```

---

## Q3: `ollama pull bge-m3:latest` 下载失败或超慢

**原因**: 网络问题或 ollama 模型服务器访问受限。

**解决**:
```bash
# 方法 1：手动拉取
ollama pull bge-m3:latest

# 方法 2：使用代理（如果你有代理服务器）
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
ollama pull bge-m3:latest

# 方法 3：确认 ollama 已启动
ollama serve  # 在另一个终端窗口运行
```

---

## Q4: `batch_compute_embeddings.py` 运行报错 `no such table: chunks`

**原因**: 数据库未初始化。

**解决**:
```bash
# 先运行数据库初始化
python3 ~/hermem/phase3/impl/db_init.py

# 再运行向量库初始化
python3 ~/hermem/phase3/scripts/batch_compute_embeddings.py
```

---

## Q5: Hermem 启动正常，但发送消息后没有 `[自动回忆]` 注入

**原因**: 当前对话轮次未达到触发频率（每 3 轮一次）。

**解决**:
- 正常现象。**V6 主动检索**由 4-signal trigger 决定(`medium_accumulated` / `anchor` / `temporal` / `intent`),`frequency_fallback`(每 3 轮)是兜底;V5 已升级为 V6,见 `README.md` §V6 `_v6_should_trigger()`
- 继续对话,触发条件命中或每 3 轮兜底时观察

**另一个原因**: 没有相似度达标的历史 chunk。

**排查**:
```bash
# 运行端到端测试，看向量检索是否有结果
python3 ~/hermem/phase3/scripts/test_v5_e2e.py
```

---

## Q6: Hermem 启动时报 `KeyError: 'vectorstore'`

**原因**: 向量库文件不存在或路径配置错误。

**解决**:
```bash
# 检查向量库文件
ls -la ~/.hermes/memory/hermem_embeddings.npy
ls -la ~/.hermes/memory/hermem_embeddings.meta.json

# 如果缺失，重新初始化
python3 ~/hermem/phase3/scripts/fix_drift_and_fill_embeddings.py
```

---

## Q7: 启动时报权限错误（`Permission denied`）

**原因**: 文件或目录权限不足。

**解决**:
```bash
# 修复权限
chmod 755 ~/.hermes/plugins/memory/hermem/
chmod 755 ~/.hermes/memory/
chmod 644 ~/.hermes/memory/*.npy 2>/dev/null || true
```

---

## Q8: Hermes 日志显示 `HermemMemoryProvider init failed`

**原因**: Ollama 未启动或 bge-m3 模型未下载。

**解决**:
```bash
# 确认 Ollama 状态
ollama list
# 应该看到 bge-m3:latest

# 如果没有，启动 ollama 并拉取模型
ollama serve
ollama pull bge-m3:latest

# 再重启 Hermes
hermes restart
```

---

## Q9: `hermes restart` 找不到命令

**原因**: `hermes` 命令未在 PATH 中。

**解决**:
```bash
# 方法 1：使用完整路径
~/.hermes/hermes-agent/venv/bin/hermes restart

# 方法 2：添加到 PATH（添加到 ~/.zshrc）
echo 'export PATH="$HOME/.hermes/hermes-agent/venv/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# 方法 3：用 Python 直接启动 gateway
python3 ~/.hermes/hermes-agent/gateway/run.py
```

---

## Q10: 软链接已存在但仍然报 `No module named 'impl'`

**原因**: macOS Finder 创建的 `.command` 文件或类似问题；或 Python import 路径问题。

**解决**:
```bash
# 确认软链接真实存在且可读
readlink ~/.hermes/plugins/memory/hermem/impl
ls -la ~/.hermes/plugins/memory/hermem/impl  # 确认可访问

# 在 Hermem 目录测试 import
cd ~/hermem/phase3 && python3 -c "from impl import database; print('OK')"
```

---

## 诊断命令汇总

```bash
# 1. 检查 Ollama 健康
curl http://localhost:11434/api/tags

# 2. 检查 Hermem 向量库状态
python3 ~/hermem/phase3/scripts/test_v5_e2e.py

# 3. 检查数据库
python3 -c "from pathlib import Path; from impl.database import get_db; db = get_db(); print(db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0])"

# 4. 检查 Hermem 日志
grep -i hermem ~/.hermes/logs/hermes.log | tail -20
```

---

## 仍然无法解决？

1. 收集日志：`~/.hermes/logs/hermes.log` 中与 Hermem 相关的行
2. 运行诊断命令并记录输出
3. 到 https://github.com/oxdh9019/hermem/issues 开 Issue，附上：
   - 错误日志
   - `python3 --version`
   - `~/.hermes/hermes-agent/venv/bin/python --version`
   - `ollama list` 输出