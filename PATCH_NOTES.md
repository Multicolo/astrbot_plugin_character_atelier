# 补丁说明（v2.5.3）

版本线：v2.5.0 提示词内置化 + 参考图开关 → v2.5.1 主动画图混入纯风景 + 去 AI 味 → v2.5.2 版本号重发 → v2.5.3 默认值对齐。

## 0. v2.5.3 只改默认值，不动代码逻辑

`main.py` 该版本只改了版本号一行，提示词正文一个字没动。改的是 `_conf_schema.json` 里三个默认值：

| 配置 | 旧默认 | 新默认 | 影响 |
| --- | --- | --- | --- |
| `reference_mode` | `always` | `on_demand` | 新装默认不随每次请求上传三视图；纯风景图不会被塞进角色 |
| `proactive_mix_scenery` | `false` | `true` | 新装默认有约一半概率画纯风景 |
| `schedule_enabled` | `true` | `false` | 新装默认不推每日日程图（没有日程数据源时只会出套话图） |

**已保存过配置的实例完全不受影响**——默认值只在字段没有值时生效。

## 1. 主动画图混入纯风景（v2.5.1）

- `proactive_mix_scenery`（默认 `false`）：开启后每次主动画图掷一次 50% 的硬币。
  - 命中风景：用 `proactive_scenery_scenes` 的场景池 + `proactive_scenery_style_prompt` 的画风，`keep_character=False`，提示词里明写「画面里不要出现人」。
  - 未命中：仍用角色档案里的场景列表和 `proactive_style_prompt`，`keep_character=True`。
- `proactive_scenery_scenes` 留空时用代码内置的 12 条海边场景。

## 2. 去 AI 味（v2.5.1）

- `style_prompt` 默认改为「生活化的手绘插画，柔和自然光，轻微纸张纹理，笔触不均匀，色彩偏灰，构图放松」。
- `negative_prompt` 默认补上：塑料质感、过度光滑皮肤、CG渲染、3D模型感、油腻高光、完美对称、高饱和、荧光色、锐利HDR。
- `proactive_style_prompt` 改为「像手机随手拍，轻微噪点和胶片颗粒，构图有点歪」。
- 内置提示词加一条：描述要具体到能拍，别写 8k / masterpiece / 超精细，也别把画面写得又干净又满。

> 这些只是**默认值**。已经存过配置的实例不会自动换，需要手动改，或把字段清空让它回到默认。

## 3. 沿用 v2.5.0 的核心改动

- `llm_guide_source`（默认 `builtin`）：内置提示词随插件版本更新，更新插件即更新提示词；切 `custom` 才用 `llm_guide_text`。
- `reference_mode`（`always` / `on_demand` / `off`）：`on_demand` 下人物图才带三视图，纯风景不带。
- 工具 `image_draw(keep_character)`、命令 `/画 --角色`。
- 修复：角色描述不再依赖参考图；主动画图不再因缺参考图静默空转。

## 升级后怎么验

```text
1. 覆盖安装，确认版本 v2.5.1。
2. 面板打开 proactive_mix_scenery。
3. 把默认画风 / 负面词改成新版（旧配置不会自动跟随）。
4. 等一次主动画图，或直接 /画 一张风景，看塑料感是否下降。
```

静态验证：`py_compile` 通过、`_conf_schema.json` 可解析、内置提示词 913 字（含新增去 AI 味那条）。
