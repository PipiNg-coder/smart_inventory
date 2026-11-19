# =====================================================================
# 07_dong_bo_phieu_sua.py
# Dong bo phieu moi tu van_hanh_v2 sang mart_data_v2 theo cach an toan.
#
# NGUYEN TAC:
# 1. KHONG xoa/sua bat ky du lieu nao trong stock_movement_live.
# 2. Kiem tra du lieu truoc khi ghi; giu nguyen lich su cac bang Mart.
# 3. Dung BigQuery Load Jobs (WRITE_TRUNCATE/WRITE_APPEND), khong dung DML
#    de phu hop project chua bat Billing.
# 4. Ghep nhat_ky_dong_bo theo movement_id de tranh ghi trung khi chay lai.
# 5. Neu load job loi trong cung tien trinh, co gang khoi phuc cac bang da ghi.
# 6. Mac dinh CHI_XEM_TRUOC=True; khong ghi that neu chua bat co xac nhan.
#
# Chay:
#   python 07_dong_bo_phieu_sua.py
#
# Muon ghi that, dat ca hai bien moi truong:
#   $env:CHI_XEM_TRUOC = "false"
#   $env:XAC_NHAN_GHI_THAT = "true"
# =====================================================================

import os
import re
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from google.cloud import bigquery


# ------------------------- CAU HINH -------------------------
PROJECT_ID = "he-thong-ton-kho-ban-le"
MART = "mart_data_v2"
VH = "van_hanh_v2"

# Ngay cuoi co so ban that trong fact_sales.
NGAY_DU_LIEU_THAT_CUOI = date(2026, 10, 28)

# Chi dong bo cac phieu web tu ngay nay tro di.
NGAY_BAT_DAU_PHIEU = date(2026, 10, 29)

VN = ZoneInfo("Asia/Ho_Chi_Minh")


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


GOM_PHIEU_HOM_NAY = env_bool("GOM_PHIEU_HOM_NAY", True)
CHI_XEM_TRUOC = env_bool("CHI_XEM_TRUOC", False)
XAC_NHAN_GHI_THAT = env_bool("XAC_NHAN_GHI_THAT", True)


# ------------------------- HAM TINH TOAN -------------------------
def tinh_fact_sales(that: pd.DataFrame) -> pd.DataFrame:
    """Tong hop BAN va TRA_HANG theo ngay, SKU."""
    cols = ["ngay", "sku", "so_luong_ban", "gia_tb", "co_khuyen_mai"]
    if that.empty:
        return pd.DataFrame(columns=cols)

    b = that[that["loai_giao_dich"].isin(["BAN", "TRA_HANG"])].copy()
    if b.empty:
        return pd.DataFrame(columns=cols)

    b["sl"] = -pd.to_numeric(b["so_luong_thay_doi"], errors="coerce")
    b["don_gia"] = pd.to_numeric(b["don_gia"], errors="coerce").fillna(0.0)
    b["tien"] = b["sl"] * b["don_gia"]

    g = b.groupby(["ngay", "sku"], as_index=False).agg(
        so_luong_ban=("sl", "sum"),
        tien=("tien", "sum"),
    )
    g = g[g["so_luong_ban"] != 0].copy()
    g["gia_tb"] = g["tien"] / g["so_luong_ban"]
    g["co_khuyen_mai"] = 0
    return g[cols]


