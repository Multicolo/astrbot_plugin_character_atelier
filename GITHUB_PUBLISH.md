# 发布到 GitHub

这个目录已经是可直接作为仓库根目录的完整插件，不需要再套 `astrbot_plugin_character_atelier/` 文件夹。

## 1. 建议的仓库信息

- 仓库名：`astrbot_plugin_character_atelier`
- 描述：`角色画室 · 多模型生图：AstrBot 插件，支持千问 / OpenAI 兼容 / Gemini，角色三视图、整图切分、主动画图`
- Topics：`astrbot`、`astrbot-plugin`、`image-generation`、`qwen`、`openai`、`gemini`
- License：MIT
- 中文搜索：仓库描述、README 标题和正文都保留「角色画室」「多模型生图」「角色三视图」「AI 生图」等词，GitHub 中文搜索可以命中。

## 2. 网页上传（不需要命令行）

1. 在 GitHub 新建空仓库，**不要**勾选 “Add a README file”。
2. 打开空仓库页面，点击 **uploading an existing file**。
3. 把本目录里的全部文件拖进去（保留 `views/character/` 目录结构）。
4. 提交信息填 `初始提交：角色画室插件 v2.3.0`，点击 **Commit changes**。
5. 进入仓库 **Settings → General**，把仓库描述和 Topics 填上。
6. 把仓库地址填到 `metadata.yaml` 的 `repo` 字段，再提交一次：

   ```yaml
   repo: https://github.com/<你的用户名>/astrbot_plugin_character_atelier
   ```

## 3. 命令行上传

需要本机已安装 Git，并已登录 GitHub（HTTPS 需要 Personal Access Token，SSH 需要配置公钥）。

```bash
cd astrbot_plugin_character_atelier
git init
git add .
git commit -m "初始提交：角色画室插件 v2.3.0"
git branch -M main
git remote add origin https://github.com/<你的用户名>/astrbot_plugin_character_atelier.git
git push -u origin main
```

如果使用 SSH：

```bash
git remote add origin git@github.com:<你的用户名>/astrbot_plugin_character_atelier.git
git push -u origin main
```

## 4. 发布前检查

- [ ] 没有提交真实 API Key、QQ 号、会话 UMO、服务器路径
- [ ] 没有提交 `character/`、`images/`、`data/`、`__pycache__/`
- [ ] `metadata.yaml` 的 `name` 与目录名一致
- [ ] `views/character/index.html` 存在
- [ ] `logo.png` 在仓库根目录
- [ ] `README.md` 中的示例 Key 全部是占位符

## 5. 可选：打一个 Release

```bash
git tag v2.3.0
git push origin v2.3.0
```

然后在 GitHub 的 **Releases → Draft a new release** 里选择 `v2.3.0`，上传 `角色画室_astrbot_plugin_character_atelier.zip`。AstrBot 插件市场或手动安装都可以直接用这个 zip。

## 6. 说明

- 插件运行时会生成 `character/`、`images/`、`state.json`，这些属于用户数据，已在 `.gitignore` 中排除。
- `README.md` 是面向使用者的完整文档；本文件只讲“怎么发布到 GitHub”。
