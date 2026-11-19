"""ShelfSense - đọc LỊCH SỬ phiếu (bán / nhập / trả / hủy) từ BigQuery.

Trước đây hóa đơn và lịch sử biến động chỉ nằm trong localStorage của từng trình duyệt,
nên đổi máy hoặc đổi trình duyệt là mất. Module này đọc lại từ van_hanh_v2 (nguồn sự thật)
để mọi thiết bị đều thấy cùng một lịch sử.

Dùng chung cho:
  - app.py  (Streamlit nhúng sẵn lịch sử vào trang khi mở)
  - api.py  (GET /api/lich-su để giao diện tự làm mới, không cần tải lại trang)
"""
from datetime import datetime, timezone

from google.cloud import bigquery

from utils.phieu import PROJECT_ID, VH, _sql, dau_phieu, ton_live_cua

NHAN_LOAI = {
    "NHAP": "Nhập hàng",
    "BAN": "Bán hàng",
    "HUY": "Hủy hàng",
    "TRA_HANG": "Trả hàng",
}


def _iso(x) -> str:
    """Timestamp BigQuery -> chuỗi ISO có múi giờ (trình duyệt tự đổi sang giờ máy)."""
    if x is None:
        return ""
    try:
        s = x.isoformat()
    except Exception:
        return ""
    return "" if s == "NaT" else s


def ma_hoa_don(ma_phieu: str) -> str:
    """Mã hóa đơn ngắn gọn, ổn định (cùng phiếu -> cùng mã trên mọi máy)."""
    return "HD" + str(ma_phieu)[3:11].upper()


def lich_su_phieu(toi_da_phieu: int = 3000) -> dict:
    """Trả về {invoices, movements, ton_live, tong_phieu, cap_nhat}.

    - Chỉ lấy phiên bản MỚI NHẤT của mỗi phiếu (phiếu đã sửa thì hiện bản sửa).
    - Bỏ phiếu đã hủy (DA_HUY) và phiếu thử (la_phieu_thu).
    - Sắp xếp CŨ -> MỚI (giao diện tự đảo ngược khi hiển thị).
    """
    toi_da_phieu = max(1, min(int(toi_da_phieu), 10000))
    df = _sql(
        f"""
        WITH p AS (
          SELECT ma_phieu, loai_phieu, ngay_phieu, doi_tac, ly_do, nguoi_thuc_hien, thoi_diem
          FROM `{PROJECT_ID}.{VH}.v_phieu_hien_tai`
          WHERE trang_thai = 'HIEU_LUC' AND IFNULL(la_phieu_thu, FALSE) = FALSE
          ORDER BY thoi_diem DESC
          LIMIT @gh
        )
        SELECT p.ma_phieu, p.loai_phieu, CAST(p.ngay_phieu AS STRING) AS ngay,
               p.doi_tac, p.ly_do, p.nguoi_thuc_hien, p.thoi_diem,
               c.sku, c.so_luong, c.don_gia
        FROM p
        JOIN `{PROJECT_ID}.{VH}.v_chi_tiet_hien_tai` c ON c.ma_phieu = p.ma_phieu
        ORDER BY p.thoi_diem, p.ma_phieu, c.sku
        """,
        [bigquery.ScalarQueryParameter("gh", "INT64", toi_da_phieu)],
    )

    movements, invoices, skus, so_phieu = [], {}, set(), set()
    for r in df.itertuples(index=False):
        loai = str(r.loai_phieu)
        ma = str(r.ma_phieu)
        sku = str(r.sku).strip().upper()
        sl = float(r.so_luong or 0)
        gia = float(r.don_gia or 0)
        ly_do = r.ly_do if isinstance(r.ly_do, str) else ""
        doi_tac = r.doi_tac if isinstance(r.doi_tac, str) else ""
        ts = _iso(r.thoi_diem)
        thay_doi = dau_phieu(loai, ly_do) * sl
        skus.add(sku)
        so_phieu.add(ma)

        note = ly_do
        if loai == "BAN":
            hd = ma_hoa_don(ma)
            note = (ly_do + " · " + hd) if ly_do else hd
            inv = invoices.setdefault(
                ma,
                {
                    "id": hd,
                    "date": str(r.ngay)[:10],
                    "time": ts,
                    "items": [],
                    "total": 0.0,
                    "source": "Phiếu bán hàng (BigQuery)",
                    "bq": True,
                    "bqMa": ma,
                    "bqAt": ts,
                },
            )
            inv["items"].append({"sku": sku, "qty": sl, "price": gia})
            inv["total"] += sl * gia

        movements.append(
            {
                "id": ma,
                "date": str(r.ngay)[:10],
                "sku": sku,
                "type": NHAN_LOAI.get(loai, loai),
                "qty": thay_doi,
                "note": note,
                "supplier": doi_tac,
                "time": ts,
            }
        )

    # Tồn live của các SKU có phát sinh để máy khác thấy ngay số tồn mới
    try:
        ton = ton_live_cua(sorted(skus)[:1000]) if skus else {}
    except Exception:
        ton = {}

    return {
        "invoices": list(invoices.values()),
        "movements": movements,
        "ton_live": ton,
        "tong_phieu": len(so_phieu),
        "cap_nhat": datetime.now(timezone.utc).isoformat(),
    }
