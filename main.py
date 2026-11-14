from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import random
import re
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import quote
from zoneinfo import ZoneInfo

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.web import error_response, json_response, request
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.command import GreedyStr

from .image_client import ImageClient, ImageError, ImageProvider
from .sheet_split import SheetSplitError, split_character_sheet

PLUGIN_NAME = "astrbot_plugin_character_atelier"

DEFAULT_SCHEDULE_TEMPLATE = (
    "今天是 {date}（{weekday}）。请画一张「{persona_name}」的今日日程插画："
    "主题是{theme}，心情{mood}，{outfit}。今天的片段：{timeline}。"
    "画面像一张温暖的生活插画，主体严格保持角色三视图中的脸型、发型、配色和服装，"
    "不要换成其他角色；场景与日程呼应，构图完整，光线柔和，细节丰富。"
)

DEFAULT_SCHEDULE_CAPTION = (
    "今日份日程图：{date} {theme}｜{mood}\n{persona_name}把今天画给你看啦。"
)

DEFAULT_LLM_GUIDE = """【生图工具使用说明】
你有生图能力：调用 {tool_draw} 会直接生成图片并发给用户。不要说“我画不了”，不要用文字描述代替图片，也不要输出图片链接或假装发图。

什么时候调用
- 用户明确要图：画一张、来张图、要头像/插画/表情包/壁纸/封面 → 直接调用。
- 用户没说要图，但话里有具体画面（“好想看看她穿婚纱的样子”）→ 值得画就直接调用；你自己撞见好看的风景（黄昏的海）→ 直接画，这种图不画人。
- 用户只是聊天、夸图、吐槽、发表情 → 不调用。
- 只有“画个图”这类没说画什么的空话 → 先用角色口吻反问一句，问清了再画。

prompt 怎么写
- 写清主体、动作、场景、光线、构图、风格，一到三句；不要写“帮我画”“画好看点”这类空话。
- 尺寸不要塞进 prompt，用 size 参数：1024*1024 方图、768*1024 竖图、1024*768 横图；不确定就留空。
- 风格按场景切换，填 style：日常生活/温柔时刻→温暖插画；旅行/风景/纪实→写实照片感；搞笑/表情包→扁平卡通；用户点了风格就用用户的。
- 画面里要不要出现她，用 keep_character 决定：要出现 → 填 true，插件会附上三视图和角色描述，你不用写她的长相；纯风景、静物、没有人的画面 → 留空，也不要提她的名字和长相。
- provider 只有用户点名服务商时才填，否则留空。当前可用：{providers}；默认尺寸 {default_size}。

调用之后
- 图已经发出去了。你只用一两句话回应，带情绪、像本人说话，不要复述画面里有什么，不要报尺寸和模型名。
- 失败就简短说原因（额度、超时、内容被拒），给一条能执行的建议（换描述/换风格/稍后再试）。同一句别连着重试两次。

不要画
真人或明星的脸、露骨色情、政治敏感、血腥暴力。遇到就按角色口吻拒绝，别说教，紧接着给一个能画的替代方案（换场景、换构图、只画背影）。

工具列表里的 image_schedule 不要调用；用户想看“今天过得怎么样”，用 {tool_draw} 按上下文自己画。"""

SIZE_PRESETS = {
    "square": "1024*1024",
    "方图": "1024*1024",
    "正方形": "1024*1024",
    "portrait": "768*1024",
    "竖图": "768*1024",
    "手机壁纸": "720*1280",
    "landscape": "1024*768",
    "横图": "1024*768",
    "壁纸": "1280*720",
}


