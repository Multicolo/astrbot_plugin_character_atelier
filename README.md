<div align="center">
  <img src="logo.png" width="128" alt="角色画室插件图标">
  <h1>角色画室 · 多模型生图 AstrBot 插件</h1>
  <p>角色三视图 / 整图自动切分 / 主动画图，千问、OpenAI 兼容、Gemini 混用</p>
  <p>
    <img alt="AstrBot" src="https://img.shields.io/badge/AstrBot-4.x-ff69b4">
    <img alt="License" src="https://img.shields.io/badge/license-MIT-blue">
    <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776ab">
  </p>
</div>

一个 AstrBot 生图插件：**阿里云百炼千问、OpenAI 兼容接口、Google Gemini 可以混用**。

> 中文搜索关键词：角色画室、多模型生图、角色三视图、AI 生图、千问生图、Gemini 生图、AstrBot 插件。

它同时提供：

- LLM 工具：bot 人格自己判断什么时候该画图，并直接调用；
- 角色三视图视图：上传正/侧/背三视图，固定角色长相与服装，之后每次生图自动带参考图；
- 主动画图：在设定时间段内，让 bot 自己找场景画一张并主动发到聊天；
- `/画` 指令：不经过模型，直接生成；
- 每日日程插画：按 `virtual_life` 的当天日程生成一张图，定时推送；
- 多服务商回退：主服务商失败时自动换下一个；
- 完整的提示词注入：让 LLM 知道有哪些服务商、什么时候调用、参数怎么写。

> 插件不内置任何 API Key。所有密钥都由使用者自己填写，不会上传到第三方。

---

## 1. 支持的服务商

`providers_json` 是一个 JSON 数组，每个对象代表一个服务商。`type` 决定走哪种协议：

| type | 适用服务 | 默认地址 | 默认模型 |
| --- | --- | --- | --- |
| `dashscope` | 阿里云百炼 / 千问 | `https://dashscope.aliyuncs.com/api/v1` | `qwen-image-2.1-turbo` |
| `openai` | OpenAI 及所有 OpenAI 兼容图片接口 | `https://api.openai.com/v1` | `gpt-image-1` |
| `gemini` | Google Gemini 图片模型 | `https://generativelanguage.googleapis.com/v1beta` | `gemini-2.5-flash-image` |

常见可接入的例子：

- 阿里云百炼：`qwen-image-2.1-turbo`、`qwen-image-2.1-pro`、`qwen-image-3.0`、`qwen-image-3.0-pro`
- OpenAI：`gpt-image-1`、`dall-e-3`
- Gemini：`gemini-2.5-flash-image`、`gemini-3-pro-image-preview`（以账号实际开通为准）
- 硅基流动 / 第三方聚合：通常用 `openai` 类型，填对方的 `api_base` 和模型名
- 自建 ComfyUI / SD WebUI 的 OpenAI 兼容网关：同样用 `openai` 类型

---

## 2. 安装

1. 把整个 `astrbot_plugin_character_atelier` 目录放到 AstrBot 的插件目录：

   ```text
   <AstrBot 数据目录>/plugins/astrbot_plugin_character_atelier/
   ```

   Docker 部署常见路径是：

   ```text
   /AstrBot/data/plugins/astrbot_plugin_character_atelier/
   ```

2. 安装依赖（AstrBot 一般会自动装，也可以手动）：

   ```bash
   pip install -r requirements.txt
   ```

3. 重启 AstrBot，或在面板里重载插件。

4. 打开插件配置，至少填一个服务商；保存后重启/重载生效。

---

## 3. 快速配置

### 3.1 只用千问（阿里云百炼）

```json
[
  {
    "name": "qwen",
    "type": "dashscope",
    "api_key": "sk-你的百炼Key",
    "model": "qwen-image-2.1-turbo"
  }
]
```

`api_key` 也可以留空：插件会尝试读取 AstrBot 模型配置里已有的 DashScope key（例如 embedding 提供商的 `embedding_api_key`）。仍然建议显式填写，权限和模型开通情况更清楚。

### 3.2 只用 OpenAI

```json
[
  {
    "name": "openai",
    "type": "openai",
    "api_key": "sk-你的OpenAIKey",
    "model": "gpt-image-1",
    "size_map": {
      "1024*1024": "1024x1024",
      "1024*768": "1536x1024",
      "768*1024": "1024x1536"
    }
  }
]
```

