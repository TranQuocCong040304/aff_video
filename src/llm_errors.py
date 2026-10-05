"""Lỗi dùng chung giữa các LLM client (Gemini, Groq) để pipeline.py không cần
biết đang gọi provider nào."""


class LLMJSONError(RuntimeError):
    """LLM trả về nội dung không phải JSON hợp lệ."""


class LLMQuotaExceededError(RuntimeError):
    """Hết quota theo ngày — retry sẽ không giúp được, phải đợi reset hoặc bật billing."""


class LLMServerError(RuntimeError):
    """Server phía provider quá tải (503) — đã retry hết số lần cho phép vẫn
    lỗi. Khác quota: đợi vài phút rồi chạy lại là được, không cần đổi key."""
