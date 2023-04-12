import argparse
import json
import os
from base64 import b64decode, b64encode
from datetime import datetime
from getpass import getpass
from hashlib import pbkdf2_hmac

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from google.cloud import bigquery
from google.oauth2.service_account import Credentials


def _backup(filename: str) -> None:
    if os.path.exists(filename):
        os.rename(filename, filename + '.bak')


def _get_data_from_big_query(query: str, filename: str, token: str) -> None:
    _backup(filename)
    token_decoded = json.loads(token)
    credentials = Credentials.from_service_account_info(token_decoded)
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


def update_pypi_metadata(big_query_token: str) -> None:
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
    _get_data_from_big_query(QUERY, filename, big_query_token)


def update_pypi_downloads(big_query_token: str) -> None:
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
    _get_data_from_big_query(QUERY, filename, big_query_token)


def _hash_password(password: str) -> bytes:
    salt = b64decode('+WnJjj92LcPFG6+ZVF9sDw3ouVpiy35NsVfg5EYXUzG57xjZfRU5Ag==')
    password_hash = pbkdf2_hmac('sha256', password.encode(), salt, 5_000_000)
    assert len(password_hash) == 32, 'Password hash must be 32 bytes long'
    return password_hash


def encode_token() -> None:
    """Encode provided token with user password."""
    secret_token_base64 = input('Enter base64 encoded token for BigQuery:').strip()
    secret_token = b64decode(secret_token_base64)
    password = getpass('Enter new password for token:')
    password2 = getpass('Enter password for token again:')
    if password != password2:
        print('Passwords do not match!')
        return
    password_hash = _hash_password(password)
    encoded_token = Fernet(b64encode(password_hash)).encrypt(secret_token)
    encoded_token_base64 = b64encode(encoded_token).decode()
    print('Encoded token (add it to .env file):')
    print(f'BIG_QUERY_TOKEN="{encoded_token_base64}"')


def decode_token() -> str:
    """Decode env token with user password."""
    password = getpass('Enter password for token:')
    password_hash = _hash_password(password)
    encoded_token = os.environ['BIG_QUERY_TOKEN']
    decoded_token_base64 = Fernet(b64encode(password_hash)).decrypt(b64decode(encoded_token))
    decoded_token = b64decode(decoded_token_base64).decode()
    return decoded_token


def main() -> None:
    """Run the update script."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--password', action='store_true', help='Encode token with password')
    parser.add_argument('--metadata', action='store_true', help='Update metadata from BigQuery')
    parser.add_argument('--downloads', action='store_true', help='Update downloads from BigQuery')
    parser.add_argument('--all', action='store_true', help='Update all data')
    args = parser.parse_args()
    load_dotenv()
    if args.password:
        encode_token()
        return
    try:
        big_query_token = decode_token()
    except InvalidToken:
        print('Wrong password')
        return
    if args.all or args.metadata:
        update_pypi_metadata(big_query_token=big_query_token)
    if args.all or args.downloads:
        update_pypi_downloads(big_query_token=big_query_token)


if __name__ == '__main__':
    main()
