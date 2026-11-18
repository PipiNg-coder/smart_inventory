"""Các hàm tạo / sửa / hủy phiếu ghi vào van_hanh_v2 (chép từ File 06)."""
from datetime import datetime
from uuid import uuid4
import math
from zoneinfo import ZoneInfo
from google.cloud import bigquery
from google.oauth2 import service_account
import streamlit as st

PROJECT_ID = "he-thong-ton-kho-ban-le"
MART, VH = "mart_data_v2", "van_hanh_v2"

# Khởi tạo client BigQuery từ st.secrets (hoặc môi trường hệ thống)
if "gcp_service_account" in st.secrets:
    creds = service_account.Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"])
    )
    client = bigquery.Client(project=PROJECT_ID, credentials=creds)
else:
    client = bigquery.Client(project=PROJECT_ID)

DAU = {"NHAP": +1, "BAN": -1, "HUY": -1, "TRA_HANG": +1}
TIEN_TO = {"NHAP": "PN", "BAN": "PB", "HUY": "PH", "TRA_HANG": "PT"}
VN = ZoneInfo("Asia/Ho_Chi_Minh")


def _them_dong(bang, rows):
    """Ghi thêm dòng vào van_hanh_v2.<bang>."""
    if not rows:
        return
    cfg = bigquery.LoadJobConfig(
        write_disposition="WRITE_APPEND",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    client.load_table_from_json(rows, f"{PROJECT_ID}.{VH}.{bang}", job_config=cfg).result()


def _sql(sql, params=None):
    cfg = bigquery.QueryJobConfig(query_parameters=params or [])
    return client.query(sql, job_config=cfg).to_dataframe()


def _params_skus(skus):
    return [bigquery.ArrayQueryParameter("skus", "STRING", list(skus))]


def _chuan_dong(dong):
    out = {}
    for d in dong:
        sku = str(d["sku"]).strip().upper()
        if not sku:
            raise ValueError("SKU không được để trống")
        if sku in out:
            raise ValueError(f"SKU {sku} bị lặp trong phiếu; hãy gộp thành một dòng")
        try:
            so_luong = float(d["so_luong"])
            don_gia = float(d.get("don_gia") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"Số lượng/đơn giá của {sku} không hợp lệ")
        if not math.isfinite(so_luong) or not math.isfinite(don_gia) or don_gia < 0:
            raise ValueError(f"Số lượng/đơn giá của {sku} không hợp lệ")
        out[sku] = {"so_luong": so_luong, "don_gia": don_gia}
    return out


def _kiem_tra(loai, dong_moi, dong_cu=None):
    """Kiểm tra SKU, số lượng và phần biến động làm giảm tồn."""
    if loai not in DAU:
        return [f"Loại phiếu không hợp lệ: {loai}"]
    if not dong_moi:
        return ["Phiếu không có món nào"]
    loi = []
    skus = list(dong_moi)
    co_df = _sql(
        f"SELECT DISTINCT UPPER(TRIM(sku)) AS sku FROM `{PROJECT_ID}.{MART}.dim_product` "
        "WHERE UPPER(TRIM(sku)) IN UNNEST(@skus)",
        _params_skus(skus),
    )
    co = set(co_df["sku"].dropna().tolist())
    for s, v in dong_moi.items():
        if s not in co:
            loi.append(f"Mã {s} không có trong dim_product")
        if v["so_luong"] <= 0:
            loi.append(f"Số lượng của {s} phải lớn hơn 0")

    delta = {
        s: DAU[loai]
        * (
            dong_moi.get(s, {"so_luong": 0.0})["so_luong"]
            - (dong_cu or {}).get(s, {}).get("so_luong", 0.0)
        )
        for s in set(dong_moi) | set(dong_cu or {})
    }
    can_kiem_tra = [s for s, d in delta.items() if d < 0]
    if can_kiem_tra:
        ton_df = _sql(
            f"SELECT UPPER(TRIM(sku)) AS sku, ton_live FROM `{PROJECT_ID}.{VH}.v_ton_live` "
            "WHERE UPPER(TRIM(sku)) IN UNNEST(@skus)",
            _params_skus(can_kiem_tra),
        )
        ton = dict(zip(ton_df["sku"], ton_df["ton_live"]))
        for s in can_kiem_tra:
            can = -delta[s]
            hien_co = float(ton.get(s, 0.0) or 0.0)
            if can > hien_co:
                loi.append(f"{s}: cần giảm tồn {can:g} nhưng tồn live chỉ còn {hien_co:g}")
    return loi


def _ghi(ma, pb, loai, hanh_dong, trang_thai, ly_do, nguoi, doi_tac, la_thu, dong, bien_dong, ngay):
    bay_gio = datetime.now(VN)
    ts = bay_gio.strftime("%Y-%m-%d %H:%M:%S+07:00")
    _them_dong(
        "phieu_log",
        [
            {
                "ma_phieu": ma,
                "phien_ban": pb,
                "loai_phieu": loai,
                "ngay_phieu": ngay,
                "doi_tac": doi_tac,
                "hanh_dong": hanh_dong,
                "trang_thai": trang_thai,
                "ly_do": ly_do,
                "nguoi_thuc_hien": nguoi,
                "thoi_diem": ts,
                "la_phieu_thu": la_thu,
            }
        ],
    )
    _them_dong(
        "phieu_chi_tiet_log",
        [
            {
                "ma_phieu": ma,
                "phien_ban": pb,
                "sku": s,
                "so_luong": v["so_luong"],
                "don_gia": v["don_gia"],
            }
            for s, v in dong.items()
        ],
    )
    _them_dong(
        "stock_movement_live",
        [
            {
                "movement_id": f"{ma}_v{pb}_{s}",
                "thoi_diem": ts,
                "ngay": bay_gio.strftime("%Y-%m-%d"),
                "ma_phieu": ma,
                "phien_ban": pb,
                "sku": s,
                "loai_giao_dich": loai,
                "so_luong_thay_doi": dl,
                "don_gia": dong.get(s, {}).get("don_gia", 0.0),
                "la_phieu_thu": la_thu,
            }
            for s, dl in bien_dong.items()
            if dl != 0
        ],
    )


def tao_phieu(loai, dong, doi_tac="", nguoi="demo", la_phieu_thu=False, ly_do=""):
    """Tạo phiếu mới. Lưu ý: la_phieu_thu phải để False cho phiếu thật để script 07 gom dữ liệu."""
    dong = _chuan_dong(dong)
    loi = _kiem_tra(loai, dong)
    if loi:
        raise ValueError("; ".join(loi))
    bay_gio = datetime.now(VN)
    ma = f"{TIEN_TO[loai]}{bay_gio.strftime('%y%m%d%H%M%S')}_{uuid4().hex[:8].upper()}"
    bien_dong = {s: DAU[loai] * v["so_luong"] for s, v in dong.items()}
    _ghi(ma, 1, loai, "TAO", "HIEU_LUC", ly_do, nguoi, doi_tac, la_phieu_thu, dong, bien_dong, bay_gio.strftime("%Y-%m-%d"))
    return ma