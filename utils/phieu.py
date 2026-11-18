"""Tạo phiếu và ghi vào van_hanh_v2 (bảng chỉ-thêm-dòng).

Quy tắc dấu của so_luong_thay_doi (khớp với 4 màn hình trên web):
  • Nhập hàng (NHAP)       : cộng tồn
  • Bán hàng (BAN)         : trừ tồn
  • Trả hàng (TRA_HANG)    : cộng tồn
  • Hủy hàng (HUY)         : lý do "Hết date" hoặc "Lỗi" thì TRỪ; "Đổi luôn" hoặc "Đổi hàng" thì CỘNG
                              (lý do khác: trừ, vì hủy hàng mặc định là mất hàng)
"""
import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from google.cloud import bigquery

PROJECT_ID = "he-thong-ton-kho-ban-le"
MART, VH = "mart_data_v2", "van_hanh_v2"
VN = ZoneInfo("Asia/Ho_Chi_Minh")

TIEN_TO = {"NHAP": "PN", "BAN": "PB", "HUY": "PH", "TRA_HANG": "PT"}
LOAI_HOP_LE = tuple(TIEN_TO)
LY_DO_HUY_CONG = {"doi luon", "doi hang"}  # hủy kiểu đổi hàng: hàng quay về kho nên cộng tồn

# Phiếu có ngày trước ngày này không được nhận (script 07 chỉ tin phiếu web từ ngày này).
NGAY_BAT_DAU_PHIEU = date(2026, 11, 9)


def _client() -> bigquery.Client:
    """Lấy client dùng chung (đọc khóa từ st.secrets hoặc Google ADC)."""
    from utils.bigquery_client import get_bigquery_client

    return get_bigquery_client()