### 3.3 只用 Gemini

```json
[
  {
    "name": "gemini",
    "type": "gemini",
    "api_key": "AIza...你的Key",
    "model": "gemini-2.5-flash-image"
  }
]
```

### 3.4 多家混用 + 自动回退

```json
[
  {
    "name": "qwen",
    "type": "dashscope",
    "api_key": "sk-你的百炼Key",
    "model": "qwen-image-2.1-turbo"
  },
  {
    "name": "openai",
    "type": "openai",
    "api_key": "sk-你的OpenAIKey",
    "model": "gpt-image-1",
    "size_map": {
      "1024*1024": "1024x1024",
      "1024*768": "1536x1024",
      "768*1024": "1024x1536"
    }
  },
  {
    "name": "gemini",
    "type": "gemini",
    "api_key": "AIza...你的Key",
    "model": "gemini-2.5-flash-image"
  }
]
```

再设置：

- `active_provider`：默认用哪家，例如 `qwen`；留空用列表第一个；
- `provider_fallback`：`true` 时按列表顺序自动换下家；
- `schedule_provider`：日程图单独指定服务商，留空用默认。

### 3.5 用环境变量藏 Key

```json
[
  {
    "name": "qwen",
    "type": "dashscope",
    "api_key_env": "DASHSCOPE_API_KEY",
    "model": "qwen-image-2.1-turbo"
  }
]
```

支持 `api_key_env` 的服务商不会把 Key 写进配置文件。容器部署时用 `-e DASHSCOPE_API_KEY=...` 或 `env_file` 注入。

---

## 4. `providers_json` 字段说明

每个服务商对象支持以下字段，除 `name` / `type` 外都可省略：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | 服务商唯一名称，用于 `active_provider`、`--provider`、`schedule_provider` |
| `type` | string | `dashscope`、`openai`、`gemini`（也接受 `qwen`/`bailian`/`openai-compatible`/`google` 等别名） |
| `api_key` | string | API Key；和 `api_key_env` 二选一 |
| `api_key_env` | string | 从环境变量读取 Key，例如 `DASHSCOPE_API_KEY` |
| `api_base` | string | 自定义接口地址；留空用类型默认值 |
| `model` | string | 模型名；留空用类型默认模型 |
| `enabled` | bool | 是否启用，默认 `true`；可临时停用某家 |
| `timeout` | number | 该服务商的整体超时（秒），覆盖全局 `timeout` |
| `poll_interval` | number | 异步任务轮询间隔（秒），覆盖全局值 |
| `request_timeout` | number | 单次 HTTP 请求超时（秒） |
| `headers` | object | 额外请求头，例如第三方网关的认证头 |
| `extra_body` | object | 合并进请求体的额外参数，例如 `quality`、`style`、`background` |
| `size_map` | object | 尺寸映射，键用 `宽*高`，值用服务商要求的格式 |
| `response_format` | string | `openai` 类型可填 `url` 或 `b64_json` |
| `send_negative_prompt` | bool | `openai` 类型是否发送 `negative_prompt`，默认 `false`（很多兼容接口不认） |
| `response_modalities` | array | `gemini` 类型返回模态，默认 `["TEXT", "IMAGE"]` |
| `send_aspect_ratio` | bool | `gemini` 类型是否发送 `imageConfig.aspectRatio`，默认 `true` |

### 尺寸映射示例

插件内部统一用 `1024*1024` 这种写法。不同服务商要求不同：

```json
{
  "name": "openai",
  "type": "openai",
  "size_map": {
    "1024*1024": "1024x1024",
    "1024*768": "1536x1024",
    "768*1024": "1024x1536"
  }
}
```

没有配置映射时，`openai` 类型会自动把 `*` 换成 `x`；`dashscope` 保持 `*`；`gemini` 会按宽高比选择 `1:1` / `3:4` / `4:3` / `9:16` / `16:9`。

### 第三方 OpenAI 兼容网关示例

```json
[
  {
    "name": "siliconflow",
    "type": "openai",
    "api_key": "sk-你的Key",
    "api_base": "https://api.siliconflow.cn/v1",
    "model": "Kwai-Kolors/Kolors",
    "size_map": {
      "1024*1024": "1024x1024"
    },
    "extra_body": {
      "batch_size": 1
    }
  }
]
```

