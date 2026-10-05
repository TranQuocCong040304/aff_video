Ngách đã chọn (JSON):
{{NICHE_JSON}}

Toàn bộ từ khóa thu thập được:
{{KEYWORDS_LIST}}

Nhiệm vụ: Lọc và xếp hạng các từ khóa liên quan chặt chẽ nhất tới ngách ở trên, để dùng cho
tiêu đề, mô tả và định hướng nội dung video.

Chỉ trả về JSON đúng theo format sau, không thêm giải thích ngoài JSON:
{
  "keywords": [
    {"keyword": "...", "score": 0, "reason": "..."}
  ]
}

Quy tắc: score từ 0-10 theo mức độ phù hợp với ngách. Chỉ giữ lại tối đa 15 từ khóa có score >= 6,
sắp xếp giảm dần theo score.
