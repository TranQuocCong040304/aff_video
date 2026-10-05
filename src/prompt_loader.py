"""Load prompt template từ prompts/*.md và thay thế {{PLACEHOLDER}}.

Dùng replace thủ công thay vì str.format() vì các template có chứa dấu ngoặc
nhọn { } trong ví dụ JSON, sẽ va chạm với cú pháp format string.
"""
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def render(template_name: str, **kwargs) -> str:
    path = PROMPTS_DIR / template_name
    text = path.read_text(encoding="utf-8")
    for key, value in kwargs.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text
