"""Quản lý kết nối BigQuery cho ShelfSense (dùng được với Streamlit và FastAPI)."""
import os
from functools import lru_cache

import google.auth
from google.cloud import bigquery
from google.oauth2 import service_account

PROJECT_ID = "he-thong-ton-kho-ban-le"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


@lru_cache(maxsize=1)
def get_bigquery_client() -> bigquery.Client:
    """
    Thứ tự xác thực:
    1. GOOGLE_APPLICATION_CREDENTIALS (service-account.json).
    2. Streamlit secrets (nếu chạy trong Streamlit).
    3. Google ADC.
    """
    credential_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    # Ưu tiên service account được khai báo trong môi trường của tiến trình.
    if credential_path:
        if not os.path.isfile(credential_path):
            raise RuntimeError(
                "GOOGLE_APPLICATION_CREDENTIALS đang trỏ tới file không tồn tại: "
                + credential_path
            )
        try:
            credentials = service_account.Credentials.from_service_account_file(
                credential_path,
                scopes=SCOPES,
            )
            return bigquery.Client(project=PROJECT_ID, credentials=credentials)
        except Exception as exc:
            raise RuntimeError(
                "Không thể tạo BigQuery Client từ service-account.json: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # Chỉ thử Streamlit secrets khi có thể import và đọc được.
    try:
        import streamlit as st

        info = dict(st.secrets["gcp_service_account"])
    except Exception:
        info = None

    if info:
        try:
            credentials = service_account.Credentials.from_service_account_info(
                info,
                scopes=SCOPES,
            )
            return bigquery.Client(project=PROJECT_ID, credentials=credentials)
        except Exception as exc:
            raise RuntimeError(
                "Không thể tạo BigQuery Client từ Streamlit secrets: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # Cuối cùng mới dùng Application Default Credentials.
    try:
        credentials, detected_project = google.auth.default(scopes=SCOPES)
        return bigquery.Client(project=PROJECT_ID, credentials=credentials)
    except Exception as exc:
        raise RuntimeError(
            "Không thể khởi tạo BigQuery Client. "
            f"Project cấu hình: {PROJECT_ID}; "
            f"ADC project phát hiện: {locals().get('detected_project', None)}; "
            f"lỗi gốc: {type(exc).__name__}: {exc}"
        ) from exc


def read_query(query: str, job_config=None):
    """Chạy SQL trên BigQuery và trả kết quả về DataFrame."""
    try:
        client = get_bigquery_client()
        query_job = client.query(query, job_config=job_config)
        return query_job.result().to_dataframe(create_bqstorage_client=False)
    except Exception as exc:
        raise RuntimeError(
            f"Lỗi khi đọc dữ liệu BigQuery: {type(exc).__name__}: {exc}"
        ) from exc


def test_connection() -> bool:
    """Kiểm tra quyền chạy truy vấn BigQuery."""
    df = read_query("SELECT 1 AS ket_noi")
    return not df.empty and int(df.iloc[0]["ket_noi"]) == 1
