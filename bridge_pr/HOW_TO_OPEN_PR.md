# 桥层 PR 操作指南(1 分钟)

## 准备材料(已完成)

- `bridge_pr/hermem_v6_bridge_sprint_2_3_4.patch`(21KB,3 commit 合并 patch)
- `bridge_pr/PR_DESCRIPTION.md`(3.5KB,完整 PR 描述)

## 步骤(浏览器操作)

### 1. Fork 仓库
打开 https://github.com/NousResearch/hermes-agent
点击右上角 **Fork** 按钮
→ 你的 fork: https://github.com/oxdh9019/hermes-agent(自动创建)

### 2. 在 fork 上创建分支 + 应用 patch
两种方式任选:

**方式 A(浏览器 GitHub UI 改文件)**:不推荐(216 行改动)

**方式 B(本地 git 命令,推荐)**:
```bash
cd /tmp
git clone https://github.com/oxdh9019/hermes-agent.git
cd hermes-agent
git checkout -b hermem-v6-sprint-2-3-4
git apply /Users/oliver/.hermes/projects/hermem/bridge_pr/hermem_v6_bridge_sprint_2_3_4.patch
git add plugins/memory/hermem/__init__.py
git commit -m "feat(hermem-bridge): V6 Sprint 2/3/4 cumulative — search_predictive + explain_chunk + reflect + auto-normalize"
git push origin hermem-v6-sprint-2-3-4
```

### 3. 在 GitHub 开 PR
- 打开 https://github.com/oxdh9019/hermes-agent/tree/hermem-v6-sprint-2-3-4
- 顶部会有 "Compare & pull request" 按钮 → 点击
- **base**: NousResearch/hermes-agent `main`
- **compare**: oxdh9019/hermes-agent `hermem-v6-sprint-2-3-4`
- **title**: `feat(hermem-bridge): V6 Sprint 2/3/4 cumulative — 4 new tools + auto-normalize`
- **description**: 复制 `bridge_pr/PR_DESCRIPTION.md` 全文
- 点击 **Create pull request**

### 4. 提交(可选)
PR 提交后,把 PR URL 发给我,我可以帮你 review diff 完整性 / 跟踪 review 状态。

## 时间预估

- Fork: 10 秒
- 克隆 + checkout + apply patch + push: 1 分钟
- 开 PR: 30 秒
- **总计 < 2 分钟**

## 如果你想要全自动(我来做)

需要你提供 1 个 GitHub Personal Access Token(PAT):
- 打开 https://github.com/settings/tokens
- Generate new token (classic)
- Scopes: `repo` + `workflow`
- 把 token 给我(我会用环境变量 `GITHUB_TOKEN`,不写入任何文件)

我能用 curl 完全自动:
- 创建 fork(用 GitHub API POST /repos/.../forks)
- 推送 patch 到 fork 分支
- 创建 PR(POST /repos/.../pulls)

**token 用完即弃,不保存**。但需要你主动提供,我不能凭空获取。
