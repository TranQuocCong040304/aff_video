"""Wrapper mỏng quanh Groq API — dùng cho các bước CHỈ CẦN TEXT (không cần
xem ảnh/video): chọn ngách, lọc keyword, lọc sản phẩm, hook, CTA, metadata.

Free tier Groq rộng rãi hơn Gemini nhiều (30 request/phút, ~14.400/ngày tùy
model) nhưng KHÔNG hỗ trợ đọc video và hỗ trợ ảnh còn hạn chế (model preview,
tối đa 5 ảnh/request) — nên phần viết lời bình bám ảnh/video thật của từng sản
phẩm (04b_product_segment) vẫn dùng GeminiClient, xem src/pipeline.py.
"""
import json
import os
import time

import groq

from .llm_errors import LLMJSONError, LLMQuotaExceededError, LLMServerError

_RETRIES = 4
_BACKOFF = 15.0


class GroqClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Thiếu GROQ_API_KEY. Copy .env.example thành .env và điền API key "
                "(lấy tại https://console.groq.com/keys)."
            )
        self.client = groq.Groq(api_key=api_key)
        self.model_name = model or os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

    def _call_with_retry(self, fn):
        last_error = None
        for attempt in range(1, _RETRIES + 1):
            try:
                return fn()
            except groq.RateLimitError as e:
                if "PerDay" in str(e) or "daily" in str(e).lower():
                    raise LLMQuotaExceededError(
                        f"Hết quota Groq theo NGÀY (free tier). Đợi reset hoặc bật billing tại "
                        f"https://console.groq.com/settings/billing. Chi tiết: {e}"
                    ) from e
                last_error = e
                if attempt < _RETRIES:
                    print(f"  [rate limit] Groq vượt giới hạn request/phút, chờ {_BACKOFF:.0f}s rồi thử lại...")
                    time.sleep(_BACKOFF)
            except groq.InternalServerError as e:
                last_error = e
                if attempt < _RETRIES:
                    print(f"  [server] Groq đang quá tải, chờ {_BACKOFF:.0f}s rồi thử lại...")
                    time.sleep(_BACKOFF)
            except groq.APIStatusError as e:
                # Lỗi khác không phải rate-limit(429)/server quá tải(5xx) —
                # đã gặp thật: 413 "Request too large" khi file sản phẩm có
                # quá nhiều dòng (52 sp), vượt giới hạn token/phút của tài
                # khoản. Retry vô ích vì cùng 1 request sẽ luôn lỗi y hệt.
                status = getattr(e, "status_code", None)
                if status == 413:
                    raise LLMServerError(
                        "Request gửi lên Groq quá LỚN (413) — dữ liệu đầu vào (số sản phẩm/từ khóa) "
                        "quá nhiều cho 1 lần gọi, vượt giới hạn token/phút của tài khoản Groq hiện tại. "
                        "KHÔNG phải lỗi tạm thời — đợi/chạy lại sẽ vẫn lỗi y hệt. Giảm bớt dữ liệu đầu "
                        f"vào (vd giảm max_products trong src/ingestion.py) rồi chạy lại. Chi tiết: {e}"
                    ) from e
                raise LLMServerError(
                    f"Groq trả lỗi HTTP {status}, không nằm trong các trường hợp đã xử lý (không phải "
                    f"rate-limit hay quá tải). Retry sẽ không giúp được, kiểm tra lại request/tài khoản. "
                    f"Chi tiết: {e}"
                ) from e

        # Hết số lần retry mà vẫn lỗi — bọc lại thành thông báo gọn tiếng Việt
        # thay vì để traceback gốc của SDK bắn thẳng ra ngoài (cùng lỗ hổng đã
        # gặp thật bên GeminiClient, xem src/gemini_client.py).
        raise LLMServerError(
            f"Groq đang quá tải hoặc vượt giới hạn request/phút quá lâu — đã thử lại {_RETRIES} lần "
            f"vẫn lỗi. Đợi 1-2 phút rồi chạy lại đúng lệnh cũ. Chi tiết: {last_error}"
        ) from last_error

    def _chat(self, prompt: str, json_mode: bool) -> str:
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if "gpt-oss" in self.model_name:
            # Đã gặp thật: bước lọc sản phẩm (prompt ~4.4k token) với mức
            # reasoning mặc định, model tiêu hết 3072 token output vào phần suy
            # nghĩ → finish_reason=length, content rỗng (JSON mode báo 400
            # json_validate_failed). "low" chỉ tốn ~1.5k token suy nghĩ.
            kwargs["reasoning_effort"] = os.environ.get("GROQ_REASONING_EFFORT", "low")
        # Groq tính 1 request = token prompt + max_completion_tokens vào giới
        # hạn token/phút (free tier 8000). Để mặc định thì prompt lọc sản phẩm
        # bị 413 dù output thật chỉ ~2000 token → đặt trần rõ ràng.
        kwargs["max_completion_tokens"] = int(os.environ.get("GROQ_MAX_COMPLETION_TOKENS", "3000"))
        response = self._call_with_retry(
            lambda: self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
        )
        choice = response.choices[0]
        if not choice.message.content and choice.finish_reason == "length":
            raise LLMServerError(
                "Groq hết giới hạn token output trước khi viết xong câu trả lời (model dùng hết token "
                "cho phần suy nghĩ). Thử đặt GROQ_REASONING_EFFORT=low trong .env hoặc giảm dữ liệu "
                "đầu vào (số sản phẩm/từ khóa)."
            )
        return choice.message.content

    def _chat_json_mode(self, prompt: str) -> str | None:
        """Gọi JSON mode, thử lại khi Groq báo 400 json_validate_failed.

        Đã gặp thật: model reasoning (gpt-oss) ở JSON mode thỉnh thoảng sinh
        output rỗng (failed_generation='') → Groq trả 400. Lỗi này ngẫu nhiên,
        gọi lại thường qua. Hết lượt vẫn lỗi thì trả None để generate_json
        chuyển sang gọi thường (không JSON mode) rồi tự tách JSON.
        """
        for attempt in range(1, _RETRIES + 1):
            try:
                return self._chat(prompt, json_mode=True)
            except LLMServerError as e:
                if "json_validate_failed" not in str(e):
                    raise
                print(f"  [json] Groq sinh JSON lỗi (lần {attempt}/{_RETRIES}), thử lại...")
        return None

    @staticmethod
    def _extract_json(text: str) -> dict:
        text = (text or "").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                raise
            return json.loads(text[start:end + 1])

    def generate_json(self, prompt: str) -> dict:
        text = self._chat_json_mode(prompt)
        if text is None:
            print("  [json] Chuyển sang gọi không dùng JSON mode rồi tự tách JSON...")
            text = self._chat(
                prompt + "\n\nChỉ trả về đúng 1 object JSON hợp lệ, không kèm giải thích hay markdown.",
                json_mode=False,
            )
        try:
            return self._extract_json(text)
        except json.JSONDecodeError as e:
            raise LLMJSONError(
                f"Groq không trả JSON hợp lệ ({e}).\n--- Raw response ---\n{text}"
            ) from e

    def generate_text(self, prompt: str) -> str:
        return self._chat(prompt, json_mode=False)

    def transcribe_words(self, audio_path, language: str = "vi") -> list[dict]:
        """Whisper trên Groq: [{"word", "start", "end"}] (giây) — dùng để căn
        giọng tự thu khớp với script (src/voice_import.py). File tối đa 25MB
        (free tier) nên gửi bản nén mono, không gửi file gốc."""
        model = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3")

        def call():
            with open(audio_path, "rb") as f:
                return self.client.audio.transcriptions.create(
                    file=f, model=model, language=language,
                    response_format="verbose_json", timestamp_granularities=["word"],
                )

        data = self._call_with_retry(call).model_dump()
        return data.get("words") or []