只要对方实现 `POST /v1/images/generations`，并能返回 `url` 或 `b64_json`，就可以接。

---

## 5. 让 bot 自己学会用

插件通过 AstrBot 的 `on_llm_request` 钩子，把一段「生图工具使用说明」追加到每次 LLM 请求的 `system_prompt` 里。默认内容包括：

- 两个工具的用途：`image_draw`（聊天画图）、`image_schedule`（今日日程图）；
- 当前可用的服务商名称、类型、模型；
- 什么时候该调用、`prompt` 怎么写、`size` / `provider` 怎么填；
- 已上传角色三视图时，工具会自动带参考图，让模型保持角色一致；
- 工具会直接把图发给用户，模型只需要简短回应，不要重复描述画面。

相关配置：

- `llm_guide_enabled`：总开关，默认开；
- `llm_guide_text`：提示词模板，可改成你的人格口吻；
- 占位符：`{providers}`、`{default_size}`、`{tool_draw}`、`{tool_schedule}`；
- 工具名固定为 `image_draw` / `image_schedule`，提示词里会自动填好。

改完后，bot 在用户说「画一张…」「给我整个头像」「来张图」「把今天的日程画出来」时，会自己调用工具，不需要用户记指令。

> 如果模型仍然不动手，先确认 `llm_guide_enabled=true`，再看 `/生图状态` 里服务商是否有效。部分小模型对工具调用不敏感，可以在人格提示里补一句「想画图时调用 image_draw」。

---

## 6. 指令

| 指令 | 说明 |
| --- | --- |
| `/画 <描述>` | 画一张图；支持 `--size 768*1024`、`--style 水彩`、`--provider qwen` |
| `/日程图 [YYYY-MM-DD]` | 生成指定日期的日程插画；支持 `--provider` |
| `/生图状态` | 查看服务商、Key 是否找到、默认服务商、日程时间、今日是否已发 |
| `/生图帮助` | 查看简短用法和可用服务商 |

示例：

```text
/画 一只在窗台晒太阳的橘猫 --size 768*1024 --style 水彩
/画 赛博朋克风格的城市夜景 --provider openai
/日程图
/日程图 2026-11-08 --provider gemini
```

指令别名：`/draw`、`/生图`、`/画图`；`/今日日程图`、`/scheduleimage`；`/画图状态`。

---

## 7. 角色三视图与主动画图

插件自带一个 `character` 视图，用来把角色“固定”下来，避免每张图都换脸换衣服。

打开方式：AstrBot 插件管理 → 角色画室 → 打开插件页面（`character`）。

### 7.1 上传三视图

- 上传正面、侧面、背面三张图，支持 PNG / JPG / WebP，单张不超过 8MB；
- 也可以点“上传整张三视图”，直接传一张角色设定总览图：插件会自动找上方角色排、按纵向空白切成正 / 侧 / 背三张，单张总图不超过 12MB；
- 自动切分适合“三个角色并排、背景干净”的图；切得不准时，再单独点某张卡片更换即可；
- 建议同一角色、同一画风、纯色或简单背景，脸型、发型、主色保持一致；
- 三张图不需要完美，但最好是同一个角色；
- 上传后填写角色名和外观/性格描述，点“保存角色设定”。

保存后，**聊天画图、`/画`、日程图、主动画图都会自动把三视图作为参考图一起发给模型**，不需要每次重复描述角色。

“用当前角色试画”会立即生成一张预览，用来检查相似度；试画不会发送到聊天。

### 7.2 主动画图

在同一个视图里开启“允许主动画图”，然后设置：

- 间隔（分钟）：默认 180；
- 每天最多几张：默认 2，0 表示不限制；
- 开始/结束小时：默认 9–23；
- 场景列表：每行一个，插件会随机挑一个场景组 prompt。

插件会按 `state.json` 里的 `last_umo` 找到最近一次对话的会话发送；没有记录时跳过。建议先用 180 分钟 / 2 张试运行，确认不会打扰后再调低间隔。

三视图和角色设定保存在插件数据目录的 `character/` 与 `character/profile.json`，只存在本地，不会上传到第三方。

---

## 8. 每日日程插画

