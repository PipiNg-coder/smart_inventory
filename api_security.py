"""ShelfSense - cấu hình bảo mật cho API (CORS + khóa X-API-Key).

Biến môi trường (đặt khi chạy / triển khai API):
  SHELFSENSE_ALLOWED_ORIGINS  Danh sách địa chỉ web được gọi API, cách nhau bằng dấu phẩy.
                              Ví dụ: https://ten-app.streamlit.app
                              Địa chỉ localhost (mọi cổng) luôn được phép để chạy thử trên máy.
  SHELFSENSE_API_KEY          Nếu đặt: mọi lệnh ghi/đọc qua API phải gửi header X-API-Key trùng khóa này.
                              Để trống: không kiểm tra khóa (chỉ nên dùng khi chạy trên máy mình).
"""
import hmac
import os

# Trang chạy trên chính máy này (Streamlit mở ở cổng nào cũng được: 8501, 8503, 8505...)
_LOCALHOST_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


def _danh_sach_origin() -> list[str]:
    tho = os.environ.get("SHELFSENSE_ALLOWED_ORIGINS", "")
    return [o.strip().rstrip("/") for o in tho.split(",") if o.strip()]


def cau_hinh_cors() -> dict:
    """Tham số cho CORSMiddleware. Header X-API-Key phải nằm trong allow_headers, nếu thiếu thì trình duyệt chặn lệnh gửi phiếu."""
    return {
        "allow_origins": _danh_sach_origin(),
        "allow_origin_regex": _LOCALHOST_REGEX,
        "allow_credentials": False,
        "allow_methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-API-Key"],
        "max_age": 600,
    }


def khoa_api() -> str:
    """Khóa API cấu hình trên máy chủ (chuỗi rỗng = không bắt buộc khóa)."""
    return os.environ.get("SHELFSENSE_API_KEY", "").strip()


def khoa_hop_le(khoa_gui_len: str | None, khoa_cau_hinh: str) -> bool:
    """Chưa cấu hình khóa thì cho qua; có khóa thì so sánh an toàn (không lộ thời gian so sánh)."""
    if not khoa_cau_hinh:
        return True
    return hmac.compare_digest(str(khoa_gui_len or "").encode("utf-8"), khoa_cau_hinh.encode("utf-8"))
