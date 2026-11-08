from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote_to_bytes

import httpx


class ImageError(RuntimeError):
    """生图失败。str(exc) 是可直接展示给用户的简短说明。"""


TYPE_ALIASES = {
    "qwen": "dashscope",
    "bailian": "dashscope",
    "aliyun": "dashscope",
    "dashscope": "dashscope",
    "openai": "openai",
    "openai-compatible": "openai",
    "openai_compatible": "openai",
    "compatible": "openai",
    "custom": "openai",
    "gemini": "gemini",
    "google": "gemini",
    "google-genai": "gemini",
    "google_genai": "gemini",
}

DEFAULT_BASES = {
    "dashscope": "https://dashscope.aliyuncs.com/api/v1",
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}

DEFAULT_MODELS = {
    "dashscope": "qwen-image-2.1-turbo",
    "openai": "gpt-image-1",
    "gemini": "gemini-2.5-flash-image",
}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "是"}


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:  # noqa: BLE001
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_str_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[\s,;，；]+", value)
    elif isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        return default
    result = [part.strip() for part in parts if part.strip()]
    return result or default


@dataclass
class ImageProvider:
    """一个图片服务商的配置。"""

    name: str
    type: str = "dashscope"
    api_key: str = ""
    api_base: str = ""
    model: str = ""
    enabled: bool = True
    timeout: float | None = None
    poll_interval: float | None = None
    api_key_env: str = ""
    extra_body: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    size_map: dict = field(default_factory=dict)
    response_format: str = ""
    send_negative_prompt: bool = False
    response_modalities: list[str] = field(
        default_factory=lambda: ["TEXT", "IMAGE"]
    )
    send_aspect_ratio: bool = True
    request_timeout: float = 60.0

    @classmethod
    def from_dict(cls, raw: dict, index: int = 0) -> "ImageProvider":
        if not isinstance(raw, dict):
            raise ImageError(f"第 {index + 1} 个服务商不是 JSON 对象")

        raw_type = str(
            raw.get("type") or raw.get("provider_type") or "openai"
        ).strip().lower()
        kind = TYPE_ALIASES.get(raw_type, raw_type)
        if kind not in DEFAULT_BASES:
            raise ImageError(
                f"服务商 {raw.get('name') or index + 1} 的类型 {raw_type!r} 不支持；"
                "可用类型：dashscope、openai、gemini"
            )

        name = str(raw.get("name") or raw.get("id") or f"provider{index + 1}").strip()
        if not name:
            name = f"provider{index + 1}"

        api_key = str(raw.get("api_key") or raw.get("key") or "").strip().lstrip("\ufeff")
        api_key_env = str(raw.get("api_key_env") or "").strip()
        model = str(raw.get("model") or DEFAULT_MODELS[kind]).strip()
        api_base = str(
            raw.get("api_base")
            or raw.get("base_url")
            or raw.get("base")
            or DEFAULT_BASES[kind]
        ).strip()

        headers = {
            str(key): str(value)
            for key, value in _as_dict(raw.get("headers")).items()
        }
        extra_body = _as_dict(raw.get("extra_body"))
        size_map = {
            str(key).replace("x", "*").strip(): str(value)
            for key, value in _as_dict(raw.get("size_map")).items()
        }

        return cls(
            name=name,
            type=kind,
            api_key=api_key,
            api_base=api_base,
            model=model,
            enabled=_as_bool(raw.get("enabled"), True),
            timeout=(
                _as_float(raw.get("timeout"), 0.0)
                if raw.get("timeout") is not None
                else None
            ),
            poll_interval=(
                _as_float(raw.get("poll_interval"), 0.0)
                if raw.get("poll_interval") is not None
                else None
            ),
            api_key_env=api_key_env,
            extra_body=extra_body,
            headers=headers,
            size_map=size_map,
            response_format=str(raw.get("response_format") or "").strip(),
            send_negative_prompt=_as_bool(raw.get("send_negative_prompt"), False),
            response_modalities=_as_str_list(
                raw.get("response_modalities"), ["TEXT", "IMAGE"]
            ),
            send_aspect_ratio=_as_bool(raw.get("send_aspect_ratio"), True),
            request_timeout=_as_float(raw.get("request_timeout"), 60.0),
        )

    def public_info(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "model": self.model,
            "api_base": self.api_base,
            "enabled": self.enabled,
        }


