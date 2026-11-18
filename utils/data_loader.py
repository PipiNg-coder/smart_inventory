
"""Các hàm đọc và chuẩn bị dữ liệu cho ShelfSense."""

from utils.bigquery_client import read_query


def load_data(query, job_config=None):
    """
    Đọc dữ liệu từ BigQuery thông qua bigquery_client.py.

    Args:
        query: Câu lệnh SQL.
        job_config: Cấu hình truy vấn nếu cần.

    Returns:
        DataFrame chứa kết quả truy vấn.
    """
    try:
        df = read_query(query, job_config=job_config)
        return df

    except Exception as error:
        raise RuntimeError(
            f"Không thể tải dữ liệu từ BigQuery: {error}"
        ) from error

