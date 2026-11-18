
"""
ShelfSense - xử lý sự kiện phiếu từ giao diện web.
"""

import hashlib

from utils.phieu import (
    LOAI_HOP_LE,
    TIEN_TO,
    tao_phieu,
)

TOI_DA_DONG = 200

_TEN_LOAI = {
    "receive": "NHAP",
    "issue": "BAN",
    "returned": "TRA_HANG",
    "canceled": "HUY",
    "nhap": "NHAP",
    "ban": "BAN",
    "tra_hang": "TRA_HANG",
    "huy": "HUY",
}


def ma_phieu_tu_su_kien(loai: str, event_id: str) -> str:
    """
    Sinh mã phiếu ổn định từ event ID.
    Cùng một event ID sẽ tạo cùng một mã phiếu.
    """

    event_id = str(event_id or "").strip()

    if len(event_id) < 8:
        raise ValueError("Mã sự kiện không hợp lệ.")

    if loai not in TIEN_TO:
        raise ValueError("Loại phiếu không hợp lệ.")

    digest = hashlib.sha256(
        event_id.encode("utf-8")
    ).hexdigest()[:32].upper()

    return f"{TIEN_TO[loai]}W{digest}"


def xu_ly_su_kien(ev: dict) -> dict:
    """
    Nhận dữ liệu phiếu từ API và gọi BigQuery.
    """

    if not isinstance(ev, dict):
        return {
            "id": "",
            "ok": False,
            "loi": "Dữ liệu sự kiện phải là JSON object.",
        }

    ev_id = str(ev.get("id", "")).strip()

    try:
        if not ev_id:
            raise ValueError("Thiếu mã sự kiện.")

        loai_raw = str(ev.get("loai", "")).strip()

        loai = (
            loai_raw
            if loai_raw in LOAI_HOP_LE
            else _TEN_LOAI.get(loai_raw.lower(), "")
        )

        if not loai:
            raise ValueError(
                f"Loại phiếu không hợp lệ: {loai_raw}"
            )

        dong = ev.get("dong")

        if not isinstance(dong, list) or not dong:
            raise ValueError("Phiếu không có sản phẩm.")

        if len(dong) > TOI_DA_DONG:
            raise ValueError(
                f"Phiếu vượt quá {TOI_DA_DONG} dòng."
            )

        dong_chuan = []

        for index, item in enumerate(dong, start=1):

            if not isinstance(item, dict):
                raise ValueError(
                    f"Dòng sản phẩm {index} không hợp lệ."
                )

            sku = str(item.get("sku", "")).strip()

            if not sku:
                raise ValueError(
                    f"Dòng sản phẩm {index} thiếu SKU."
                )

            dong_chuan.append({
                "sku": sku,
                "so_luong": item.get("so_luong"),
                "don_gia": item.get("don_gia", 0),
            })

        ma = tao_phieu(
            loai,
            dong_chuan,
            doi_tac=str(ev.get("doi_tac", ""))[:200],
            nguoi=(
                str(ev.get("nguoi", "")).strip() or "web"
            )[:100],
            la_phieu_thu=False,
            ly_do=str(ev.get("ly_do", ""))[:200],
            ghi_chu=str(ev.get("ghi_chu", ""))[:500],
            ngay=ev.get("ngay") or None,
            ma_phieu=ma_phieu_tu_su_kien(loai, ev_id),
        )

        return {
            "id": ev_id,
            "ok": True,
            "ma": ma,
        }

    except ValueError as error:
        return {
            "id": ev_id,
            "ok": False,
            "loi": str(error),
        }

    except Exception as error:
        return {
            "id": ev_id,
            "ok": False,
            "loi": (
                "Không ghi được lên BigQuery: "
                f"{type(error).__name__}: {error}"
            ),
        }