def _bo_dau(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()



def dau_phieu(loai: str, ly_do: str = "") -> int:
    """Xác định dấu biến động tồn kho."""
    if loai in ("NHAP", "TRA_HANG"):
        return 1

    if loai == "BAN":
        return -1

    if loai == "HUY":
        # phieu_log lưu lý do và ghi chú chung bằng dấu ·
        ly_do_chinh = str(ly_do or "").split("·", 1)[0]
        return 1 if _bo_dau(ly_do_chinh) in LY_DO_HUY_CONG else -1

    raise ValueError(f"Loại phiếu không hợp lệ: {loai}")



def _them_dong(bang, rows):
    """Ghi thêm dòng vào van_hanh_v2.<bang> (load job, không dùng DML nên chạy được cả ở sandbox)."""
    if not rows:
        return
    cfg = bigquery.LoadJobConfig(
        write_disposition="WRITE_APPEND",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    _client().load_table_from_json(rows, f"{PROJECT_ID}.{VH}.{bang}", job_config=cfg).result()


def _sql(sql, params=None):
    cfg = bigquery.QueryJobConfig(query_parameters=params or [])
    return _client().query(sql, job_config=cfg).to_dataframe()


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



def _kiem_tra(
    loai,
    dong_moi,
    dong_cu=None,
    ly_do="",
    dau_cu=None,
):
    """Kiểm tra SKU và tồn kho dựa trên biến động thực tế."""

    if loai not in LOAI_HOP_LE:
        return [f"Loại phiếu không hợp lệ: {loai}"]

    if not dong_moi:
        return ["Phiếu không có món nào"]

    dau_moi = dau_phieu(loai, ly_do)
    dau_cu = dau_moi if dau_cu is None else dau_cu

    loi = []
    dong_cu = dong_cu or {}

    skus = list(set(dong_moi) | set(dong_cu))

    co_df = _sql(
        f"""
        SELECT DISTINCT UPPER(TRIM(sku)) AS sku
        FROM `{PROJECT_ID}.{MART}.dim_product`
        WHERE UPPER(TRIM(sku)) IN UNNEST(@skus)
        """,
        _params_skus(skus),
    )

    co = set(co_df["sku"].dropna().tolist())

    for sku, item in dong_moi.items():
        if sku not in co:
            loi.append(f"Mã {sku} không có trong dim_product")

        if item["so_luong"] <= 0:
            loi.append(f"Số lượng của {sku} phải lớn hơn 0")

    # Biến động mới - biến động cũ.
    # Dương: tăng tồn; âm: giảm tồn.
    delta = {
        sku: (
            dau_moi * dong_moi.get(sku, {}).get("so_luong", 0.0)
            - dau_cu * dong_cu.get(sku, {}).get("so_luong", 0.0)
        )
        for sku in skus
    }

    can_kiem_tra = [sku for sku, value in delta.items() if value < 0]

    if can_kiem_tra:
        ton_df = _sql(
            f"""
            SELECT UPPER(TRIM(sku)) AS sku, ton_live
            FROM `{PROJECT_ID}.{VH}.v_ton_live`
            WHERE UPPER(TRIM(sku)) IN UNNEST(@skus)
            """,
            _params_skus(can_kiem_tra),
        )

        ton = dict(zip(ton_df["sku"], ton_df["ton_live"]))

        for sku in can_kiem_tra:
            can_giam = -delta[sku]
            hien_co = float(ton.get(sku, 0) or 0)

            if can_giam > hien_co:
                loi.append(
                    f"{sku}: cần giảm {can_giam:g}, "
                    f"nhưng tồn live chỉ còn {hien_co:g}"
                )

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
    # stock_movement_live ghi CUỐI CÙNG: chỉ khi 2 bảng trên đã ghi xong thì tồn live mới đổi
    _them_dong(
        "stock_movement_live",
        [
            {
                "movement_id": f"{ma}_v{pb}_{s}",
                "thoi_diem": ts,
                "ngay": ngay,
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


def _chuan_ngay(ngay) -> str:
    """Ngày phiếu (yyyy-mm-dd). Bỏ trống = hôm nay. Không nhận ngày tương lai hoặc trước NGAY_BAT_DAU_PHIEU."""
    hom_nay = datetime.now(VN).date()
    if ngay in (None, ""):
        return hom_nay.isoformat()
    try:
        d = ngay if isinstance(ngay, date) else date.fromisoformat(str(ngay)[:10])
    except ValueError:
        raise ValueError(f"Ngày phiếu không hợp lệ: {ngay}")
    if d > hom_nay:
        raise ValueError("Ngày phiếu không được ở tương lai")
    if d < NGAY_BAT_DAU_PHIEU:
        raise ValueError(f"Ngày phiếu phải từ {NGAY_BAT_DAU_PHIEU:%d/%m/%Y} trở đi")
    return d.isoformat()


def da_co_phieu(ma: str) -> bool:
    df = _sql(
        f"SELECT 1 AS x FROM `{PROJECT_ID}.{VH}.phieu_log` WHERE ma_phieu = @ma LIMIT 1",
        [bigquery.ScalarQueryParameter("ma", "STRING", ma)],
    )
    return not df.empty


def tao_phieu(
    loai,
    dong,
    doi_tac="",
    nguoi="demo",
    la_phieu_thu=False,
    ly_do="",
    ghi_chu="",
    ngay=None,
    ma_phieu=None,
):
    """Tạo phiếu mới, trả về mã phiếu.

    ly_do   : với HUY là trạng thái hàng (Hết date / Lỗi / Đổi luôn / Đổi hàng) và quyết định dấu cộng/trừ.
    ghi_chu : ghi chú tự do, được nối vào cột ly_do của phieu_log.
    ngay    : ngày phiếu (mặc định hôm nay).
    ma_phieu: mã do nơi gọi đặt (web dùng mã suy ra từ mã sự kiện). Nếu mã đã có thì KHÔNG ghi lại (chống ghi trùng).
    la_phieu_thu phải để False cho phiếu thật để script 07 gom dữ liệu.
    """
    if loai not in LOAI_HOP_LE:
        raise ValueError(f"Loại phiếu không hợp lệ: {loai}")
    if ma_phieu and da_co_phieu(ma_phieu):
        return ma_phieu
    dong = _chuan_dong(dong)
    loi = _kiem_tra(loai, dong, ly_do=ly_do)
    if loi:
        raise ValueError("; ".join(loi))
    ngay_s = _chuan_ngay(ngay)
    dau = dau_phieu(loai, ly_do)
    bay_gio = datetime.now(VN)
    ma = ma_phieu or f"{TIEN_TO[loai]}{bay_gio.strftime('%y%m%d%H%M%S')}_{uuid4().hex[:8].upper()}"
    bien_dong = {s: dau * v["so_luong"] for s, v in dong.items()}
    ly_do_luu = " · ".join(x for x in (str(ly_do or "").strip(), str(ghi_chu or "").strip()) if x)
    _ghi(ma, 1, loai, "TAO", "HIEU_LUC", ly_do_luu, nguoi, doi_tac, la_phieu_thu, dong, bien_dong, ngay_s)
    return ma

def _phieu_hien_tai(ma):
    """Đọc phiên bản hiện tại và toàn bộ chi tiết phiếu."""

    params = [
        bigquery.ScalarQueryParameter("ma", "STRING", ma)
    ]

    p = _sql(
        f"""
        SELECT *
        FROM `{PROJECT_ID}.{VH}.v_phieu_hien_tai`
        WHERE ma_phieu = @ma
        """,
        params,
    )

    if p.empty:
        raise ValueError(f"Không tìm thấy phiếu {ma}")

    ct = _sql(
        f"""
        SELECT sku, so_luong, don_gia
        FROM `{PROJECT_ID}.{VH}.v_chi_tiet_hien_tai`
        WHERE ma_phieu = @ma
        """,
        params,
    )

    dong_cu = {
        r.sku: {
            "so_luong": float(r.so_luong),
            "don_gia": float(r.don_gia),
        }
        for r in ct.itertuples()
    }

    return p.iloc[0], dong_cu


def sua_phieu(
    ma,
    dong_moi,
    ly_do="",
    ghi_chu="",
    nguoi="web",
    ngay=None,
    expected_version=None,
):
    """Tạo phiên bản mới và chỉ ghi phần chênh lệch tồn kho."""

    p, dong_cu = _phieu_hien_tai(ma)

    if p["trang_thai"] == "DA_HUY":
        raise ValueError("Phiếu đã hủy, không thể sửa")

    pb_cu = int(p["phien_ban"])

    if expected_version is not None:
        if int(expected_version) != pb_cu:
            raise ValueError(
                "Phiếu đã được người khác cập nhật. "
                "Hãy tải lại dữ liệu trước khi sửa."
            )

    loai = str(p["loai_phieu"])

    dong_moi = _chuan_dong(dong_moi)

    # Dấu của phiên bản cũ và phiên bản mới.
    ly_do_cu = str(p.get("ly_do", "") or "")
    dau_cu = dau_phieu(loai, ly_do_cu)
    dau_moi = dau_phieu(loai, ly_do)

    loi = _kiem_tra(
        loai,
        dong_moi,
        dong_cu=dong_cu,
        ly_do=ly_do,
        dau_cu=dau_cu,
    )

    if loi:
        raise ValueError("; ".join(loi))

    tat_ca_sku = set(dong_cu) | set(dong_moi)

    bien_dong = {
        sku: (
            dau_moi * dong_moi.get(sku, {}).get("so_luong", 0.0)
            - dau_cu * dong_cu.get(sku, {}).get("so_luong", 0.0)
        )
        for sku in tat_ca_sku
    }

    pb_moi = pb_cu + 1

    ngay_s = _chuan_ngay(
        ngay or p["ngay_phieu"]
    )

    ly_do_luu = " · ".join(
        x for x in (
            str(ly_do or "").strip(),
            str(ghi_chu or "").strip(),
        )
        if x
    )

    _ghi(
        ma,
        pb_moi,
        loai,
        "SUA",
        "HIEU_LUC",
        ly_do_luu,
        nguoi,
        p["doi_tac"],
        bool(p["la_phieu_thu"]),
        dong_moi,
        bien_dong,
        ngay_s,
    )

    return pb_moi
