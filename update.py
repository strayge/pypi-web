import argparse
import json
import os
from base64 import b64decode
from datetime import datetime

from dotenv import load_dotenv
from google.cloud import bigquery
from google.oauth2.service_account import Credentials


def _backup(filename: str) -> None:
    if os.path.exists(filename):
        os.rename(filename, filename + '.bak')


def _get_data_from_big_query(query: str, filename: str) -> None:
    _backup(filename)
    credentials = Credentials.from_service_account_info(
        json.loads(b64decode(os.environ['QUERY_KEY'])),
    )
    client = bigquery.Client(credentials=credentials)
    print('Querying BigQuery...')
    query_job = client.query(query)
    rows = query_job.result()
    processed = query_job.total_bytes_processed or 0
    billed = query_job.total_bytes_billed or 0
    print(f'Processed {processed // 1024 // 1024} MB, billed {billed // 1024 // 1024} MB')
    print('Reading results...')
    with open(filename, 'w') as f:
        for row in rows:
            row_dict = dict(row)
            # convent non-serializable objects to string
            for key, value in row_dict.items():
                if isinstance(value, datetime):
                    row_dict[key] = str(value)
            f.write(json.dumps(row_dict) + '\n')


def update_pypi_metadata() -> None:
    """Update the metadata_bigquery.json file."""
    # was required 1.2 GB process data quota
    QUERY = """
    SELECT
        AS VALUE ARRAY_AGG(table1 ORDER BY `upload_time` DESC LIMIT 1)[OFFSET(0)]
    FROM (
    SELECT
        name,
        version,
        upload_time,
        home_page,
        project_urls,
        summary,
    FROM
        `bigquery-public-data.pypi.distribution_metadata`) AS table1
    GROUP BY
        name
    """
    filename = os.path.join('data', 'metadata_lines.json')
    print('Updating PyPI metadata...')
    _get_data_from_big_query(QUERY, filename)


def update_pypi_downloads() -> None:
    """Update the downloads.json file."""
    # was required 22 GB process data quota (minimum is 8.7 GB)
    QUERY = """
        SELECT
            project as name,
            COUNT(*) as download_count,
        FROM `bigquery-public-data.pypi.file_downloads`
        WHERE
            timestamp BETWEEN
                TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL -2 DAY)
                AND TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL -1 DAY)
            AND details.installer.name = "pip"
        GROUP BY
            name
        ORDER BY
            name ASC
    """
    filename = os.path.join('data', 'downloads_lines.json')
    print('Updating PyPI downloads...')
    _get_data_from_big_query(QUERY, filename)


def main() -> None:
    """Run the update script."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--metadata', action='store_true', help='Update metadata from BigQuery')
    parser.add_argument('--downloads', action='store_true', help='Update downloads from BigQuery')
    parser.add_argument('--all', action='store_true', help='Update all data')
    args = parser.parse_args()
    load_dotenv()
    if args.all or args.metadata:
        update_pypi_metadata()
    if args.all or args.downloads:
        update_pypi_downloads()


if __name__ == '__main__':
    main()
