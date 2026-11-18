"""ShelfSense local demo API. Bind to 127.0.0.1 only; never expose publicly without authentication."""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from web_api import xu_ly_su_kien

app = FastAPI(title="ShelfSense BigQuery API", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501", "http://127.0.0.1:8501",
        "http://localhost:8503", "http://127.0.0.1:8503",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

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
    return {"ok": True, "database": "BigQuery", "write_test": False, "note": "API đang hoạt động; endpoint này không tự ghi thử dữ liệu."}

@app.post("/api/phieu")
def tao_phieu_endpoint(request: TaoPhieuRequest):
    try:
        payload = request.model_dump()
        payload["dong"] = [x.model_dump() for x in request.dong]
        result = xu_ly_su_kien(payload)
        if not result.get("ok"):
            raise HTTPException(status_code=422, detail=result.get("loi", "Không tạo được phiếu"))
        return {"ok": True, "id": result.get("id"), "ma_phieu": result.get("ma"), "message": "Đã ghi phiếu vào BigQuery"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
