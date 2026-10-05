"""Wrapper mỏng quanh Gemini API (SDK mới `google-genai`): sinh JSON, sinh text
thuần, và sinh text có kèm ảnh/video (multimodal) để viết script bám theo
ảnh/video thật của sản phẩm.
"""
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Optional, Sequence, Union

from google import genai
from google.genai import errors, types

from .llm_errors import LLMJSONError, LLMQuotaExceededError, LLMServerError

_VIDEO_UPLOAD_POLL_INTERVAL = 2.0
_VIDEO_UPLOAD_TIMEOUT = 120.0

# Free tier Gemini giới hạn cả theo phút (vd 5 request/phút) lẫn theo ngày. Lỗi
# theo phút tự hết sau ít giây nên retry là đủ; lỗi theo ngày thì retry vô ích
# (chờ hàng chục giây rồi vẫn 429) — phân biệt qua quotaId trong response.
_RATE_LIMIT_RETRIES = 4
_RATE_LIMIT_BACKOFF = 20.0


def _is_rate_limited(client_error: errors.ClientError) -> bool:
    return "RESOURCE_EXHAUSTED" in str(client_error)


def _is_daily_quota(client_error: errors.ClientError) -> bool:
    # quotaId thường là "...PerDay..." (hết hạn mức ngày, retry vô ích) hoặc
    # "...PerMinute..." (chỉ cần chờ ngắn rồi thử lại được).
    return "PerDay" in str(client_error)


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Thiếu GEMINI_API_KEY. Copy .env.example thành .env và điền API key "
                "(lấy tại https://aistudio.google.com/apikey)."
            )
        self.client = genai.Client(api_key=api_key)
        self.model_name = model or os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

    def _call_with_retry(self, fn):
        last_error = None
        for attempt in range(1, _RATE_LIMIT_RETRIES + 1):
            try:
                return fn()
            except errors.ClientError as e:
                if not _is_rate_limited(e):
                    # Lỗi client khác (400/404/413...) không phải rate-limit —
                    # cùng lỗ hổng từng gặp thật bên GroqClient (413 "Request
                    # too large" khi payload quá lớn). Retry vô ích vì cùng 1
                    # request sẽ luôn lỗi y hệt, nên bọc lại rõ ràng thay vì
                    # để traceback gốc bắn thẳng ra ngoài.
                    raise LLMServerError(
                        f"Gemini trả lỗi client (không phải rate-limit): {e}. Retry sẽ không giúp được — "
                        "kiểm tra lại dữ liệu/kích thước request (ảnh/video đính kèm có thể quá lớn) "
                        "hoặc cấu hình API key."
                    ) from e
                if _is_daily_quota(e):
                    raise LLMQuotaExceededError(
                        "Hết quota Gemini theo NGÀY (free tier). Đợi reset (thường theo giờ UTC lúc "
                        "nửa đêm Thái Bình Dương) hoặc bật billing tại Google AI Studio. "
                        f"Chi tiết: {e}"
                    ) from e
                last_error = e
                if attempt < _RATE_LIMIT_RETRIES:
                    print(f"  [rate limit] Vượt giới hạn request/phút, chờ {_RATE_LIMIT_BACKOFF:.0f}s rồi thử lại...")
                    time.sleep(_RATE_LIMIT_BACKOFF)
            except errors.ServerError as e:
                # 503 UNAVAILABLE ("high demand") — lỗi tạm thời phía Google, không phải quota.
                last_error = e
                if attempt < _RATE_LIMIT_RETRIES:
                    print(f"  [server] Gemini đang quá tải (503), chờ {_RATE_LIMIT_BACKOFF:.0f}s rồi thử lại...")
                    time.sleep(_RATE_LIMIT_BACKOFF)

        # Hết số lần retry mà vẫn lỗi — bọc lại thành thông báo gọn tiếng Việt
        # thay vì để traceback gốc của SDK bắn thẳng ra ngoài (đã gặp thật:
        # ServerError 503 không được main.py/app.py bắt vì trước đây raise
        # nguyên bản, không phải LLMQuotaExceededError).
        if isinstance(last_error, errors.ServerError):
            raise LLMServerError(
                f"Gemini server đang quá tải (503) — đã thử lại {_RATE_LIMIT_RETRIES} lần vẫn lỗi. "
                "Đây là lỗi TẠM THỜI phía Google (không phải hết quota) — đợi vài phút rồi chạy lại "
                "đúng lệnh cũ, tiến độ đã sinh được sẽ không mất. "
                f"Chi tiết: {last_error}"
            ) from last_error
        raise LLMServerError(
            f"Gemini vượt giới hạn request/phút quá lâu — đã thử lại {_RATE_LIMIT_RETRIES} lần vẫn lỗi. "
            f"Đợi 1-2 phút rồi chạy lại đúng lệnh cũ. Chi tiết: {last_error}"
        ) from last_error

    def generate_json(self, prompt: str) -> dict:
        response = self._call_with_retry(
            lambda: self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
        )
        text = response.text
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMJSONError(
                f"Gemini không trả JSON hợp lệ ({e}).\n--- Raw response ---\n{text}"
            ) from e

    def _upload_video_part(self, video_path: Union[str, Path]) -> types.Part:
        video_path = Path(video_path)
        uploaded = self.client.files.upload(file=str(video_path))

        deadline = time.monotonic() + _VIDEO_UPLOAD_TIMEOUT
        while uploaded.state == types.FileState.PROCESSING:
            if time.monotonic() > deadline:
                raise RuntimeError(f"Video '{video_path.name}' xử lý quá lâu trên Gemini Files API (timeout).")
            time.sleep(_VIDEO_UPLOAD_POLL_INTERVAL)
            uploaded = self.client.files.get(name=uploaded.name)

        if uploaded.state != types.FileState.ACTIVE:
            raise RuntimeError(f"Gemini không xử lý được video '{video_path.name}': state={uploaded.state}")

        return types.Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type)

    def generate_text(
        self,
        prompt: str,
        images: Optional[Sequence[Union[str, Path]]] = None,
        video: Optional[Union[str, Path]] = None,
    ) -> str:
        """images/video: đính kèm ảnh/video thật — Gemini nhìn trực tiếp nội dung này
        khi viết text (dùng để viết script bám theo ảnh/video sản phẩm thật). Ảnh gửi
        inline; video upload qua Gemini Files API (video có thể lớn hơn giới hạn inline)."""
        parts = [types.Part.from_text(text=prompt)]
        for img_path in images or []:
            img_path = Path(img_path)
            mime_type = mimetypes.guess_type(str(img_path))[0] or "image/jpeg"
            parts.append(types.Part.from_bytes(data=img_path.read_bytes(), mime_type=mime_type))
        if video:
            parts.append(self._upload_video_part(video))

        response = self._call_with_retry(
            lambda: self.client.models.generate_content(model=self.model_name, contents=parts)
        )
        return response.text

    def generate_speech(self, text_with_tags: str, voice_name: str = "Kore", model: Optional[str] = None) -> bytes:
        """Sinh giọng đọc qua Gemini TTS — trả về PCM thô (16-bit, 24kHz, mono,
        xem GEMINI_TTS_SAMPLE_RATE/SAMPLE_WIDTH). Thẻ cảm xúc kiểu `[hào hứng]`
        được model đọc trực tiếp như chỉ dẫn phong cách (không cần strip như
        edge-tts) — model TTS này KHÔNG trả về timing từng câu/từ, nên chỉ dùng
        cho đoạn không cần đồng bộ ảnh-theo-câu (hook/CTA), xem src/media_step.py."""
        response = self._call_with_retry(
            lambda: self.client.models.generate_content(
                model=model or "gemini-3.1-flash-tts-preview",
                contents=text_with_tags,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                        )
                    ),
                ),
            )
        )
        return response.candidates[0].content.parts[0].inline_data.data
