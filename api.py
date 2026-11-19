"""ShelfSense local demo API. Bind to 127.0.0.1 only; never expose publicly without authentication."""
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api_security import cau_hinh_cors, khoa_api, khoa_hop_le

# Nếu utils/phieu.py hoặc web_api.py lỗi khi nạp, vẫn cho API chạy để /health báo rõ nguyên nhân
try:
    from web_api import xu_ly_su_kien
    LOI_KHOI_DONG = None
except Exception as exc:  # noqa: BLE001
    xu_ly_su_kien = None
    LOI_KHOI_DONG = f"{type(exc).__name__}: {exc}"

app = FastAPI(title="ShelfSense BigQuery API", version="1.0.0")
# CORS: mặc định chỉ trang chạy trên chính máy này; khi triển khai đặt SHELFSENSE_ALLOWED_ORIGINS=https://ten-app.streamlit.app
app.add_middleware(CORSMiddleware, **cau_hinh_cors())
KHOA_API = khoa_api()  # đặt biến môi trường SHELFSENSE_API_KEY để bắt buộc header X-API-Key khi gửi phiếu


def kiem_tra_khoa(x_api_key: str | None):
    if not khoa_hop_le(x_api_key, KHOA_API):
        raise HTTPException(status_code=401, detail="Thiếu hoặc sai khóa API (X-API-Key).")


class DongPhieu(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    so_luong: float = Field(gt=0)
    don_gia: float = Field(default=0, ge=0)

class TaoPhieuRequest(BaseModel):
    id: str = Field(min_length=8, max_length=200)
    loai: str
    ngay: str | None = None
    dong: list[DongPhieu] = Field(min_length=1, max_length=200)
    ly_do: str = ""
    ghi_chu: str = ""
    doi_tac: str = ""
    nguoi: str = "web"

@app.get("/")
def root():
    return {"service": "ShelfSense API", "database": "BigQuery", "health": "/health"}

@app.get("/health")
def health():
    # phieu_san_sang = False nghĩa là API chạy nhưng chưa nạp được utils/phieu.py (xem trường "loi")
    return {"ok": LOI_KHOI_DONG is None, "phieu_san_sang": LOI_KHOI_DONG is None, "loi": LOI_KHOI_DONG,
            "database": "BigQuery", "co_khoa_api": bool(KHOA_API)}


@app.get("/health/bigquery")
def health_bigquery():
    """Kiểm tra thật: xác thực Google, quyền đọc và 3 bảng vận hành."""
    if LOI_KHOI_DONG:
        raise HTTPException(status_code=503, detail=f"Chưa nạp được utils/phieu.py: {LOI_KHOI_DONG}")
    try:
        from utils.phieu import kiem_tra_ket_noi
        return {"ok": True, **kiem_tra_ket_noi()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

@app.post("/api/phieu")
def tao_phieu_endpoint(request: TaoPhieuRequest, x_api_key: str | None = Header(default=None)):
    kiem_tra_khoa(x_api_key)
    if LOI_KHOI_DONG:
        raise HTTPException(status_code=503, detail=f"API chưa sẵn sàng: {LOI_KHOI_DONG}")
    try:
        payload = request.model_dump()
        payload["dong"] = [x.model_dump() for x in request.dong]
        result = xu_ly_su_kien(payload)
        _CACHE_LICH_SU["data"] = None  # có phiếu mới: bỏ bộ nhớ đệm lịch sử
        if not result.get("ok"):
            raise HTTPException(status_code=422, detail=result.get("loi", "Không tạo được phiếu"))
        return {
            "ok": True,
            "id": result.get("id"),
            "ma_phieu": result.get("ma"),
            "ton_live": result.get("ton_live"),          # tồn thật đọc lại từ BigQuery sau khi ghi
            "ton_live_loi": result.get("ton_live_loi"),
            "message": "Đã ghi phiếu vào BigQuery",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/api/ton")
def doc_ton(skus: str, x_api_key: str | None = Header(default=None)):
    """Đọc tồn live hiện tại từ BigQuery cho danh sách SKU (cách nhau bằng dấu phẩy, tối đa 200)."""
    kiem_tra_khoa(x_api_key)
    if LOI_KHOI_DONG:
        raise HTTPException(status_code=503, detail=f"API chưa sẵn sàng: {LOI_KHOI_DONG}")
    ds = [x for x in (s.strip() for s in skus.split(",")) if x][:200]
    if not ds:
        raise HTTPException(status_code=422, detail="Thiếu danh sách SKU")
    try:
        from utils.phieu import ton_live_cua
        return {"ok": True, "ton_live": ton_live_cua(ds)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


# Bộ nhớ đệm ngắn: nhiều máy cùng tự làm mới mỗi 30 giây thì chỉ truy vấn BigQuery tối đa 1 lần / 10 giây
_CACHE_LICH_SU = {"t": 0.0, "n": 0, "data": None}


@app.get("/api/lich-su")
def lich_su(toi_da: int = 3000, x_api_key: str | None = Header(default=None)):
    """Lịch sử hóa đơn + biến động kho (bán/nhập/trả/hủy) đọc từ BigQuery, dùng chung cho mọi trình duyệt/thiết bị."""
    kiem_tra_khoa(x_api_key)
    if LOI_KHOI_DONG:
        raise HTTPException(status_code=503, detail=f"API chưa sẵn sàng: {LOI_KHOI_DONG}")
    toi_da = max(1, min(int(toi_da), 10000))
    now = time.time()
    c = _CACHE_LICH_SU
    if c["data"] is not None and c["n"] == toi_da and now - c["t"] < 10:
        return c["data"]
    try:
        from utils.lich_su import lich_su_phieu
        data = {"ok": True, **lich_su_phieu(toi_da)}
        c.update(t=now, n=toi_da, data=data)
        return data
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