插件会按 `schedule_time`（默认 `08:30`）生成一张当天日程图，并推送到 `schedule_sessions` 里的会话。

- 如果填了 `virtual_life` 的 `plans.json` 路径，会读取当天 `日期::人格` 的计划，取 `theme`、`mood`、`outfit`、`timeline` 组成提示词；
- 没装 `virtual_life` 也能用：会退化成「日常 / 悠闲 / 舒服的衣服 / 按自己的节奏慢慢过」；
- `schedule_sessions` 留空时，会推给最近一次生图的会话（插件会记录 `last_umo`）；
- `schedule_catch_up=true` 时，如果插件在当天日程时间之后启动，且还在 `schedule_catch_up_window_hours` 内，会补发一张；
- 同一天只会发一次，用插件数据目录下 `state.json` 的 `last_schedule_date` 防重（重启后仍然有效）。

会话格式示例：

```text
qq-onebot:FriendMessage:123456
qq-onebot:GroupMessage:987654
```

多个会话用逗号或换行分隔。

### 自定义日程提示词

`schedule_prompt_template` 支持占位符：

```text
{date} {weekday} {persona_name} {theme} {mood} {outfit} {timeline}
```

如果不用 `virtual_life`，可以把模板改成固定内容，例如：

```text
今天是 {date}。画一张「{persona_name}」的今日生活插画，主题是{theme}，心情{mood}。
```

---

## 9. 配置项速查

### 服务商

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `providers_json` | `[]` | 服务商 JSON 数组 |
| `active_provider` | 空 | 默认服务商名 |
| `provider_fallback` | `true` | 失败自动换下家 |
| `provider_type` / `api_key` / `api_base` / `model` | — | 旧版单服务商配置，仅在 `providers_json` 为空时生效 |
| `cmd_config_path` | `/AstrBot/data/cmd_config.json` | DashScope Key 自动查找路径 |

### 生成

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `default_size` | `1024*1024` | 默认尺寸 |
| `style_prompt` | 温暖插画风 | 默认追加画风 |
| `negative_prompt` | 空 | 负面词 |
| `watermark` | `false` | 水印 |
| `prompt_extend` | `false` | 千问提示词扩写 |
| `enable_thinking` | `false` | 千问思考模式 |
| `timeout` | `180` | 整体超时 |
| `poll_interval` | `2.0` | 异步轮询间隔 |
| `request_timeout` | `60` | 单次 HTTP 超时 |
| `max_prompt_chars` | `800` | 提示词截断长度 |
| `max_concurrency` | `2` | 同时生图数 |
| `cooldown_seconds` | `8` | 同一用户冷却，0 关闭 |
| `max_images_keep` | `200` | 本地缓存上限 |
| `show_provider` | `false` | 配文里显示实际服务商 |

### 日程

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `schedule_enabled` | `true` | 开关 |
| `schedule_time` | `08:30` | 推送时间 |
| `schedule_timezone` | `Asia/Shanghai` | 时区 |
| `schedule_sessions` | 空 | 推送会话 |
| `schedule_persona_name` | `助手` | 匹配 `plans.json` 的人格名 |
| `schedule_use_virtual_life` | `true` | 读取 virtual_life |
| `virtual_life_plans_path` | `/AstrBot/data/plugin_data/astrbot_plugin_virtual_life/plans.json` | 数据路径 |
| `schedule_provider` | 空 | 日程图指定服务商 |
| `schedule_prompt_template` | 见默认 | 提示词模板 |
| `schedule_style_prompt` | 温暖生活插画 | 日程画画风 |
| `schedule_caption` | 见默认 | 配文模板 |
| `schedule_size` | `1024*1024` | 尺寸 |
| `schedule_progress_hint` | 见默认 | 进度提示 |
| `schedule_catch_up` | `true` | 启动补发 |
| `schedule_catch_up_window_hours` | `3.0` | 补发窗口 |

### LLM

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `llm_guide_enabled` | `true` | 注入使用说明 |
| `llm_guide_text` | 见默认 | 说明模板 |

---

## 10. 常见问题

**401 / 403**

Key 不对、账号没开通该模型、或 Key 没有图片权限。先到服务商控制台确认模型已开通、余额充足。

**404 / Model not exist**

模型名写错，或该服务商没有这个模型。OpenAI 兼容网关还要确认 `api_base` 是否包含正确的版本前缀（如 `/v1`）。