def tinh_fact_inventory(
    ton_cuoi: pd.DataFrame,
    that: pd.DataFrame,
    inv_cuoi: date,
    han: date,
    gap_het: date,
) -> pd.DataFrame:
    """Tinh ton cuoi ngay tu ngay sau snapshot goc den han dong bo."""
    cols = ["ngay", "sku", "ton_cuoi_ngay", "het_hang"]
    ngay = [
        inv_cuoi + timedelta(days=i)
        for i in range(1, (han - inv_cuoi).days + 1)
    ]
    if not ngay or ton_cuoi.empty:
        return pd.DataFrame(columns=cols)

    skus = ton_cuoi["sku"].astype(str).tolist()
    if that.empty:
        net = pd.DataFrame(0.0, index=skus, columns=ngay)
    else:
        t = that.copy()
        t["ngay2"] = t["ngay"].apply(lambda d: max(d, ngay[0]))
        net = (
            t.groupby(["sku", "ngay2"])["so_luong_thay_doi"]
            .sum()
            .unstack(fill_value=0)
            .reindex(index=skus, columns=ngay, fill_value=0)
        )

    ton_goc = (
        ton_cuoi.assign(sku=ton_cuoi["sku"].astype(str))
        .set_index("sku")["ton_cuoi_ngay"]
        .reindex(skus)
    )
    ton = net.cumsum(axis=1).add(ton_goc, axis=0)
    dai = ton.stack().rename("ton_cuoi_ngay").reset_index()
    dai.columns = ["sku", "ngay", "ton_cuoi_ngay"]
    dai["het_hang"] = (
        (dai["ngay"] <= gap_het) | (dai["ton_cuoi_ngay"] <= 0)
    ).astype("int64")
    return dai[cols]


def tinh_ton_hien_tai(ton: pd.DataFrame, that: pd.DataFrame) -> pd.DataFrame:
    """Chi cong cac movement moi; movement cu da duoc tinh vao snapshot truoc."""
    out = ton.copy()
    if that.empty:
        return out

    net = that.groupby("sku")["so_luong_thay_doi"].sum()
    nhap = that[that["loai_giao_dich"] == "NHAP"].groupby("sku")[
        "so_luong_thay_doi"
    ].sum()

    out["ton_cuoi_ngay"] = (
        out["ton_cuoi_ngay"] + out["sku"].map(net).fillna(0.0)
    )
    out["hang_dang_ve"] = (
        np.maximum(
            0,
            out["hang_dang_ve"] - out["sku"].map(nhap).fillna(0.0),
        )
        .round()
        .astype("int64")
    )
    return out


def tinh_tu(cho: pd.DataFrame, hom_nay: date) -> date:
    """Tinh lai tu ngay som nhat bi anh huong; neu gom hom nay thi lui toi hom qua."""
    ngay_som_nhat = cho["ngay"].min()
    if GOM_PHIEU_HOM_NAY:
        ngay_som_nhat = min(ngay_som_nhat, hom_nay - timedelta(days=1))
    return max(NGAY_BAT_DAU_PHIEU, ngay_som_nhat)


def lap_ke_hoach(
    cho: pd.DataFrame,
    cu: pd.DataFrame,
    ton: pd.DataFrame,
    inv_cuoi: date,
    ton_cuoi: pd.DataFrame,
    han: date,
    gap_het: date,
    tu: date,
) -> dict:
    """Tao cac DataFrame moi; chua ghi bat ky du lieu nao len BigQuery."""
    la_thu = cho["la_phieu_thu"].fillna(False).astype(bool)
    sku_la = ~cho["sku"].isin(set(ton["sku"]))
    trang_thai = np.where(
        la_thu,
        "BO_QUA_PHIEU_THU",
        np.where(sku_la, "BO_QUA_SKU_LA", "DA_DONG_BO"),
    )

    that = cho[trang_thai == "DA_DONG_BO"].copy()
    # Giu day du movement tu sau snapshot goc de tinh ton lien tuc.
    # Fact_sales chi can tinh lai trong cua so tu -> han.
    tinh_day_du = pd.concat([cu, that], ignore_index=True) if not cu.empty else that.copy()
    tinh_day_du = tinh_day_du[
        (tinh_day_du["ngay"] > inv_cuoi) & (tinh_day_du["ngay"] <= han)
    ].copy()

    tinh_trong_cua_so = tinh_day_du[
        (tinh_day_du["ngay"] >= tu) & (tinh_day_du["ngay"] <= han)
    ].copy()

    fs = tinh_fact_sales(tinh_trong_cua_so)

    # Tinh ton tu snapshot goc, bao gom movement truoc ngay tu; sau do
    # chi dua cac ngay tu -> han vao staging de khong cham vao lich su.
    fi = tinh_fact_inventory(ton_cuoi, tinh_day_du, inv_cuoi, han, gap_het)
    fi = fi[fi["ngay"].between(tu, han)].copy()

    ton_moi = tinh_ton_hien_tai(ton, that)

    return {
        "la_thu": la_thu,
        "sku_la": sku_la,
        "trang_thai": trang_thai,
        "that": that,
        "fs": fs,
        "fi": fi,
        "ton_moi": ton_moi,
    }


