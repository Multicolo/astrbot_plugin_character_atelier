# 补丁说明（v2.5.0）

在官方 v2.4.0 上的改动，共三类：提示词内置化、参考图开关、两个静默故障的修复。

## 1. 提示词内置化（本次核心）

- 内置 `DEFAULT_LLM_GUIDE` 换成定稿版本（831 字，不含空白）。
- 新增配置 `llm_guide_source`：`builtin`（默认）/ `custom`。
  - `builtin`：**永远用内置版本**。推仓库 → AstrBot 点更新 → 提示词跟着变，不用碰面板。
  - `custom`：用 `llm_guide_text` 里你写的那份；留空则回退内置并打一条 warning。
- `llm_guide_text` 默认值改为空。旧配置里存着的历史文本在 `builtin` 下会被忽略——这是有意的，否则升级后旧文本又会盖住新提示词。

> 想保留自己改的口吻：把 `llm_guide_source` 设为 `custom`，再把文本贴进 `llm_guide_text`。

## 2. 参考图开关

- `reference_mode`：`always`（默认，等于官方行为）/ `on_demand` / `off`。
- `on_demand` 下，人物图需要工具传 `keep_character=true` 或命令加 `--角色`；纯风景图不传，插件不附参考图、不加角色描述。
- 主动画图、单次日程、定时日程三处 `_generate` 调用写死 `keep_character=True`，避免换成 `on_demand` 后变成空镜。

## 3. 两个静默故障

- 角色描述原本只在「有参考图」时才拼进 prompt（`if character_prompt and refs`），没图就整段丢失；现在按 `use_character` 判断。
- 主动画图循环原本以「存在参考图」为前提，参考图一不带就每 300 秒空转、永不触发、不写日志；改为按「角色名 / 描述 / 三视图文件」任一存在判断。顺带避免每次循环都 base64 编码三张图。

## 升级后怎么验

```text
1. 覆盖插件目录，重载插件（或重启）。
2. /生图状态 正常返回 → 没编译错误。
3. 直接发「画一张海边的黄昏」→ 应出纯风景，画面里没有人。
4. 发「画她坐在窗台上看海」→ 应出角色，长相对得上三视图。
5. 面板里 llm_guide_source 应为 builtin；把 llm_guide_text 清空不影响内置版本。
```

已做静态验证：`py_compile` 通过、`_conf_schema.json` 可解析、内置提示词 831 字且占位符齐全。运行时未在真实 AstrBot 上验证。
