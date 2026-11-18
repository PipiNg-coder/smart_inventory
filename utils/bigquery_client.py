
"""Quản lý kết nối BigQuery cho ShelfSense (Streamlit, FastAPI, Render)."""

import os
import json
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
    1. GOOGLE_APPLICATION_CREDENTIALS (file JSON).
    2. BIGQUERY_SERVICE_ACCOUNT_JSON (JSON từ Environment Variables).
    3. Streamlit secrets.
    4. Google Application Default Credentials.
    """

    # 1. Đọc Service Account từ file JSON
    credential_path = os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS", ""
    ).strip()

    if credential_path:
        if not os.path.isfile(credential_path):
            raise RuntimeError(
                "GOOGLE_APPLICATION_CREDENTIALS trỏ tới file không tồn tại: "
                + credential_path
            )

        try:
            credentials = (
                service_account.Credentials.from_service_account_file(
                    credential_path,
                    scopes=SCOPES,
                )
            )
            return bigquery.Client(
                project=PROJECT_ID,
                credentials=credentials,
            )
        except Exception as exc:
            raise RuntimeError(
                "Không thể tạo BigQuery Client từ file Service Account: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # 2. Đọc Service Account JSON từ Render Environment Variables
    service_account_json = os.getenv(
        "BIGQUERY_SERVICE_ACCOUNT_JSON", ""
    ).strip()

    if service_account_json:
        try:
            info = json.loads(service_account_json)

            if not isinstance(info, dict):
                raise ValueError("Nội dung Service Account phải là JSON object.")

            credentials = (
                service_account.Credentials.from_service_account_info(
                    info,
                    scopes=SCOPES,
                )
            )

            return bigquery.Client(
                project=PROJECT_ID,
                credentials=credentials,
            )

        except Exception as exc:
            raise RuntimeError(
                "Không thể tạo BigQuery Client từ "
                "BIGQUERY_SERVICE_ACCOUNT_JSON: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # 3. Đọc Streamlit secrets
    try:
        import streamlit as st

        info = dict(st.secrets["gcp_service_account"])

    except Exception:
        info = None

    if info:
        try:
            credentials = (
                service_account.Credentials.from_service_account_info(
                    info,
                    scopes=SCOPES,
                )
            )

            return bigquery.Client(
                project=PROJECT_ID,
                credentials=credentials,
            )

        except Exception as exc:
            raise RuntimeError(
                "Không thể tạo BigQuery Client từ Streamlit secrets: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # 4. Google Application Default Credentials
    try:
        credentials, detected_project = google.auth.default(
            scopes=SCOPES
        )

        return bigquery.Client(
            project=PROJECT_ID,
            credentials=credentials,
        )

    except Exception as exc:
        raise RuntimeError(
            "Không thể khởi tạo BigQuery Client. "
            f"Project cấu hình: {PROJECT_ID}; "
            f"ADC project phát hiện: "
            f"{locals().get('detected_project', None)}; "
            f"Lỗi gốc: {type(exc).__name__}: {exc}"
        ) from exc


def read_query(query: str, job_config=None):
    """Chạy SQL trên BigQuery và trả kết quả về DataFrame."""

    try:
        client = get_bigquery_client()
        query_job = client.query(
            query,
            job_config=job_config,
        )

        return query_job.result().to_dataframe(
            create_bqstorage_client=False
        )

    except Exception as exc:
        raise RuntimeError(
            f"Lỗi khi đọc dữ liệu BigQuery: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def test_connection() -> bool:
    """Kiểm tra quyền chạy truy vấn BigQuery."""

    df = read_query("SELECT 1 AS ket_noi")

    return (
        not df.empty
        and int(df.iloc[0]["ket_noi"]) == 1
    )