# ------------------------- KIEM TRA DU LIEU -------------------------
def validate_plan(
    cho: pd.DataFrame,
    fs: pd.DataFrame,
    fi: pd.DataFrame,
    ton_moi: pd.DataFrame,
    tu: date,
    han: date,
) -> None:
    """Dung truoc khi tao staging va truoc khi thay the Mart."""
    required_source = {
        "movement_id", "ngay", "sku", "loai_giao_dich",
        "so_luong_thay_doi", "don_gia", "la_phieu_thu",
    }
    missing = required_source - set(cho.columns)
    if missing:
        raise ValueError(f"stock_movement_live thieu cot bat buoc: {sorted(missing)}")

    if cho["movement_id"].isna().any() or cho["movement_id"].astype(str).str.strip().eq("").any():
        raise ValueError("Co movement_id rong/NULL; dung truoc khi dong bo.")
    if cho["movement_id"].duplicated().any():
        raise ValueError("stock_movement_live co movement_id trung; can xu ly nguon truoc.")

    if fs.duplicated(["ngay", "sku"]).any():
        raise ValueError("fact_sales staging bi trung khoa (ngay, sku).")
    if fi.duplicated(["ngay", "sku"]).any():
        raise ValueError("fact_inventory staging bi trung khoa (ngay, sku).")
    if ton_moi["sku"].duplicated().any():
        raise ValueError("ton_hien_tai co SKU trung; khong the thay the an toan.")

    for name, df, cols in [
        ("fact_sales", fs, ["so_luong_ban", "gia_tb"]),
        ("fact_inventory", fi, ["ton_cuoi_ngay"]),
        ("ton_hien_tai", ton_moi, ["ton_cuoi_ngay", "hang_dang_ve"]),
    ]:
        if df.empty:
            continue
        if df[cols].isna().any().any():
            raise ValueError(f"{name} co gia tri NULL trong cot so lieu.")
        for col in cols:
            values = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
            if not np.isfinite(values).all():
                raise ValueError(f"{name}.{col} co gia tri khong huu han/khong hop le.")

    if not fs.empty and not fs["ngay"].between(tu, han).all():
        raise ValueError("fact_sales co ngay nam ngoai cua so cap nhat.")
    if not fi.empty and not fi["ngay"].between(tu, han).all():
        raise ValueError("fact_inventory co ngay nam ngoai cua so cap nhat.")


# ------------------------- BIGQUERY HELPERS -------------------------
def load_stage(
    client: bigquery.Client,
    table_id: str,
    df: pd.DataFrame,
    schema: list,
) -> None:
    """Nap vao bang staging rieng cua dot chay; bang tu het han sau 24 gio."""
    cfg = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_dataframe(df, table_id, job_config=cfg)
    job.result()
    table = client.get_table(table_id)
    table.expires = datetime.now(tz=ZoneInfo("UTC")) + timedelta(hours=24)
    client.update_table(table, ["expires"])


def run_query(client: bigquery.Client, sql: str, params: list | None = None):
    config = bigquery.QueryJobConfig(query_parameters=params or [])
    return client.query(sql, job_config=config).result()


