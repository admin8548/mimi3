# OpenClaw WebUI 中文操作手册

本文说明 OpenClaw 页面中哪些功能适合日常使用，以及受控写入的安全操作顺序。

## 功能分区

### 1. 只读诊断

优先使用这些区域确认当前状态，不会执行写入：

- `OpenClaw 诊断`
- `能力矩阵`
- `智能体运行观测`
- `会话概览`
- `定时任务概览`
- `浏览器概览`
- `配置 / 智能体文件只读查看`

这些接口只做读取、摘要和脱敏展示。浏览器快照需要显式点击才会加载。

### 2. 写入前预览

所有高风险写入都应先生成差异预览：

1. 选择目标：`配置` 或 `智能体文件`。
2. 粘贴拟写入内容。
3. 点击 `生成差异预览`。
4. 检查 backup ID、目标、哈希、baseHash 或文件名是否匹配。
5. 只有当前内容与预览一致时，令牌按钮才会启用。

注意：系统只保存 metadata 和哈希，不保存 raw 或文件明文。

### 3. 受控写入

默认情况下，写入由环境变量关闭：

```bash
MIMO_OPENCLAW_MUTATIONS_ENABLED=false
```

只有显式设置为 `true` 后，才允许生成 confirmation token 并执行写入。

受控写入必须满足：

- 已生成差异预览
- backup metadata 匹配
- 参数哈希匹配
- confirmation token 未过期、未使用
- 用户完成最终二次确认

当前允许的受控写入：

- `config.patch`
- `agents.files.set`
- `sessions.compact`
- `cron.run`
- `browser.navigate`

不要开放 `config.apply` / `config.set`，除非另有完整安全设计。

### 4. 审计与恢复

审计区用于回看：

- preview
- confirmation_token
- execute_start
- execute_done
- execute_error
- verify_error

恢复配置时：

1. 找到外部保存的旧 raw 明文。
2. 将旧 raw 粘贴到拟写入内容。
3. 重新生成配置差异预览，获取当前 `baseHash`。
4. 生成配置补丁令牌。
5. 二次确认并执行 `config.patch`。

恢复智能体文件时：

1. 找到外部保存的旧文件明文。
2. 填写 agent id 和文件名。
3. 粘贴旧文件内容。
4. 重新生成差异预览。
5. 生成令牌并执行 `agents.files.set`。

## 推荐日常流程

- 日常排障：先看只读诊断。
- 不确定问题：查看审计记录和 backup metadata。
- 需要写入：严格按 `预览 → 令牌 → 二次确认 → 执行 → 审计` 的顺序。
- 写入后：刷新只读概览确认结果。