**千问返回 404**

旧域名 `dashscope.aliyuncs.com` 的 OpenAI 兼容 `/images/generations` 不一定可用。本插件用的是百炼原生异步接口：

```text
POST /api/v1/services/aigc/image-generation/generation
GET  /api/v1/tasks/{task_id}
```

不需要手动改。

**图片一直不返回 / 超时**

把 `timeout` 调大；Gemini 和 gpt-image-1 可能比千问慢。`poll_interval` 不建议小于 1 秒。

**Gemini 只返回文字**

确认 `response_modalities` 包含 `"IMAGE"`，并且模型本身支持图片输出。部分 Key 需要先在 Google AI Studio 开通对应模型。

**日程图没发**

- `schedule_enabled` 是否为 `true`；
- `schedule_sessions` 是否为空且没有历史 `last_umo`；
- 时区是否写对；
- 看日志里「定时日程生图跳过」「发送失败」的提示；
- 同一天已经发过会跳过，想重发可改 `schedule_time` 或清 KV。

**回退了但不知道为什么**

把 `show_provider` 打开，配文会显示实际服务商；日志里也会记录每个失败服务商的原因。

---

## 11. 隐私与分享

- 插件代码不包含任何 API Key、QQ 号、会话 ID、服务器路径或人格名；
- 角色三视图、角色设定和主动画图记录只保存在插件数据目录，不会上传；
- 配置里的 Key 只存在本地 AstrBot 配置文件中，不会上传；
- 分享给别人时，**不要连 `config/astrbot_plugin_character_atelier_config.json` 一起发**，让对方自己填 Key；
- 如果要从自己的部署复制一份给别人，建议只复制插件目录，并检查 `schedule_sessions`、`api_key`、`persona` 等字段是否已清空。

---

## 12. 文件结构

```text
astrbot_plugin_character_atelier/
├── main.py                    # 插件入口：指令、LLM 工具、角色 API、日程、主动画图
├── image_client.py            # 多服务商客户端：dashscope / openai / gemini
├── sheet_split.py             # 整张三视图自动切分（Pillow）
├── _conf_schema.json          # 插件配置面板
├── metadata.yaml              # 插件元信息
├── requirements.txt           # httpx、Pillow
├── README.md                  # 项目文档
├── LICENSE                    # MIT
├── .gitignore                 # 开发文件忽略规则
├── logo.png                   # AstrBot 插件图标
└── views/
    └── character/
        └── index.html         # 角色三视图、整图上传与主动画图视图
```

---

## 13. 开发与测试

最直接的验收方式：

1. 在 AstrBot 面板里打开本插件的「角色」页面，上传三视图或一张整图；
2. 点「用当前角色试画」，确认返回的图保持角色形态；
3. 在聊天里发「画一张她在窗边晒太阳的图」，确认 LLM 工具能调用并发出图片；
4. 用 `/生图状态` 查看当前服务商、角色参考图与队列状态。

OpenAI / Gemini 通道可以用 `/画 --provider 名称` 逐项验证，或在配置里打开 `show_provider` 看实际服务商。

---

## 14. 发布到 GitHub

这个目录本身就是可直接上传的仓库根目录，不需要再套一层文件夹。

更细的网页上传 / 命令行推送 / Release 步骤见 [GITHUB_PUBLISH.md](GITHUB_PUBLISH.md)。

建议仓库信息：

- 仓库名：`astrbot_plugin_character_atelier`
- 描述：`多服务商生图插件 for AstrBot：千问 / OpenAI 兼容 / Gemini，角色三视图、整图切分、主动画图`
- Topics：`astrbot`、`astrbot-plugin`、`image-generation`、`qwen`、`openai`、`gemini`

发布后把仓库地址填到 `metadata.yaml` 的 `repo` 字段：

```yaml
repo: https://github.com/<你的用户名>/astrbot_plugin_character_atelier
```

如果通过 GitHub 网页上传，直接把本目录里的文件拖进仓库根目录即可；`logo.png` 会作为 AstrBot 插件图标，`views/character/` 必须保持原目录结构。

---

## 15. 许可

本项目使用 [MIT License](LICENSE)。二次分发请保留 `README.md`、`LICENSE` 与作者署名。