class ImageClient:
    """多服务商图片客户端。

    支持三种协议：
      * dashscope：阿里云百炼原生异步接口（qwen-image 系列）
      * openai：OpenAI /images/generations 及所有兼容实现
      * gemini：Google Generative Language generateContent
    """

    def __init__(
        self,
        provider: ImageProvider,
        timeout: float = 180.0,
        poll_interval: float = 2.0,
    ) -> None:
        self.provider = provider
        self.api_key = (provider.api_key or "").strip().lstrip("\ufeff")
        if not self.api_key and provider.api_key_env:
            self.api_key = os.environ.get(provider.api_key_env, "").strip().lstrip(
                "\ufeff"
            )
        self.api_base = (provider.api_base or "").strip().rstrip("/")
        self.model = (provider.model or "").strip()
        self.timeout = max(10.0, float(provider.timeout or timeout))
        self.poll_interval = max(0.5, float(provider.poll_interval or poll_interval))
        self.request_timeout = max(10.0, float(provider.request_timeout or 60.0))

    async def generate(
        self,
        prompt: str,
        size: str = "1024*1024",
        negative_prompt: str = "",
        watermark: bool = False,
        prompt_extend: bool = False,
        enable_thinking: bool = False,
        seed: int | None = None,
        reference_images: list[str] | None = None,
    ) -> str:
        """生成图片，返回图片 URL 或 data: URI。"""
        if not prompt.strip():
            raise ImageError("画面描述不能为空")
        if not self.model:
            raise ImageError(f"{self.provider.name} 没有配置模型名")
        if not self.api_key and not self.provider.headers:
            raise ImageError(
                f"{self.provider.name} 没有 API Key；"
                "请在 providers_json 里填 api_key，或设置对应的环境变量"
            )

        try:
            if self.provider.type == "dashscope":
                return await self._dashscope(
                    prompt,
                    size,
                    negative_prompt,
                    watermark,
                    prompt_extend,
                    enable_thinking,
                    seed,
                    reference_images or [],
                )
            if self.provider.type == "openai":
                # OpenAI 兼容的 /images/generations 不接受参考图；
                # 角色描述仍会写进 prompt，保证至少不丢角色设定。
                return await self._openai(
                    prompt, size, negative_prompt, watermark
                )
            if self.provider.type == "gemini":
                return await self._gemini(
                    prompt, size, negative_prompt, reference_images or []
                )
            raise ImageError(f"未知服务商类型：{self.provider.type}")
        except ImageError:
            raise
        except httpx.TimeoutException as exc:
            raise ImageError(
                f"{self.provider.name} 请求超时（{int(self.timeout)} 秒）"
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageError(f"{self.provider.name} 网络请求失败：{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ImageError(f"{self.provider.name} 生图异常：{exc}") from exc

    # ------------------------------------------------------------------ headers
    def _headers(self, **extra: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AstrBot-Multi-Image/2.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.provider.headers)
        headers.update(extra)
        return headers

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}
        except Exception:  # noqa: BLE001
            return {"message": (response.text or "")[:800]}

    def _describe_error(self, status: int, data: dict) -> str:
        error = data.get("error")
        code = ""
        message = ""
        if isinstance(error, dict):
            code = str(error.get("code") or error.get("type") or "")
            message = str(error.get("message") or "")
        elif error:
            message = str(error)
        code = str(data.get("code") or code or "")
        message = str(data.get("message") or message or "")
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        code = str(output.get("code") or code or "")
        message = str(output.get("message") or message or "")

        parts = [f"{self.provider.name} HTTP {status}"]
        if code:
            parts.append(code)
        if message:
            parts.append(message[:300])
        text = " | ".join(parts)
        if status in (401, 403):
            text += "（检查 API Key、账号权限和模型是否已开通）"
        return text

    # ------------------------------------------------------------------ dashscope
    @staticmethod
    def _dashscope_base(api_base: str) -> str:
        base = (api_base or "").strip().rstrip("/")
        if not base:
            base = DEFAULT_BASES["dashscope"]
        if base.endswith("/compatible-mode/v1"):
            return base[: -len("/compatible-mode/v1")] + "/api/v1"
        if base.endswith("/api/v1"):
            return base
        if base.endswith("/services/aigc/image-generation/generation"):
            return base.split("/services/", 1)[0]
        return base + "/api/v1"

    async def _dashscope(
        self,
        prompt: str,
        size: str,
        negative_prompt: str,
        watermark: bool,
        prompt_extend: bool,
        enable_thinking: bool,
        seed: int | None,
        reference_images: list[str],
    ) -> str:
        base = self._dashscope_base(self.api_base)
        url = f"{base}/services/aigc/image-generation/generation"
        parameters: dict = {
            "size": size or "1024*1024",
            "n": 1,
            "watermark": bool(watermark),
        }
        if negative_prompt.strip():
            parameters["negative_prompt"] = negative_prompt.strip()
        if prompt_extend:
            parameters["prompt_extend"] = True
        if enable_thinking:
            parameters["enable_thinking"] = True
        if seed is not None:
            parameters["seed"] = int(seed)
        parameters.update(self.provider.extra_body)

        content: list[dict] = [
            {"image": ref} for ref in reference_images if ref
        ]
        content.append({"text": prompt.strip()})
        payload = {
            "model": self.model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": parameters,
        }

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=20.0),
            follow_redirects=True,
        ) as client:
            response = await client.post(
                url,
                headers=self._headers(**{"X-DashScope-Async": "enable"}),
                json=payload,
            )
            data = self._json(response)
            if response.status_code >= 400 or data.get("code"):
                if response.status_code == 400 and any(
                    key in json.dumps(data, ensure_ascii=False)
                    for key in ("prompt_extend", "enable_thinking", "negative_prompt")
                ):
                    return await self._dashscope_minimal(
                        client, url, prompt, size, reference_images
                    )
                raise ImageError(self._describe_error(response.status_code, data))

            output = data.get("output") or {}
            task_id = output.get("task_id") or data.get("task_id")
            if not task_id:
                raise ImageError(
                    "提交生图任务失败：响应里没有 task_id。"
                    + self._describe_error(response.status_code, data)
                )
            return await self._dashscope_wait(client, str(task_id))

    async def _dashscope_minimal(
        self,
        client: httpx.AsyncClient,
        url: str,
        prompt: str,
        size: str,
        reference_images: list[str],
    ) -> str:
        content: list[dict] = [
            {"image": ref} for ref in reference_images if ref
        ]
        content.append({"text": prompt.strip()})
        minimal = {
            "model": self.model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {"size": size or "1024*1024", "n": 1, "watermark": False},
        }
        response = await client.post(
            url,
            headers=self._headers(**{"X-DashScope-Async": "enable"}),
            json=minimal,
        )
        data = self._json(response)
        output = data.get("output") or {}
        task_id = output.get("task_id") or data.get("task_id")
        if response.status_code >= 400 or data.get("code") or not task_id:
            raise ImageError(self._describe_error(response.status_code, data))
        return await self._dashscope_wait(client, str(task_id))

    async def _dashscope_wait(self, client: httpx.AsyncClient, task_id: str) -> str:
        deadline = time.monotonic() + self.timeout
        last_status = ""
        base = self._dashscope_base(self.api_base)
        url = f"{base}/tasks/{task_id}"
        while time.monotonic() < deadline:
            await asyncio.sleep(self.poll_interval)
            response = await client.get(url, headers=self._headers())
            data = self._json(response)
            if response.status_code >= 400 or data.get("code"):
                raise ImageError(self._describe_error(response.status_code, data))
            output = data.get("output") or {}
            status = str(
                output.get("task_status") or data.get("task_status") or ""
            )
            last_status = status or last_status
            if status == "SUCCEEDED":
                return self._extract_dashscope_image(output)
            if status in {"FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}:
                detail = (
                    output.get("message")
                    or data.get("message")
                    or output.get("code")
                    or data.get("code")
                    or "任务未成功"
                )
                raise ImageError(f"生图任务失败（{status}）：{detail}")
        raise ImageError(
            f"生图超时（{int(self.timeout)} 秒，最后状态 {last_status or '未知'}），"
            f"task_id={task_id}"
        )

    @staticmethod
    def _extract_dashscope_image(output: dict) -> str:
        for choice in output.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or {}
            for item in message.get("content") or []:
                if isinstance(item, dict):
                    image = (
                        item.get("image")
                        or item.get("image_url")
                        or item.get("url")
                    )
                    if image:
                        return str(image)
        for item in output.get("results") or []:
            if isinstance(item, dict):
                image = item.get("url") or item.get("image") or item.get("image_url")
                if image:
                    return str(image)
        image = output.get("image_url") or output.get("url")
        if image:
            return str(image)
        raise ImageError(
            "任务成功了，但响应里找不到图片 URL："
            + json.dumps(output, ensure_ascii=False)[:400]
        )

    # ------------------------------------------------------------------ openai
    def _openai_url(self) -> str:
        base = self.api_base or DEFAULT_BASES["openai"]
        if base.endswith("/images/generations"):
            return base
        if re.search(r"/v\d+[a-z]*$", base, re.IGNORECASE):
            return base + "/images/generations"
        if not re.search(r"/v\d+", base, re.IGNORECASE):
            return base + "/v1/images/generations"
        return base + "/images/generations"

    _OPENAI_KEYS = ("url", "image_url", "image", "b64_json", "base64", "data")

    async def _openai(
        self,
        prompt: str,
        size: str,
        negative_prompt: str,
        watermark: bool,
    ) -> str:
        url = self._openai_url()
        body: dict = {
            "model": self.model,
            "prompt": prompt.strip(),
            "n": 1,
            "size": self._openai_size(size),
        }
        if self.provider.response_format:
            body["response_format"] = self.provider.response_format
        if self.provider.send_negative_prompt and negative_prompt.strip():
            body["negative_prompt"] = negative_prompt.strip()
        if watermark:
            body["watermark"] = True
        body.update(self.provider.extra_body)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=20.0),
            follow_redirects=True,
        ) as client:
            response = await client.post(url, headers=self._headers(), json=body)
            content_type = response.headers.get("content-type", "")
            if response.status_code < 400 and content_type.startswith("image/"):
                return self._data_uri(response.content, content_type)
            data = self._json(response)
            if response.status_code >= 400:
                raise ImageError(self._describe_error(response.status_code, data))
            return self._extract_openai_image(data)

    def _openai_size(self, size: str) -> str:
        canonical = (size or "1024*1024").strip().replace("x", "*")
        if canonical in self.provider.size_map:
            return self.provider.size_map[canonical]
        return canonical.replace("*", "x")

    def _extract_openai_image(self, data: dict) -> str:
        items: list[Any] = []
        for key in ("data", "images", "output", "results"):
            value = data.get(key)
            if isinstance(value, list):
                items.extend(value)
            elif isinstance(value, dict):
                for child_key in ("results", "images", "data"):
                    child = value.get(child_key)
                    if isinstance(child, list):
                        items.extend(child)
        if not items and isinstance(data.get("data"), dict):
            items.append(data["data"])

        for item in items:
            if isinstance(item, str):
                if item.startswith(("http://", "https://", "data:")):
                    return item
                if len(item) > 512:
                    return self._data_uri_from_b64(item)
                continue
            if not isinstance(item, dict):
                continue
            for key in self._OPENAI_KEYS:
                value = item.get(key)
                if not isinstance(value, str) or not value.strip():
                    continue
                value = value.strip()
                if key in ("b64_json", "base64") or (
                    key == "data" and len(value) > 512
                ):
                    return self._data_uri_from_b64(value)
                if value.startswith(("http://", "https://", "data:")):
                    return value
        raise ImageError(
            "接口返回成功，但响应里没有图片。"
            + json.dumps(data, ensure_ascii=False)[:500]
        )

    # ------------------------------------------------------------------ gemini
    def _gemini_url(self) -> str:
        base = self.api_base or DEFAULT_BASES["gemini"]
        if ":generateContent" in base:
            return base
        if "/models/" in base:
            return base + ":generateContent"
        return f"{base}/models/{quote(self.model)}:generateContent"

    async def _gemini(
        self,
        prompt: str,
        size: str,
        negative_prompt: str,
        reference_images: list[str],
    ) -> str:
        url = self._gemini_url()
        text = prompt.strip()
        if negative_prompt.strip():
            text += f"\n避免出现：{negative_prompt.strip()}"
        parts: list[dict] = []
        for ref in reference_images:
            split = self._split_data_uri(ref)
            if split:
                mime, payload = split
                parts.append(
                    {"inlineData": {"mimeType": mime, "data": payload}}
                )
            elif ref.startswith(("http://", "https://")):
                parts.append({"fileData": {"fileUri": ref, "mimeType": "image/png"}})
        parts.append({"text": text})
        body: dict = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": self.provider.response_modalities,
            },
        }
        aspect = self._aspect_ratio(size)
        if self.provider.send_aspect_ratio and aspect:
            body["generationConfig"]["imageConfig"] = {"aspectRatio": aspect}

        headers = self._headers(**{"x-goog-api-key": self.api_key})
        headers.pop("Authorization", None)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=20.0),
            follow_redirects=True,
        ) as client:
            response = await client.post(url, headers=headers, json=body)
            data = self._json(response)
            if response.status_code >= 400:
                # 部分模型不接受 imageConfig，去掉后重试一次
                if (
                    response.status_code == 400
                    and "imageConfig" in body.get("generationConfig", {})
                ):
                    body["generationConfig"].pop("imageConfig", None)
                    response = await client.post(url, headers=headers, json=body)
                    data = self._json(response)
                if response.status_code >= 400:
                    raise ImageError(
                        self._describe_error(response.status_code, data)
                    )
            return self._extract_gemini_image(data)

    @staticmethod
    def _aspect_ratio(size: str) -> str:
        match = re.fullmatch(r"\s*(\d{3,4})\s*[*x×]\s*(\d{3,4})\s*", size or "")
        if not match:
            return "1:1"
        width, height = int(match.group(1)), int(match.group(2))
        if not width or not height:
            return "1:1"
        ratio = width / height
        choices = {
            "1:1": 1.0,
            "3:4": 0.75,
            "4:3": 4 / 3,
            "9:16": 9 / 16,
            "16:9": 16 / 9,
        }
        return min(choices, key=lambda key: abs(ratio - choices[key]))

    @staticmethod
    def _extract_gemini_image(data: dict) -> str:
        for candidate in data.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                inline = part.get("inlineData") or part.get("inline_data")
                if isinstance(inline, dict):
                    payload = inline.get("data")
                    mime = (
                        inline.get("mimeType")
                        or inline.get("mime_type")
                        or "image/png"
                    )
                    if payload:
                        return ImageClient._data_uri_from_b64(str(payload), str(mime))
                file_data = part.get("fileData") or part.get("file_data")
                if isinstance(file_data, dict):
                    uri = file_data.get("fileUri") or file_data.get("file_uri")
                    if uri:
                        return str(uri)
        error = data.get("error")
        if error:
            raise ImageError(str(error.get("message") or error)[:300])
        raise ImageError(
            "Gemini 返回成功，但没有图片内容。"
            + json.dumps(data, ensure_ascii=False)[:500]
        )

    # ------------------------------------------------------------------ data uri
    @staticmethod
    def _data_uri(data: bytes, mime: str) -> str:
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime or 'image/png'};base64,{encoded}"

    @staticmethod
    def _data_uri_from_b64(payload: str, mime: str = "image/png") -> str:
        value = payload.strip()
        if value.startswith("data:"):
            return value
        return f"data:{mime or 'image/png'};base64,{value}"

    @staticmethod
    def _split_data_uri(uri: str) -> tuple[str, str] | None:
        """把 data URI 拆成 (mime, 纯 base64)；不是 data URI 时返回 None。"""
        text = str(uri or "").strip()
        if not text.startswith("data:") or "," not in text:
            return None
        header, payload = text.split(",", 1)
        mime = "image/png"
        if ";" in header:
            mime = header[5:].split(";", 1)[0] or mime
        if ";base64" not in header.lower():
            payload = base64.b64encode(unquote_to_bytes(payload)).decode("ascii")
        return mime, payload

    # ------------------------------------------------------------------ download
    async def download(self, ref: str, dest_without_suffix: Path) -> Path:
        """把 URL、data URI 或本地文件保存到本地，按文件头补后缀。"""
        dest_without_suffix.parent.mkdir(parents=True, exist_ok=True)
        text = str(ref or "").strip()
        if not text:
            raise ImageError("图片地址为空")

        if text.startswith("data:"):
            data = self._decode_data_uri(text)
        elif text.startswith(("http://", "https://")):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(90.0, connect=20.0),
                    follow_redirects=True,
                ) as client:
                    response = await client.get(text)
                    response.raise_for_status()
                    data = response.content
            except httpx.HTTPError as exc:
                raise ImageError(f"下载生成的图片失败：{exc}") from exc
        elif Path(text).is_file():
            data = Path(text).read_bytes()
        else:
            raise ImageError(f"无法识别的图片地址：{text[:120]}")

        if len(data) < 512:
            raise ImageError(f"图片内容异常（只有 {len(data)} 字节）")

        suffix = ".png"
        if data[:3] == b"\xff\xd8\xff":
            suffix = ".jpg"
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            suffix = ".gif"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            suffix = ".webp"
        elif data[4:12] in (b"ftypavif", b"ftypavis"):
            suffix = ".avif"

        path = dest_without_suffix.with_suffix(suffix)
        path.write_bytes(data)
        return path

    @staticmethod
    def _decode_data_uri(uri: str) -> bytes:
        header, _, payload = uri.partition(",")
        if not payload:
            raise ImageError("data URI 格式不正确")
        if ";base64" in header.lower():
            try:
                return base64.b64decode(payload, validate=False)
            except Exception as exc:  # noqa: BLE001
                raise ImageError(f"图片 base64 解码失败：{exc}") from exc
        return unquote_to_bytes(payload)