# ------------------------- MAIN -------------------------
def main() -> None:
    if not CHI_XEM_TRUOC and not XAC_NHAN_GHI_THAT:
        raise SystemExit(
            "TU CHOI GHI: muon ghi that phai dat XAC_NHAN_GHI_THAT=true."
        )

    client = bigquery.Client(project=PROJECT_ID)
    q = lambda sql: client.query(sql).to_dataframe()
    hom_nay = datetime.now(VN).date()

    if hom_nay < NGAY_BAT_DAU_PHIEU:
        raise SystemExit(
            f"Hôm nay {hom_nay} chua toi NGAY_BAT_DAU_PHIEU {NGAY_BAT_DAU_PHIEU}."
        )

    han = hom_nay if GOM_PHIEU_HOM_NAY else hom_nay - timedelta(days=1)

    # Kiem tra cac bang dich va bang nhat ky truoc khi doc/ghi.
    required_tables = [
        f"{PROJECT_ID}.{VH}.stock_movement_live",
        f"{PROJECT_ID}.{VH}.nhat_ky_dong_bo",
        f"{PROJECT_ID}.{VH}.lan_chay_mo_hinh",
        f"{PROJECT_ID}.{MART}.fact_sales",
        f"{PROJECT_ID}.{MART}.fact_inventory",
        f"{PROJECT_ID}.{MART}.ton_hien_tai",
        f"{PROJECT_ID}.{MART}.sku_chon_v2",
    ]
    for table_id in required_tables:
        client.get_table(table_id)

    # Chua dong bo: doc nguon; khong co lenh DELETE/UPDATE nao tren bang nguon.
    cho = q(
        f"""
        SELECT m.*
        FROM `{PROJECT_ID}.{VH}.stock_movement_live` AS m
        LEFT JOIN `{PROJECT_ID}.{VH}.nhat_ky_dong_bo` AS n
          ON n.movement_id = m.movement_id
        WHERE n.movement_id IS NULL
          AND m.ngay <= DATE '{han}'
        """
    )

    if cho.empty:
        print(f"Khong co phieu moi can dong bo (gom den {han}).")
        return

    cho["ngay"] = pd.to_datetime(cho["ngay"]).dt.date
    cho["sku"] = cho["sku"].fillna("").astype(str).str.strip().str.upper()
    cho["movement_id"] = cho["movement_id"].astype(str).str.strip()

    ton = q(
        f"""
        SELECT sku, ton_cuoi_ngay, hang_dang_ve
        FROM `{PROJECT_ID}.{MART}.ton_hien_tai`
        """
    )
    ton["sku"] = ton["sku"].astype(str).str.strip().str.upper()

    tu = tinh_tu(cho, hom_nay)

    # Snapshot goc phai la ngay truoc cua so tinh lai.
    inv_df = q(
        f"""
        SELECT MAX(ngay) AS d
        FROM `{PROJECT_ID}.{MART}.fact_inventory`
        WHERE ngay < DATE '{tu}'
        """
    )
    if inv_df.empty or pd.isna(inv_df.loc[0, "d"]):
        raise ValueError(
            f"Khong tim thay snapshot fact_inventory truoc ngay {tu}; dung de tranh tinh ton sai."
        )
    inv_cuoi = pd.to_datetime(inv_df.loc[0, "d"]).date()

    # Lay lai movement da dong bo tu sau snapshot goc (khong chi tu ngay tu).
    # Neu bo qua doan nay, ton dau ngay tu se thieu cac bien dong lich su.
    cu = q(
        f"""
        SELECT m.*
        FROM `{PROJECT_ID}.{VH}.stock_movement_live` AS m
        JOIN `{PROJECT_ID}.{VH}.nhat_ky_dong_bo` AS n
          ON n.movement_id = m.movement_id
        WHERE n.trang_thai = 'DA_DONG_BO'
          AND m.ngay > DATE '{inv_cuoi}'
          AND m.ngay <= DATE '{han}'
        """
    )
    if not cu.empty:
        cu["ngay"] = pd.to_datetime(cu["ngay"]).dt.date
        cu["sku"] = cu["sku"].fillna("").astype(str).str.strip().str.upper()

    ton_cuoi = q(
        f"""
        SELECT sku, ton_cuoi_ngay
        FROM `{PROJECT_ID}.{MART}.fact_inventory`
        WHERE ngay = DATE '{inv_cuoi}'
        """
    )
    ton_cuoi["sku"] = ton_cuoi["sku"].astype(str).str.strip().str.upper()
    if ton_cuoi.empty:
        raise ValueError(f"Snapshot fact_inventory ngay {inv_cuoi} khong co du lieu.")

    gap_het = NGAY_BAT_DAU_PHIEU - timedelta(days=1)
    kh = lap_ke_hoach(
        cho, cu, ton, inv_cuoi, ton_cuoi, han, gap_het, tu
    )
    la_thu = kh["la_thu"]
    sku_la = kh["sku_la"]
    trang_thai = kh["trang_thai"]
    that = kh["that"]
    fs = kh["fs"]
    fi = kh["fi"]
    ton_moi = kh["ton_moi"]

    # Giu logic danh dau khoang du lieu chua co so ban that.
    fi_gap = pd.DataFrame(
        columns=["ngay", "sku", "ton_cuoi_ngay", "het_hang"]
    )
    if inv_cuoi < gap_het and inv_cuoi > NGAY_DU_LIEU_THAT_CUOI:
        a = NGAY_DU_LIEU_THAT_CUOI + timedelta(days=1)
        fi_gap = q(
            f"""
            SELECT ngay, sku, ton_cuoi_ngay
            FROM `{PROJECT_ID}.{MART}.fact_inventory`
            WHERE ngay BETWEEN DATE '{a}' AND DATE '{inv_cuoi}'
              AND sku IN (
                SELECT sku FROM `{PROJECT_ID}.{MART}.sku_chon_v2`
              )
            """
        )
        fi_gap["het_hang"] = 1

    fi_all = pd.concat([fi, fi_gap], ignore_index=True)
    validate_plan(cho, fs, fi, ton_moi, tu, han)

    # Validate them cac khoa va schema logic cua du lieu se dua vao staging.
    if not fi_gap.empty and fi_gap.duplicated(["ngay", "sku"]).any():
        raise ValueError("Du lieu fi_gap co khoa (ngay, sku) trung.")
    if fi_all.duplicated(["ngay", "sku"]).any():
        raise ValueError("fact_inventory sau khi ghep fi_gap bi trung khoa.")

    print(
        f"Ngay gom: {han} | movement moi: {len(cho)} | "
        f"bo qua phieu thu: {int(la_thu.sum())} | "
        f"bo qua SKU la: {int((sku_la & ~la_thu).sum())} | "
        f"movement hop le: {len(that)}"
    )
    print(f"Cua so tinh lai Mart: {tu} -> {han}")
    print(f"fact_sales: {len(fs)} dong; fact_inventory: {len(fi_all)} dong")
    print(f"ton_hien_tai: {len(ton_moi)} SKU")

    if CHI_XEM_TRUOC:
        print("\nCHE DO XEM TRUOC: KHONG GHI VAO BAT KY BANG NAO.")
        print("Bang nguon stock_movement_live duoc giu nguyen.")
        return

    # BigQuery free tier khong cho phep DML. Dung Load Jobs (WRITE_TRUNCATE/WRITE_APPEND)
    # thay cho DELETE/INSERT/MERGE. Doc day du bang dich de giu nguyen lich su.
    # Concurrency cua workflow phai giu cancel-in-progress=false.
    table_fs = f"{PROJECT_ID}.{MART}.fact_sales"
    table_fi = f"{PROJECT_ID}.{MART}.fact_inventory"
    table_ton = f"{PROJECT_ID}.{MART}.ton_hien_tai"
    table_nk = f"{PROJECT_ID}.{VH}.nhat_ky_dong_bo"
    table_run = f"{PROJECT_ID}.{VH}.lan_chay_mo_hinh"

    # Lay schema that tu BigQuery, khong tu y doi kieu cot.
    schemas = {
        table_fs: client.get_table(table_fs).schema,
        table_fi: client.get_table(table_fi).schema,
        table_ton: client.get_table(table_ton).schema,
        table_nk: client.get_table(table_nk).schema,
        table_run: client.get_table(table_run).schema,
    }

    fs_old = q(f"SELECT * FROM `{table_fs}`")
    fi_old = q(f"SELECT * FROM `{table_fi}`")
    nk_old = q(f"SELECT * FROM `{table_nk}`")

    # Chi thay the phan ngay bi anh huong; toan bo lich su ngoai cua so duoc giu.
    fs_old["ngay"] = pd.to_datetime(fs_old["ngay"]).dt.date
    fi_old["ngay"] = pd.to_datetime(fi_old["ngay"]).dt.date
    fs_keep = fs_old[~fs_old["ngay"].between(tu, han)].copy()
    fi_keep = fi_old[~fi_old["ngay"].between(tu, han)].copy()
    fs_all = pd.concat([fs_keep, fs], ignore_index=True)
    fi_all_full = pd.concat([fi_keep, fi_all], ignore_index=True)

    # Nhat ky la bang dedup theo movement_id; giu nguyen cac dong da co.
    nk_all = pd.concat([nk_old, nk], ignore_index=True)
    nk_all = nk_all.drop_duplicates(subset=["movement_id"], keep="first")

    now_utc = pd.Timestamp.now(tz="UTC")
    run_row = pd.DataFrame([{
        "thoi_diem_chay": now_utc,
        "loai": "DONG_BO",
        "ngay_du_lieu_cuoi": han,
        "trang_thai": "THANH_CONG",
        "ghi_chu": f"{len(that)} movement hop le; run_id={run_id}",
    }])

    def load_replace(table_id: str, frame: pd.DataFrame) -> None:
        cfg = bigquery.LoadJobConfig(
            schema=schemas[table_id],
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        client.load_table_from_dataframe(frame, table_id, job_config=cfg).result()

    # Backup trong bo nho de phuc hoi neu mot load job that bai giua chung.
    # Khong co lenh SQL DML nao duoc su dung.
    backups = {
        table_fs: fs_old,
        table_fi: fi_old,
        table_ton: ton.copy(),
        table_nk: nk_old,
    }
    changed = []
    try:
        load_replace(table_fs, fs_all)
        changed.append(table_fs)
        load_replace(table_fi, fi_all_full)
        changed.append(table_fi)
        load_replace(table_ton, ton_moi)
        changed.append(table_ton)
        load_replace(table_nk, nk_all)
        changed.append(table_nk)

        cfg_run = bigquery.LoadJobConfig(
            schema=schemas[table_run],
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        client.load_table_from_dataframe(
            run_row, table_run, job_config=cfg_run
        ).result()

        print("\nDONG BO THANH CONG bang BigQuery Load Jobs (khong dung DML).")
        print("Lich su fact_sales/fact_inventory va cac movement goc duoc giu nguyen.")
        print("Luu y: BigQuery khong co transaction nguyen tu cho nhieu Load Jobs.")
    except Exception:
        # Neu loi duoc bat trong cung tien trinh, khoi phuc cac bang da ghi.
        # Workflow khong duoc cancel giua cac buoc; xem concurrency ben duoi.
        for table_id in reversed(changed):
            try:
                load_replace(table_id, backups[table_id])
            except Exception as restore_error:
                print(f"CANH BAO: khong khoi phuc duoc {table_id}: {restore_error}")
        raise


if __name__ == "__main__":
    main()