@register(
    PLUGIN_NAME,
    "花影落闲庭",
    "角色画室 · 多模型生图：支持阿里云百炼千问、OpenAI 兼容接口与 Gemini，提供 LLM 工具、/画 指令、每日日程插画、主动画图与按人设绑定的角色三视图（支持整图自动切分）。",
    "v2.5.0",
)
class CharacterAtelierPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        try:
            self._data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:  # noqa: BLE001
            self._data_dir = Path("/AstrBot/data/plugin_data") / PLUGIN_NAME
        self._cache_dir = self._data_dir / "images"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._data_dir / "state.json"
        self._character_dir = self._data_dir / "character"
        self._character_dir.mkdir(parents=True, exist_ok=True)
        self._character_path = self._character_dir / "profile.json"

        self._schedule_task: asyncio.Task | None = None
        self._proactive_task: asyncio.Task | None = None
        self._semaphore = asyncio.Semaphore(
            max(1, self._cfg_int("max_concurrency", 2))
        )
        self._last_call: dict[str, float] = {}
        self._register_web_apis()

    # ------------------------------------------------------------------ config
    def _cfg(self, key: str, default=None):
        try:
            value = self.config.get(key, default)
        except Exception:  # noqa: BLE001
            return default
        return default if value is None else value

    def _cfg_bool(self, key: str, default: bool = False) -> bool:
        value = self._cfg(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "是"}

    def _cfg_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _reference_mode(self) -> str:
        """三视图参考图使用策略：always / on_demand / off。"""
        mode = str(self._cfg("reference_mode", "always")).strip().lower()
        return mode if mode in {"always", "on_demand", "off"} else "always"

    def _cfg_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------ lifecycle
    async def initialize(self) -> None:
        self._cleanup_cache()
        self._start_scheduler()
        self._start_proactive()
        try:
            providers, errors = self._load_providers()
            summary = ", ".join(f"{item.name}={item.type}/{item.model}" for item in providers)
        except Exception as exc:  # noqa: BLE001
            summary = f"配置错误：{exc}"
            errors = [str(exc)]
        logger.info(
            "角色画室已加载：%s；定时日程图 %s %s",
            summary or "无服务商",
            self._cfg("schedule_time", "08:30"),
            self._cfg("schedule_timezone", "Asia/Shanghai"),
        )
        for error in errors[:5]:
            logger.warning("生图配置提示：%s", error)

    async def terminate(self) -> None:
        for attr in ("_schedule_task", "_proactive_task"):
            task = getattr(self, attr, None)
            setattr(self, attr, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    # ------------------------------------------------------------------ character
    CHARACTER_SLOTS = ("front", "side", "back")
    CHARACTER_DEFAULT = {
        "persona_id": "",
        "name": "",
        "description": "",
        "slots": {},
        "proactive_enabled": False,
        "proactive_interval_minutes": 180,
        "proactive_daily_limit": 2,
        "proactive_start_hour": 9,
        "proactive_end_hour": 23,
        "proactive_scenes": [],
    }

    @staticmethod
    def _safe_persona_key(persona_id: str) -> str:
        text = str(persona_id or "").strip()
        if not text or text == "default":
            return "default"
        slug = re.sub(r"[^0-9A-Za-z_-]+", "_", text).strip("_")[:40] or "persona"
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
        return f"{slug}-{digest}"

    def _persona_dir(self, persona_id: str | None = None) -> Path:
        text = str(persona_id or "").strip()
        if not text or text == "default":
            return self._character_dir
        path = self._character_dir / "personas" / self._safe_persona_key(text)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _profile_path(self, persona_id: str | None = None) -> Path:
        return self._persona_dir(persona_id) / "profile.json"

    def _read_profile_file(self, path: Path) -> dict | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        return raw if isinstance(raw, dict) else None

    def _legacy_profile(self) -> dict:
        raw = self._read_profile_file(self._character_path)
        return raw if isinstance(raw, dict) else {}

    def _read_character(self, persona_id: str | None = None) -> dict:
        text = str(persona_id or "").strip() or "default"
        data = dict(self.CHARACTER_DEFAULT)
        path = self._profile_path(text)
        raw = self._read_profile_file(path) if path.is_file() else None

        # 老版本只有 character/profile.json；如果它的角色名正好等于当前人设，
        # 自动复制一份到该人设目录，避免升级后旧角色“消失”。
        if raw is None and text != "default":
            legacy = self._legacy_profile()
            if str(legacy.get("name") or "").strip() == text:
                raw = dict(legacy)
                self._write_character(raw, text)
                path = self._profile_path(text)

        if raw:
            data.update(raw)
        data["persona_id"] = text
        if not isinstance(data.get("slots"), dict):
            data["slots"] = {}
        return data

    def _write_character(self, data: dict, persona_id: str | None = None) -> None:
        text = str(persona_id or "").strip() or "default"
        if text != "default":
            data["persona_id"] = text
        path = self._profile_path(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)

    def _character_image_path(
        self, slot: str, persona_id: str | None = None
    ) -> Path | None:
        if slot not in self.CHARACTER_SLOTS:
            return None
        profile = self._read_character(persona_id)
        filename = str((profile.get("slots") or {}).get(slot) or "")
        if not filename:
            return None
        path = self._persona_dir(persona_id) / Path(filename).name
        return path if path.is_file() else None

    def _auto_refs(self, persona_id: str | None = None, keep_character: bool = False) -> list[str]:
        """按 reference_mode 决定本次是否附带三视图参考图。"""
        mode = self._reference_mode()
        if mode == "off":
            return []
        if mode == "on_demand" and not keep_character:
            return []
        return self._character_refs(persona_id)

    def _character_refs(self, persona_id: str | None = None) -> list[str]:
        refs: list[str] = []
        for slot in self.CHARACTER_SLOTS:
            path = self._character_image_path(slot, persona_id)
            if not path:
                continue
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            refs.append(f"data:{mime};base64,{encoded}")
        return refs

    def _character_prompt(self, persona_id: str | None = None) -> str:
        profile = self._read_character(persona_id)
        name = str(profile.get("name") or "").strip()
        desc = str(profile.get("description") or "").strip()
        if not name and not desc:
            return ""
        text = f"角色「{name}」" if name else "角色"
        if desc:
            text += f"：{desc}"
        text += "。严格保持角色三视图中的脸型、发型/头身、配色和服装特征，不要换角色。"
        return text

    def _character_state(self, persona_id: str | None = None) -> dict:
        text = str(persona_id or "").strip() or "default"
        profile = self._read_character(text)
        images: dict[str, str] = {}
        for slot in self.CHARACTER_SLOTS:
            path = self._character_image_path(slot, text)
            if path:
                base = (
                    f"/api/v1/plugins/extensions/{PLUGIN_NAME}/character/image/"
                    f"{slot}?v={int(path.stat().st_mtime)}"
                )
                if text != "default":
                    base += f"&persona={quote(text, safe='')}"
                images[slot] = base
        return {"profile": profile, "images": images, "persona_id": text}

    def _profile_has_images(self, persona_id: str) -> bool:
        return any(
            self._character_image_path(slot, persona_id)
            for slot in self.CHARACTER_SLOTS
        )

    async def _persona_catalog(self) -> list[dict]:
        items: list[dict] = []
        seen: set[str] = set()

        def add(persona_id: str, label: str = "", prompt: str = "") -> None:
            pid = str(persona_id or "").strip()
            if not pid or pid in seen:
                return
            seen.add(pid)
            profile = self._read_character(pid)
            items.append(
                {
                    "id": pid,
                    "label": label or pid,
                    "prompt": prompt[:160],
                    "name": str(profile.get("name") or "").strip(),
                    "has_profile": self._profile_path(pid).is_file()
                    or bool(profile.get("name") or profile.get("description")),
                    "has_images": self._profile_has_images(pid),
                }
            )

        mgr = getattr(self.context, "persona_manager", None)
        if mgr is not None:
            try:
                personas = await mgr.get_all_personas()
                for persona in personas:
                    pid = str(getattr(persona, "persona_id", "") or "").strip()
                    prompt = str(getattr(persona, "system_prompt", "") or "")
                    add(pid, pid, prompt)
            except Exception:  # noqa: BLE001
                logger.debug("读取 AstrBot 人设列表失败", exc_info=True)

        add("default", "默认（不绑定人设）")
        try:
            for path in sorted((self._character_dir / "personas").glob("*/profile.json")):
                raw = self._read_profile_file(path)
                if raw:
                    add(str(raw.get("persona_id") or "").strip())
        except Exception:  # noqa: BLE001
            pass

        items.sort(key=lambda item: (item["id"] != "default", item["label"]))
        return items

    async def _resolve_persona_id(self, event: AstrMessageEvent | None = None) -> str:
        if event is not None:
            try:
                traced = event.get_extra("_trace_persona_id")
                if traced:
                    return str(traced)
            except Exception:  # noqa: BLE001
                pass
        if event is None:
            return "default"
        try:
            cm = getattr(self.context, "conversation_manager", None)
            conversation_persona_id = None
            if cm is not None:
                umo = event.unified_msg_origin
                cid = await cm.get_curr_conversation_id(umo)
                if cid:
                    conversation = await cm.get_conversation(umo, cid)
                    if conversation is not None:
                        conversation_persona_id = getattr(
                            conversation, "persona_id", None
                        )
            mgr = getattr(self.context, "persona_manager", None)
            if mgr is not None:
                persona_id, _, _, _ = await mgr.resolve_selected_persona(
                    umo=event.unified_msg_origin,
                    conversation_persona_id=conversation_persona_id,
                    platform_name=event.get_platform_name(),
                )
                if persona_id:
                    return str(persona_id)
        except Exception:  # noqa: BLE001
            logger.debug("解析当前会话人设失败", exc_info=True)
        return "default"

    def _schedule_persona_id(self) -> str:
        raw = str(self._cfg("schedule_persona_name", "") or "").strip()
        return raw or "default"

    async def _request_persona_id(self, payload: dict | None = None) -> str:
        pid = ""
        if isinstance(payload, dict):
            pid = str(payload.get("persona") or payload.get("persona_id") or "").strip()
        if not pid:
            try:
                pid = str(request.query.get("persona", "") or "").strip()
            except Exception:  # noqa: BLE001
                pid = ""
        if not pid:
            try:
                form = await request.form()
                pid = str(form.get("persona", "") or "").strip()
            except Exception:  # noqa: BLE001
                pid = ""
        return pid or "default"

    def _register_web_apis(self) -> None:
        reg = getattr(self.context, "register_web_api", None)
        if reg is None:
            logger.warning("当前 AstrBot 版本不支持插件 Web API，角色视图不可用")
            return
        reg(
            f"/{PLUGIN_NAME}/character",
            self.api_character_get,
            ["GET"],
            "角色视图：读取角色设定",
        )
        reg(
            f"/{PLUGIN_NAME}/character",
            self.api_character_save,
            ["POST"],
            "角色视图：保存角色设定",
        )
        reg(
            f"/{PLUGIN_NAME}/character/personas",
            self.api_character_personas,
            ["GET"],
            "角色视图：读取 AstrBot 人设列表",
        )
        reg(
            f"/{PLUGIN_NAME}/character/sheet",
            self.api_character_sheet,
            ["POST"],
            "角色视图：上传整张三视图并自动切分",
        )
        reg(
            f"/{PLUGIN_NAME}/character/upload",
            self.api_character_upload,
            ["POST"],
            "角色视图：上传三视图",
        )
        reg(
            f"/{PLUGIN_NAME}/character/upload/<slot>",
            self.api_character_upload,
            ["POST"],
            "角色视图：按角度上传三视图",
        )
        reg(
            f"/{PLUGIN_NAME}/character/image/<slot>",
            self.api_character_image,
            ["GET"],
            "角色视图：读取三视图",
        )
        reg(
            f"/{PLUGIN_NAME}/character/preview",
            self.api_character_preview,
            ["POST"],
            "角色视图：按当前角色试画一张",
        )

    async def api_character_get(self, **kwargs):
        persona_id = await self._request_persona_id()
        return json_response(
            {
                "ok": True,
                "persona_id": persona_id,
                "personas": await self._persona_catalog(),
                **self._character_state(persona_id),
            }
        )

    async def api_character_personas(self, **kwargs):
        persona_id = await self._request_persona_id()
        return json_response(
            {
                "ok": True,
                "persona_id": persona_id,
                "personas": await self._persona_catalog(),
            }
        )

    async def api_character_save(self, **kwargs):
        payload = await request.json({})
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象")
        persona_id = await self._request_persona_id(payload)
        profile = self._read_character(persona_id)
        for key in ("name", "description"):
            if key in payload:
                profile[key] = str(payload[key] or "").strip()[:2000]
        for key in (
            "proactive_enabled",
            "proactive_interval_minutes",
            "proactive_daily_limit",
            "proactive_start_hour",
            "proactive_end_hour",
        ):
            if key not in payload:
                continue
            if key == "proactive_enabled":
                profile[key] = bool(payload[key])
            else:
                try:
                    profile[key] = int(payload[key])
                except (TypeError, ValueError):
                    return error_response(f"{key} 必须是整数")
        scenes = payload.get("proactive_scenes")
        if isinstance(scenes, list):
            profile["proactive_scenes"] = [
                str(item).strip()[:300] for item in scenes if str(item).strip()
            ][:20]
        self._write_character(profile, persona_id)
        return json_response(
            {
                "ok": True,
                "persona_id": persona_id,
                "personas": await self._persona_catalog(),
                **self._character_state(persona_id),
            }
        )

    async def api_character_sheet(self, **kwargs):
        """接收一张总览图，自动切成正面 / 侧面 / 背面三张参考图。"""
        persona_id = await self._request_persona_id()
        files = await request.files()
        upload = files.get("file") if files else None
        if upload is None:
            return error_response("没有收到图片")
        mime = str(getattr(upload, "content_type", "") or "").lower()
        if mime not in {"image/png", "image/jpeg", "image/webp"}:
            return error_response("只支持 PNG / JPG / WebP 图片")
        target = self._persona_dir(persona_id)
        tmp = target / f".sheet_{uuid.uuid4().hex}"
        try:
            await upload.save(tmp, max_bytes=12 * 1024 * 1024)
            if tmp.stat().st_size < 256:
                return error_response("图片内容为空")
            raw = tmp.read_bytes()
            try:
                crops = split_character_sheet(raw)
            except SheetSplitError as exc:
                return error_response(str(exc))
            (target / "sheet.png").write_bytes(raw)
            profile = self._read_character(persona_id)
            slots = profile.setdefault("slots", {})
            for slot, data in crops.items():
                dest = target / f"{slot}.png"
                dest.write_bytes(data)
                slots[slot] = dest.name
            self._write_character(profile, persona_id)
            return json_response(
                {
                    "ok": True,
                    "persona_id": persona_id,
                    "personas": await self._persona_catalog(),
                    **self._character_state(persona_id),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("自动切分角色三视图失败：%s", exc)
            return error_response("自动切分失败，请换一张背景更干净、三个角色并排的图")
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    async def api_character_upload(self, slot: str = "", **kwargs):
        persona_id = await self._request_persona_id()
        files = await request.files()
        upload = files.get("file") if files else None
        if upload is None:
            return error_response("没有收到文件")
        slot = str(slot or (await request.form()).get("slot") or "front").strip()
        if slot not in self.CHARACTER_SLOTS:
            return error_response("slot 只能是 front / side / back")
        mime = str(getattr(upload, "content_type", "") or "").lower()
        if mime not in {"image/png", "image/jpeg", "image/webp"}:
            return error_response("只支持 PNG / JPG / WebP 图片")
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime]
        target = self._persona_dir(persona_id)
        dest = target / f"{slot}{suffix}"
        tmp = target / f".upload_{uuid.uuid4().hex}"
        try:
            await upload.save(tmp, max_bytes=8 * 1024 * 1024)
            if tmp.stat().st_size < 256:
                return error_response("图片内容为空")
            os.replace(tmp, dest)
        except Exception as exc:  # noqa: BLE001
            logger.warning("保存角色三视图失败：%s", exc)
            return error_response("保存图片失败，可能是文件超过 8MB")
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        profile = self._read_character(persona_id)
        slots = profile.setdefault("slots", {})
        slots[slot] = dest.name
        self._write_character(profile, persona_id)
        return json_response(
            {
                "ok": True,
                "persona_id": persona_id,
                "personas": await self._persona_catalog(),
                **self._character_state(persona_id),
            }
        )

    async def api_character_image(self, slot: str = "", **kwargs):
        persona_id = await self._request_persona_id()
        path = self._character_image_path(slot, persona_id)
        if not path:
            return error_response("这个角度还没有上传", status_code=404)
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        # 不要把图片放在顶层 data 字段：宿主 bridge 会解包 data。
        return json_response({"ok": True, "mime": mime, "image": encoded})

    async def api_character_preview(self, **kwargs):
        payload = await request.json({})
        persona_id = await self._request_persona_id(
            payload if isinstance(payload, dict) else None
        )
        try:
            path, used = await self._generate(
                "角色日常插画：站在窗边回头看向镜头，表情自然，生活感。",
                size="768*1024",
                style="",
                reference_images=self._character_refs(persona_id),
                persona_id=persona_id,
            )
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return json_response(
                {
                    "ok": True,
                    "persona_id": persona_id,
                    "provider": used,
                    "mime": "image/png",
                    "image": encoded,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return error_response(self._friendly_error(exc), status_code=500)

    # ------------------------------------------------------------------ providers
    def _legacy_provider_dict(self) -> dict:
        return {
            "name": str(self._cfg("provider_name", "default")).strip() or "default",
            "type": str(self._cfg("provider_type", "dashscope")).strip() or "dashscope",
            "api_key": str(self._cfg("api_key", "") or ""),
            "api_base": str(self._cfg("api_base", "") or ""),
            "model": str(self._cfg("model", "") or ""),
            "timeout": self._cfg_float("timeout", 180.0),
            "poll_interval": self._cfg_float("poll_interval", 2.0),
            "request_timeout": self._cfg_float("request_timeout", 60.0),
            "send_negative_prompt": self._cfg_bool("send_negative_prompt", False),
            "response_format": str(self._cfg("response_format", "") or ""),
        }

    def _load_providers(self) -> tuple[list[ImageProvider], list[str]]:
        raw = self._cfg("providers_json", "")
        errors: list[str] = []
        items: list[dict] = []

        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"providers_json 不是合法 JSON：{exc}")
                parsed = None
            if isinstance(parsed, dict):
                if isinstance(parsed.get("providers"), list):
                    items = parsed["providers"]
                else:
                    items = [parsed]
            elif isinstance(parsed, list):
                items = [item for item in parsed if isinstance(item, dict)]
            elif parsed is not None:
                errors.append("providers_json 必须是对象或对象数组")
        elif isinstance(raw, list):
            items = [item for item in raw if isinstance(item, dict)]

        if not items:
            items = [self._legacy_provider_dict()]

        providers: list[ImageProvider] = []
        for index, item in enumerate(items):
            try:
                providers.append(ImageProvider.from_dict(item, index))
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

        names: set[str] = set()
        for provider in providers:
            if provider.name in names:
                errors.append(f"服务商重名：{provider.name}，后一个已忽略")
                continue
            names.add(provider.name)

        unique: list[ImageProvider] = []
        seen: set[str] = set()
        for provider in providers:
            if provider.name in seen:
                continue
            seen.add(provider.name)
            unique.append(provider)
        return unique, errors

    def _resolve_dashscope_key(self) -> str:
        env_key = os.environ.get("DASHSCOPE_API_KEY", "").strip().lstrip("\ufeff")
        if env_key:
            return env_key

        cfg_path = Path(
            str(self._cfg("cmd_config_path", "/AstrBot/data/cmd_config.json"))
        )
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            raise ImageError(
                f"没有配置 DashScope API Key，且读取 AstrBot 配置失败：{exc}"
            ) from exc

        raw_providers = (
            data.get("providers")
            or data.get("provider")
            or data.get("provider_sources")
            or []
        )
        if isinstance(raw_providers, dict):
            raw_providers = list(raw_providers.values())

        candidates: list[tuple[bool, str]] = []
        for item in raw_providers:
            if not isinstance(item, dict):
                continue
            provider_id = str(item.get("id") or "")
            provider_type = str(item.get("type") or "")
            is_dashscope = (
                "dashscope" in provider_id.lower()
                or "dashscope" in provider_type.lower()
                or "百炼" in provider_id
            )
            for field in ("embedding_api_key", "api_key", "key"):
                value = item.get(field)
                if isinstance(value, str):
                    value = value.strip().lstrip("\ufeff")
                    if value.startswith("sk-"):
                        candidates.append((is_dashscope, value))

        for preferred, value in candidates:
            if preferred:
                return value
        if candidates:
            return candidates[0][1]

        raise ImageError(
            "没有找到 DashScope API Key。请在 providers_json 里填 api_key，"
            "或确认 AstrBot 模型配置里有 dashscope 提供商。"
        )

    def _provider_key(self, provider: ImageProvider) -> str:
        if provider.api_key:
            return provider.api_key
        env_name = provider.api_key_env
        if not env_name and provider.type == "dashscope":
            env_name = "DASHSCOPE_API_KEY"
        if env_name:
            value = os.environ.get(env_name, "").strip().lstrip("\ufeff")
            if value:
                return value
        if provider.type == "dashscope":
            return self._resolve_dashscope_key()
        return ""

    def _make_client(self, provider: ImageProvider) -> ImageClient:
        key = self._provider_key(provider)
        if not key and not provider.headers:
            raise ImageError(
                f"服务商 {provider.name} 没有 API Key；"
                "请在 providers_json 里填 api_key 或设置 api_key_env"
            )
        runtime = replace(provider, api_key=key)
        return ImageClient(
            runtime,
            timeout=self._cfg_float("timeout", 180.0),
            poll_interval=self._cfg_float("poll_interval", 2.0),
        )

    def _ordered_providers(self, preferred: str = "") -> list[ImageProvider]:
        providers, errors = self._load_providers()
        if not providers:
            detail = "；".join(errors) if errors else "providers_json 为空"
            raise ImageError(f"没有可用的生图服务商：{detail}")

        wanted = (preferred or str(self._cfg("active_provider", ""))).strip()
        selected: list[ImageProvider] = []
        if wanted:
            selected = [item for item in providers if item.name == wanted]
            if not selected:
                logger.warning("找不到服务商 %s，改用默认服务商", wanted)
                selected = [providers[0]]
        else:
            selected = [providers[0]]

        if self._cfg_bool("provider_fallback", True):
            names = {item.name for item in selected}
            selected.extend(item for item in providers if item.name not in names)
        return selected

    # ------------------------------------------------------------------ prompt
    def _normalize_size(self, raw: str) -> str:
        default = str(self._cfg("default_size", "1024*1024")).strip() or "1024*1024"
        text = (raw or "").strip().lower().replace("×", "*").replace("x", "*")
        text = SIZE_PRESETS.get(text, text)
        match = re.fullmatch(r"(\d{3,4})\*(\d{3,4})", text)
        if not match:
            return default
        width, height = int(match.group(1)), int(match.group(2))
        if not (256 <= width <= 2048 and 256 <= height <= 2048):
            return default
        return f"{width}*{height}"

    def _compose_prompt(self, prompt: str, style: str = "") -> str:
        text = re.sub(r"\s+", " ", str(prompt or "")).strip()
        style_text = (style or str(self._cfg("style_prompt", ""))).strip()
        if style_text:
            text = f"{text}，{style_text}" if text else style_text
        max_chars = max(100, self._cfg_int("max_prompt_chars", 800))
        if len(text) > max_chars:
            text = text[:max_chars].rstrip("，,。.；; ")
        return text

    @staticmethod
    def _parse_flags(text: str) -> tuple[str, str, str, str]:
        tokens = str(text or "").split()
        prompt_parts: list[str] = []
        size = ""
        style = ""
        provider = ""
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in {"--size", "-s"} and index + 1 < len(tokens):
                size = tokens[index + 1]
                index += 2
                continue
            if token in {"--style", "-st"} and index + 1 < len(tokens):
                style = tokens[index + 1]
                index += 2
                continue
            if token in {"--provider", "-p"} and index + 1 < len(tokens):
                provider = tokens[index + 1]
                index += 2
                continue
            if token.startswith("--size="):
                size = token.split("=", 1)[1]
                index += 1
                continue
            if token.startswith("--style="):
                style = token.split("=", 1)[1]
                index += 1
                continue
            if token.startswith("--provider="):
                provider = token.split("=", 1)[1]
                index += 1
                continue
            prompt_parts.append(token)
            index += 1
        return " ".join(prompt_parts).strip(), size.strip(), style.strip(), provider.strip()

    def _check_cooldown(self, event: AstrMessageEvent) -> float:
        cooldown = self._cfg_int("cooldown_seconds", 8)
        if cooldown <= 0:
            return 0.0
        user_id = str(event.get_sender_id() or "unknown")
        now = time.monotonic()
        last = self._last_call.get(user_id, 0.0)
        if now - last < cooldown:
            return cooldown - (now - last)
        self._last_call[user_id] = now
        return 0.0

    async def _remember_umo(
        self, event: AstrMessageEvent, persona_id: str = ""
    ) -> None:
        umo = getattr(event, "unified_msg_origin", "") or ""
        if not umo:
            return
        try:
            await self.put_kv_data("last_umo", str(umo))
        except Exception:  # noqa: BLE001
            logger.debug("记录 last_umo 失败", exc_info=True)
        updates: dict[str, str] = {"last_umo": str(umo)}
        if persona_id:
            updates["last_persona_id"] = str(persona_id)
        self._write_state(**updates)

    # ------------------------------------------------------------------ local state
    def _read_state(self) -> dict:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _write_state(self, **updates) -> None:
        try:
            data = self._read_state()
            data.update(updates)
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self._state_path)
        except Exception:  # noqa: BLE001
            logger.debug("写入本地状态失败", exc_info=True)

    async def _last_schedule_date(self) -> str:
        try:
            value = await self.get_kv_data("last_schedule_date", "")
        except Exception:  # noqa: BLE001
            value = ""
        if value:
            return str(value)
        return str(self._read_state().get("last_schedule_date") or "")

    # ------------------------------------------------------------------ drawing
    async def _generate(
        self,
        prompt: str,
        size: str = "",
        style: str = "",
        provider_name: str = "",
        reference_images: list[str] | None = None,
        persona_id: str | None = None,
        keep_character: bool = False,
    ) -> tuple[Path, str]:
        providers = self._ordered_providers(provider_name)
        prompt_text = self._compose_prompt(prompt, style)
        pid = str(persona_id or "").strip() or "default"
        refs = (
            reference_images
            if reference_images is not None
            else self._auto_refs(pid, keep_character)
        )
        mode = self._reference_mode()
        use_character = bool(refs) or keep_character or mode == "off"
        character_prompt = self._character_prompt(pid) if use_character else ""
        if character_prompt:
            prompt_text = f"{character_prompt}。{prompt_text}" if prompt_text else character_prompt
        size_text = self._normalize_size(size)
        negative_prompt = str(self._cfg("negative_prompt", ""))
        errors: list[str] = []

        async with self._semaphore:
            for provider in providers:
                try:
                    client = self._make_client(provider)
                    image_ref = await client.generate(
                        prompt=prompt_text,
                        size=size_text,
                        negative_prompt=negative_prompt,
                        watermark=self._cfg_bool("watermark", False),
                        prompt_extend=self._cfg_bool("prompt_extend", False),
                        enable_thinking=self._cfg_bool("enable_thinking", False),
                        reference_images=refs,
                    )
                    name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
                    path = await client.download(image_ref, self._cache_dir / name)
                    if not path.is_file() or path.stat().st_size < 512:
                        raise ImageError("生成的图片文件为空")
                    self._cleanup_cache()
                    if provider is not providers[0]:
                        logger.info("主服务商失败，已回退到 %s", provider.name)
                    return path, provider.name
                except Exception as exc:  # noqa: BLE001
                    text = str(exc).strip() or exc.__class__.__name__
                    errors.append(f"{provider.name}: {text[:240]}")
                    logger.warning(
                        "服务商 %s 生图失败：%s", provider.name, text[:500]
                    )

        raise ImageError("所有服务商都失败了：" + "；".join(errors))

    def _cleanup_cache(self) -> None:
        max_keep = max(10, self._cfg_int("max_images_keep", 200))
        try:
            files = [p for p in self._cache_dir.glob("*") if p.is_file()]
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for path in files[max_keep:]:
                try:
                    path.unlink()
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            logger.debug("清理生图缓存失败", exc_info=True)

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        text = str(exc).strip() or exc.__class__.__name__
        return text[:300]

    async def _send_progress(self, event: AstrMessageEvent, text: str) -> None:
        if not self._cfg_bool("send_progress_hint", True):
            return
        try:
            await event.send(MessageChain().message(text))
        except Exception:  # noqa: BLE001
            logger.debug("发送进度提示失败", exc_info=True)

    async def _send_generated_image(
        self, event: AstrMessageEvent, path: Path, caption: str
    ) -> bool:
        """主动把生成图发到当前会话。

        工具调用时不能只依赖 tool_direct_result：部分 AstrBot/平台组合会
        静默丢掉工具结果链。这里直接走 context.send_message（和定时日程图
        同一条已验证的发送路径），失败再由调用方回退到工具结果。
        """
        chain = MessageChain()
        if caption:
            chain.message(caption)
        chain.chain.append(Image.fromFileSystem(str(path)))
        try:
            ok = await self.context.send_message(event.unified_msg_origin, chain)
            logger.info(
                "生图直接发送：umo=%s ok=%s path=%s",
                event.unified_msg_origin,
                ok,
                path,
            )
            return ok is not False
        except Exception:  # noqa: BLE001
            logger.exception("生图直接发送失败：%s", path)
            return False

    async def _handle_draw(
        self,
        event: AstrMessageEvent,
        raw_prompt: str,
        size: str = "",
        style: str = "",
        provider: str = "",
        keep_character: bool = False,
        source: str = "command",
    ) -> AsyncGenerator:
        if not self._cfg_bool("enabled", True):
            yield "生图功能当前已关闭。"
            return

        remaining = self._check_cooldown(event)
        if remaining > 0:
            yield f"别急，{remaining:.0f} 秒后再画。"
            return

        raw_text = str(raw_prompt or "")
        keep = bool(keep_character) or any(
            flag in raw_text for flag in ("--角色", "--keep", "--ref")
        )
        for flag in ("--角色", "--keep", "--ref"):
            raw_text = raw_text.replace(flag, " ")

        prompt, flag_size, flag_style, flag_provider = self._parse_flags(raw_text)
        size = size or flag_size
        style = style or flag_style
        provider = provider or flag_provider
        if not prompt:
            yield (
                "想画什么？用法：/画 一只在窗台晒太阳的橘猫 "
                "[--size 768*1024] [--style 水彩] [--provider 服务商名] [--角色]"
            )
            return

        persona_id = await self._resolve_persona_id(event)
        await self._remember_umo(event, persona_id)
        await self._send_progress(
            event, str(self._cfg("progress_hint", "好，开始画了，等我一下…"))
        )

        try:
            path, used_provider = await self._generate(
                prompt,
                size,
                style,
                provider,
                persona_id=persona_id,
                keep_character=keep,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("生图失败：%s", exc)
            yield f"生图失败了：{self._friendly_error(exc)}"
            return

        caption = str(self._cfg("caption", "画好啦，看看这张怎么样～")).strip()
        if self._cfg_bool("show_provider", False):
            caption = f"{caption}\n（{used_provider}）" if caption else f"（{used_provider}）"

        if source == "tool":
            if await self._send_generated_image(event, path, caption):
                yield f"图片已经用 {used_provider} 生成并直接发送给用户了。"
                return
            logger.warning("工具直接发送失败，回退到 tool_direct_result")

        result = event.make_result()
        if caption:
            result.message(caption)
        result.file_image(str(path))
        yield result

        if source == "tool":
            yield f"图片已经用 {used_provider} 生成并直接发送给用户了。"

    # ------------------------------------------------------------------ schedule prompt
    def _tz(self) -> ZoneInfo:
        name = str(self._cfg("schedule_timezone", "Asia/Shanghai")).strip()
        try:
            return ZoneInfo(name or "Asia/Shanghai")
        except Exception:  # noqa: BLE001
            return ZoneInfo("Asia/Shanghai")

    @staticmethod
    def _weekday_cn(day: datetime) -> str:
        return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][day.weekday()]

    def _load_virtual_life_plan(self, date_str: str) -> dict:
        path = Path(
            str(
                self._cfg(
                    "virtual_life_plans_path",
                    "/AstrBot/data/plugin_data/astrbot_plugin_virtual_life/plans.json",
                )
            )
        )
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            logger.warning("读取 virtual_life plans.json 失败", exc_info=True)
            return {}

        plans = data.get("plans") if isinstance(data, dict) else None
        if not isinstance(plans, dict):
            plans = data if isinstance(data, dict) else {}
        persona = str(self._cfg("schedule_persona_name", "助手")).strip()
        plan = plans.get(f"{date_str}::{persona}")
        if isinstance(plan, dict):
            return plan
        for key, value in plans.items():
            if str(key).startswith(f"{date_str}::") and isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _format_outfit(plan: dict) -> str:
        outfit = plan.get("outfit")
        if isinstance(outfit, dict):
            summary = str(outfit.get("summary") or "").strip()
            if summary:
                return summary
            names = []
            for item in outfit.get("items") or []:
                if isinstance(item, dict) and item.get("name"):
                    names.append(str(item["name"]))
            return "、".join(names[:6])
        if isinstance(outfit, str):
            return outfit.strip()
        return ""

    @staticmethod
    def _format_timeline(plan: dict) -> str:
        pieces: list[str] = []
        for item in (plan.get("timeline") or [])[:6]:
            if not isinstance(item, dict):
                continue
            start = str(item.get("start") or "").strip()
            end = str(item.get("end") or "").strip()
            activity = str(item.get("activity") or "").strip()
            location = str(item.get("location") or "").strip()
            if not activity:
                continue
            when = f"{start}-{end} " if start or end else ""
            where = f"（{location}）" if location else ""
            pieces.append(f"{when}{activity}{where}")
        text = "；".join(pieces)
        return text[:500]

    def _build_schedule_prompt(
        self, date_str: str, persona_id: str | None = None
    ) -> tuple[str, dict]:
        configured = str(self._cfg("schedule_persona_name", "助手")).strip() or "助手"
        requested = str(persona_id or "").strip()
        persona = requested if requested and requested != "default" else configured
        theme = "日常"
        mood = "悠闲"
        outfit = "穿着舒服的日常衣服"
        timeline = "按自己的节奏慢慢过"

        if self._cfg_bool("schedule_use_virtual_life", True):
            plan = self._load_virtual_life_plan(date_str)
            if plan:
                theme = str(plan.get("theme") or theme).strip() or theme
                mood = str(plan.get("mood") or mood).strip() or mood
                outfit = self._format_outfit(plan) or outfit
                timeline = self._format_timeline(plan) or timeline

        try:
            day = datetime.strptime(date_str, "%Y-%m-%d")
            weekday = self._weekday_cn(day)
        except ValueError:
            weekday = ""

        meta = {
            "date": date_str,
            "weekday": weekday,
            "persona_name": persona,
            "theme": theme,
            "mood": mood,
            "outfit": outfit,
            "timeline": timeline,
        }

        template = str(
            self._cfg("schedule_prompt_template", DEFAULT_SCHEDULE_TEMPLATE)
        )
        for key, value in meta.items():
            template = template.replace("{" + key + "}", str(value))
        return template.strip(), meta

    def _format_schedule_caption(self, meta: dict) -> str:
        template = str(self._cfg("schedule_caption", DEFAULT_SCHEDULE_CAPTION))
        for key, value in meta.items():
            template = template.replace("{" + key + "}", str(value))
        return template.strip()[:400]

    async def _handle_schedule_image(
        self,
        event: AstrMessageEvent,
        date_str: str = "",
        provider: str = "",
        source: str = "command",
    ) -> AsyncGenerator:
        if not self._cfg_bool("enabled", True):
            yield "生图功能当前已关闭。"
            return

        remaining = self._check_cooldown(event)
        if remaining > 0:
            yield f"别急，{remaining:.0f} 秒后再来。"
            return

        date_str = (date_str or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
            date_str = datetime.now(self._tz()).date().isoformat()

        await self._remember_umo(event)
        persona_id = await self._resolve_persona_id(event)
        await self._remember_umo(event, persona_id)
        prompt, meta = self._build_schedule_prompt(date_str, persona_id)
        await self._send_progress(
            event, str(self._cfg("schedule_progress_hint", "我去翻翻今天的日程，画张图给你…"))
        )

        try:
            path, used_provider = await self._generate(
                prompt,
                str(self._cfg("schedule_size", "1024*1024")),
                str(self._cfg("schedule_style_prompt", "")),
                provider or str(self._cfg("schedule_provider", "")),
                persona_id=persona_id,
                keep_character=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("日程生图失败：%s", exc)
            yield f"日程图没画成：{self._friendly_error(exc)}"
            return

        caption = self._format_schedule_caption(meta)
        if self._cfg_bool("show_provider", False):
            caption = f"{caption}\n（{used_provider}）" if caption else f"（{used_provider}）"

        if source == "tool":
            if await self._send_generated_image(event, path, caption):
                yield f"今日日程图已经用 {used_provider} 生成并发送给用户了。"
                return
            logger.warning("日程图工具直接发送失败，回退到 tool_direct_result")

        result = event.make_result()
        if caption:
            result.message(caption)
        result.file_image(str(path))
        yield result

        if source == "tool":
            yield f"今日日程图已经用 {used_provider} 生成并发送给用户了。"

    # ------------------------------------------------------------------ scheduler
    def _schedule_enabled(self) -> bool:
        return self._cfg_bool("schedule_enabled", True) and self._cfg_bool(
            "enabled", True
        )

    def _schedule_hour_minute(self) -> tuple[int, int]:
        raw = str(self._cfg("schedule_time", "08:30")).strip()
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
        if not match:
            return 8, 30
        hour, minute = int(match.group(1)), int(match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return 8, 30
        return hour, minute

    def _today_target(self, now: datetime) -> datetime:
        hour, minute = self._schedule_hour_minute()
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _start_scheduler(self) -> None:
        if self._schedule_task and not self._schedule_task.done():
            return
        self._schedule_task = asyncio.create_task(
            self._schedule_loop(), name="character_atelier_schedule"
        )

    async def _maybe_catch_up(self) -> None:
        if not self._schedule_enabled():
            return
        if not self._cfg_bool("schedule_catch_up", True):
            return
        now = datetime.now(self._tz())
        target = self._today_target(now)
        if now < target:
            return
        window = max(0.0, self._cfg_float("schedule_catch_up_window_hours", 3.0))
        if (now - target).total_seconds() > window * 3600:
            return
        await self._run_scheduled()

    async def _schedule_loop(self) -> None:
        try:
            await self._maybe_catch_up()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("日程生图补发检查失败")

        while True:
            try:
                if not self._schedule_enabled():
                    await asyncio.sleep(60)
                    continue

                now = datetime.now(self._tz())
                target = self._today_target(now)
                if target <= now:
                    target += timedelta(days=1)
                remaining = (target - now).total_seconds()
                if remaining > 30:
                    await asyncio.sleep(30)
                    continue
                if remaining > 0:
                    await asyncio.sleep(remaining)

                await self._run_scheduled()
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("日程生图循环异常")
                await asyncio.sleep(60)

    async def _schedule_sessions(self) -> list[str]:
        raw = self._cfg("schedule_sessions", "")
        if isinstance(raw, (list, tuple, set)):
            parts = [str(item) for item in raw]
        else:
            parts = re.split(r"[\s,;，；]+", str(raw or ""))
        sessions = [part.strip() for part in parts if part.strip()]
        if not sessions:
            last = await self.get_kv_data("last_umo", "")
            if not last:
                last = self._read_state().get("last_umo") or ""
            if last:
                sessions = [str(last)]
        return list(dict.fromkeys(sessions))

    async def _run_scheduled(self) -> None:
        if not self._schedule_enabled():
            return
        now = datetime.now(self._tz())
        date_str = now.date().isoformat()
        last = await self._last_schedule_date()
        if str(last or "") == date_str:
            return

        persona_id = self._schedule_persona_id()
        prompt, meta = self._build_schedule_prompt(date_str, persona_id)
        try:
            path, used_provider = await self._generate(
                prompt,
                str(self._cfg("schedule_size", "1024*1024")),
                str(self._cfg("schedule_style_prompt", "")),
                str(self._cfg("schedule_provider", "")),
                persona_id=persona_id,
                keep_character=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("定时日程生图失败：%s", exc)
            return

        sessions = await self._schedule_sessions()
        if not sessions:
            logger.warning(
                "定时日程生图跳过：没有配置 schedule_sessions，也没有记录到会话"
            )
            return

        caption = self._format_schedule_caption(meta)
        if self._cfg_bool("show_provider", False):
            caption = f"{caption}\n（{used_provider}）" if caption else f"（{used_provider}）"
        sent = 0
        for umo in sessions:
            try:
                chain = MessageChain(
                    chain=[Plain(caption), Image.fromFileSystem(str(path))]
                )
                ok = await self.context.send_message(umo, chain)
                if ok is not False:
                    sent += 1
                    logger.info("定时日程图已发送：%s", umo)
            except Exception as exc:  # noqa: BLE001
                logger.warning("定时日程图发送失败：%s -> %s", umo, exc)

        if sent:
            try:
                await self.put_kv_data("last_schedule_date", date_str)
            except Exception:  # noqa: BLE001
                logger.debug("写入 last_schedule_date 失败", exc_info=True)
            self._write_state(last_schedule_date=date_str)

    # ------------------------------------------------------------------ proactive
    def _start_proactive(self) -> None:
        if self._proactive_task and not self._proactive_task.done():
            return
        state = self._read_state()
        if not state.get("last_proactive_ts"):
            state["last_proactive_ts"] = int(time.time())
            self._write_state(**state)
        self._proactive_task = asyncio.create_task(
            self._proactive_loop(), name="character_atelier_proactive"
        )

    async def _proactive_loop(self) -> None:
        while True:
            try:
                state = self._read_state()
                persona_id = str(state.get("last_persona_id") or "default")
                profile = self._read_character(persona_id)
                if not profile.get("proactive_enabled"):
                    await asyncio.sleep(120)
                    continue
                if not (
                    str(profile.get("name") or "").strip()
                    or str(profile.get("description") or "").strip()
                    or any(
                        self._character_image_path(slot, persona_id)
                        for slot in self.CHARACTER_SLOTS
                    )
                ):
                    await asyncio.sleep(300)
                    continue

                now = datetime.now(self._tz())
                start_h = max(0, min(23, int(profile.get("proactive_start_hour", 9))))
                end_h = max(1, min(24, int(profile.get("proactive_end_hour", 23))))
                if not (start_h <= now.hour < end_h):
                    await asyncio.sleep(300)
                    continue

                state = self._read_state()
                today = now.date().isoformat()
                if str(state.get("proactive_date") or "") != today:
                    state["proactive_date"] = today
                    state["proactive_count"] = 0
                    self._write_state(**state)

                limit = max(0, int(profile.get("proactive_daily_limit", 2)))
                if int(state.get("proactive_count") or 0) >= limit:
                    await asyncio.sleep(600)
                    continue

                interval = max(30, int(profile.get("proactive_interval_minutes", 180)))
                last_ts = float(state.get("last_proactive_ts") or 0)
                next_ts = last_ts + interval * 60
                if time.time() < next_ts:
                    await asyncio.sleep(min(60, max(5, next_ts - time.time())))
                    continue

                sessions = await self._schedule_sessions()
                if not sessions:
                    await asyncio.sleep(600)
                    continue

                scenes = profile.get("proactive_scenes") or []
                scene = random.choice(
                    scenes
                    or [
                        "坐在窗台晒太阳，旁边放着一杯柠檬水",
                        "在厨房里笨手笨脚地做早餐",
                    ]
                )
                prompt = (
                    f"角色日常照片：{scene}。自然抓拍感，生活化，不要摆拍，不要文字水印。"
                )
                path, used = await self._generate(
                    prompt,
                    str(self._cfg("proactive_size", "1024*1024")),
                    str(self._cfg("proactive_style_prompt", "")),
                    str(self._cfg("proactive_provider", "")),
                    persona_id=persona_id,
                    keep_character=True,
                )
                caption = str(
                    self._cfg("proactive_caption", "突然想给你看看这个——")
                ).strip()
                if self._cfg_bool("show_provider", False):
                    caption = (
                        f"{caption}\n（{used}）" if caption else f"（{used}）"
                    )

                sent = 0
                for umo in sessions:
                    try:
                        chain = MessageChain(
                            chain=[Plain(caption), Image.fromFileSystem(str(path))]
                        )
                        ok = await self.context.send_message(umo, chain)
                        if ok is not False:
                            sent += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("主动画图发送失败：%s -> %s", umo, exc)

                if sent:
                    state = self._read_state()
                    state["last_proactive_ts"] = int(time.time())
                    state["proactive_count"] = int(state.get("proactive_count") or 0) + 1
                    state["proactive_date"] = today
                    self._write_state(**state)
                    logger.info("主动画图已发送 %s 个会话", sent)
                else:
                    state = self._read_state()
                    state["last_proactive_ts"] = int(time.time())
                    self._write_state(**state)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("主动画图循环异常")
                await asyncio.sleep(120)

    # ------------------------------------------------------------------ LLM guide
    def _provider_lines(self) -> str:
        try:
            providers, _ = self._load_providers()
        except Exception:  # noqa: BLE001
            return "尚未配置"
        if not providers:
            return "尚未配置"
        active = str(self._cfg("active_provider", "")).strip()
        lines = []
        for item in providers:
            mark = "（默认）" if item.name == active or (not active and item is providers[0]) else ""
            lines.append(f"{item.name}{mark}[{item.type}/{item.model}]")
        return "、".join(lines)

    def _guide_template(self) -> str:
        """builtin：始终用内置提示词（更新插件即更新提示词）；custom：用配置里的自定义版本。"""
        source = str(self._cfg("llm_guide_source", "builtin")).strip().lower()
        if source == "custom":
            configured = str(self._cfg("llm_guide_text", "") or "").strip()
            if configured:
                return configured
            logger.warning(
                "llm_guide_source=custom 但 llm_guide_text 为空，已回退到内置提示词"
            )
        return DEFAULT_LLM_GUIDE

    def _build_llm_guide(self) -> str:
        template = self._guide_template()
        if not template:
            return ""
        tool_draw = "image_draw"
        tool_schedule = "image_schedule"
        replacements = {
            "{providers}": self._provider_lines(),
            "{default_size}": str(self._cfg("default_size", "1024*1024")),
            "{tool_draw}": tool_draw,
            "{tool_schedule}": tool_schedule,
        }
        for key, value in replacements.items():
            template = template.replace(key, value)
        return template

    @filter.on_llm_request()
    async def inject_image_guide(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        if not self._cfg_bool("enabled", True):
            return
        if not self._cfg_bool("llm_guide_enabled", True):
            return
        guide = self._build_llm_guide()
        if not guide:
            return
        current = str(getattr(req, "system_prompt", "") or "")
        marker = "【生图工具使用说明】"
        if marker in current:
            return
        req.system_prompt = f"{current}\n\n{guide}".strip()

    # ------------------------------------------------------------------ commands
    @filter.command("画", alias={"draw", "生图", "画图"})
    async def draw_command(self, event: AstrMessageEvent, prompt: GreedyStr):
        """让角色画一张图。用法：/画 一只在窗台晒太阳的橘猫 [--size 768*1024] [--style 水彩] [--provider 服务商名]"""
        async for item in self._handle_draw(event, str(prompt), source="command"):
            yield item

    @filter.command("日程图", alias={"今日日程图", "scheduleimage"})
    async def schedule_command(self, event: AstrMessageEvent, date: GreedyStr = ""):
        """画今天的日程插画。用法：/日程图 [YYYY-MM-DD] [--provider 服务商名]"""
        date_text, _, _, provider = self._parse_flags(str(date or ""))
        async for item in self._handle_schedule_image(
            event, date_text, provider, source="command"
        ):
            yield item

    @filter.command("生图状态", alias={"画图状态"})
    async def status_command(self, event: AstrMessageEvent):
        """查看多服务商生图配置与今日是否已发日程图。"""
        try:
            providers, errors = self._load_providers()
        except Exception as exc:  # noqa: BLE001
            providers, errors = [], [str(exc)]
        active = str(self._cfg("active_provider", "")).strip()
        lines = []
        for provider in providers:
            try:
                key_ok = bool(self._provider_key(provider))
            except Exception:  # noqa: BLE001
                key_ok = False
            mark = " ←默认" if provider.name == active or (not active and provider is providers[0]) else ""
            lines.append(
                f"- {provider.name}{mark}：{provider.type}/{provider.model}，"
                f"Key {'已找到' if key_ok else '未找到'}"
            )
        if not lines:
            lines.append("- 没有有效服务商")
        try:
            last = await self._last_schedule_date()
        except Exception:  # noqa: BLE001
            last = ""
        sessions = await self._schedule_sessions()
        text = (
            f"角色画室：{'开启' if self._cfg_bool('enabled', True) else '关闭'}\n"
            f"服务商：\n" + "\n".join(lines) + "\n"
            f"回退：{'开启' if self._cfg_bool('provider_fallback', True) else '关闭'}\n"
            f"日程生图：{self._cfg('schedule_time', '08:30')} "
            f"{self._cfg('schedule_timezone', 'Asia/Shanghai')}"
            f"（{self._cfg('schedule_provider', '') or '默认服务商'}）\n"
            f"今日已发：{last or '否'}\n"
            f"推送会话：{len(sessions)} 个"
        )
        if errors:
            text += "\n配置提示：" + "；".join(errors[:3])
        yield event.make_result().message(text)

    @filter.command("生图帮助", alias={"画图帮助"})
    async def help_command(self, event: AstrMessageEvent):
        """查看角色画室的用法与服务商列表。"""
        text = (
            "【角色画室】\n"
            "/画 <描述> [--size 1024*1024] [--style 水彩] [--provider 名称]\n"
            "/日程图 [YYYY-MM-DD] [--provider 名称]\n"
            "/生图状态\n\n"
            f"可用服务商：{self._provider_lines()}\n"
            "服务商在插件配置的 providers_json 里添加，支持 dashscope、openai、gemini 三种类型。"
        )
        yield event.make_result().message(text)

    # ------------------------------------------------------------------ LLM tools
    @filter.llm_tool(name="image_draw")
    async def draw_tool(
        self,
        event: AstrMessageEvent,
        prompt: str,
        size: str = "",
        style: str = "",
        provider: str = "",
        keep_character: bool = False,
    ):
        """根据文字描述生成一张图片，并直接发送给用户。当用户说“画一张…”“生图”“来张图”“给我画…”，或者想要头像、插画、表情包、场景图时调用。

        Args:
            prompt(string): 画面描述，写清主体、动作、场景、氛围和构图；不要在这里写尺寸参数。
            size(string): 可选尺寸，例如 1024*1024、768*1024、1024*768；留空使用默认值。
            style(string): 可选风格，例如 水彩、二次元、写实、像素风；留空使用默认风格。
            provider(string): 可选服务商名称；只有用户点名服务商时才填，留空使用默认服务商。
            keep_character(bool): 是否强制附带角色三视图参考图；用户要求“保持角色长相/还是她”时填 true，其余留空。
        """
        async for item in self._handle_draw(
            event,
            prompt,
            size,
            style,
            provider,
            keep_character=keep_character,
            source="tool",
        ):
            yield item

    @filter.llm_tool(name="image_schedule")
    async def schedule_tool(
        self,
        event: AstrMessageEvent,
        date: str = "",
        provider: str = "",
    ):
        """当用户想看“今天的日程图”“今日安排插画”“把今天的计划画出来”时调用，生成带当天主题、心情、穿搭和日程片段的插画并直接发送。

        Args:
            date(string): 可选日期，格式 YYYY-MM-DD；留空表示今天。
            provider(string): 可选服务商名称；留空使用日程图配置或默认服务商。
        """
        async for item in self._handle_schedule_image(
            event, date, provider, source="tool"
        ):
            yield item
