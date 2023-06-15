import argparse
import json
import logging
import logging.config
import os
import re
import sqlite3
from datetime import datetime
from time import time
from typing import Iterable, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import bindparam, create_engine, insert, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass, Session, mapped_column

app = FastAPI()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger('web')
db = create_engine("sqlite:///data/data.sqlite")


class BaseModel(MappedAsDataclass, DeclarativeBase):
    pass


class Package(BaseModel):
    __tablename__ = 'packages'

    name: Mapped[str] = mapped_column(primary_key=True)
    name_lower: Mapped[str]
    name_normalized: Mapped[str] = mapped_column(index=True)
    version: Mapped[str]
    upload_time: Mapped[int]
    home_page: Mapped[str]
    summary: Mapped[str]
    summary_lower: Mapped[str]
    downloads: Mapped[int]
    stars: Mapped[int]
    forks: Mapped[int]
    github_owner: Mapped[Optional[str]]
    github_name: Mapped[Optional[str]]
    github_url: Mapped[Optional[str]]
    github_timestamp: Mapped[int]


def _read_json_by_line(filename: str) -> Iterable[dict]:
    with open(filename, 'r') as f:
        for line in f:
            if not line:
                continue
            yield json.loads(line)  # type: ignore[no-any-return]


def init_data() -> None:
    def get_github_url(url: str, urls_with_names: list[str]) -> tuple[str, str] | None:
        urls = []
        if url:
            urls.append(url)
        for url_with_name in urls_with_names:
            url = url_with_name.split(',', 1)[1].strip()
            urls.append(url)
        for url in urls:
            if m := re.match(r'https:\/\/github.com\/([\w\.-]+)\/([\w\.-]+)\/?', url):
                return m.group(1), m.group(2)
        return None

    if os.path.exists(os.path.join('data', 'data.sqlite')):
        return

    print('Initializing data...')

    old_packages: dict[str, Package] = {}
    if os.path.exists(os.path.join('data', 'data.sqlite.bak')):
        print('Reading github info from backup db...')
        db_backup = create_engine("sqlite:///data/data.sqlite.bak")
        with Session(db_backup) as session:
            packages = session.execute(select(Package).where(Package.github_timestamp > 0))
            for old_db_package in packages.scalars().all():
                old_packages[old_db_package.name] = old_db_package

    print('Creating new db...')

    BaseModel.metadata.create_all(db)
    raw_connection: sqlite3.Connection = db.raw_connection()  # type: ignore
    insert_sql = str(insert(Package).compile())
    t1 = time()
    for data in _read_json_by_line(os.path.join('data', 'metadata_lines.json')):
        name = data['name']
        summary = data.get('summary') or ''
        timestamp = datetime.strptime(data['upload_time'][:10], '%Y-%m-%d').timestamp()
        homepage = data.get('home_page') or ''
        urls_with_names = data.get('project_urls') or []
        github_names = get_github_url(homepage, urls_with_names)
        old_package: Package | None = old_packages.get(name)
        package = dict(
            name=name,
            name_lower=name.lower(),
            name_normalized=name.lower().replace('.', '-'),
            version=data['version'],
            upload_time=int(timestamp),
            home_page=homepage,
            summary=summary,
            summary_lower=summary.lower(),
            downloads=0,
            stars=old_package.stars if old_package else 0,
            forks=old_package.forks if old_package else 0,
            github_owner=github_names[0] if github_names else None,
            github_name=github_names[1] if github_names else None,
            github_url=old_package.github_url if old_package else '',
            github_timestamp=old_package.github_timestamp if old_package else 0,
        )
        raw_connection.execute(insert_sql, package)
    raw_connection.commit()
    t2 = time()
    print(f'insert metadata: {t2 - t1:.2f}s')

    update_sql = str(
        update(Package).where(Package.name_normalized == bindparam('name')).values(
            downloads=bindparam('download_count'),
        ).compile(),
    )
    for data in _read_json_by_line(os.path.join('data', 'downloads_lines.json')):
        raw_connection.execute(update_sql, data)
    raw_connection.commit()
    t3 = time()
    print(f'insert downloads: {t3 - t2:.2f}s')

    update_sql = str(
        update(Package).where(Package.name == bindparam('name')).values(
            stars=bindparam('stargazerCount'),
            forks=bindparam('forkCount'),
            github_url=bindparam('url'),
            github_timestamp=bindparam('timestamp'),
        ).compile(),
    )
    if os.path.exists(os.path.join('data', 'github_lines.json')):
        for data in _read_json_by_line(os.path.join('data', 'github_lines.json')):
            data.setdefault('stargazerCount', 0)
            data.setdefault('forkCount', 0)
            data.setdefault('url', None)
            raw_connection.execute(update_sql, data)
        raw_connection.commit()
        t4 = time()
        print(f'insert github: {t4 - t3:.2f}s')
    print('Initializing done.')


@app.get("/")
async def root() -> Response:
    return templates.TemplateResponse('index.html', {"request": {}})


@app.get("/search/")
async def search(query: str | None = None, limit: int = 50, order: str = 'downloads') -> Response:
    GLOBAL_LIMIT = 2000
    if order not in ('downloads', 'stars', 'forks', 'latest_upload'):
        order = 'downloads'
    if not query or not query.strip():
        return RedirectResponse(url='/', status_code=302)
    limit = min(limit, GLOBAL_LIMIT)

    sql = select(Package).where(
        Package.name_lower.like(f'%{query.lower()}%')
        | Package.summary_lower.like(f'%{query.lower()}%'),
    ).order_by(getattr(Package, order).desc())

    with Session(db) as session:
        packages = session.scalars(sql.limit(GLOBAL_LIMIT)).all()
        total = len(packages)
        if await update_github_info(packages):
            packages = session.scalars(sql.limit(limit)).all()

    packages = packages[:limit]
    return templates.TemplateResponse('search.html', {
        'request': {},
        'packages': packages,
        'showed': len(packages),
        'total': total,
        'query': query,
        'limit': limit,
        'order': order,
    })


async def update_github_info(packages: list[Package]) -> bool:
    github_requests = []
    for package in packages:
        if package.github_url or package.github_timestamp:
            continue
        if not package.github_owner or not package.github_name:
            continue
        github_requests.append(package)
    if not github_requests:
        return False
    result_packages = []
    limit = 600
    for i in range(0, (len(packages) - 1) // limit + 1):
        packages_batch = await get_github_info(packages[i*limit:(i+1)*limit])
        result_packages.extend(packages_batch)
    with Session(db) as session:
        session.bulk_save_objects(result_packages)
        session.commit()
    return True


async def get_github_info(packages: list[Package]) -> list[Package]:
    """Get info about github repos via GraphQL endpoint."""
    logger.info(f'github call: {len(packages)}')
    token = os.environ.get('GITHUB_TOKEN')
    github_api_url = 'https://api.github.com/graphql'
    query_part_template = """
        {key}: repository(owner: "{owner}", name: "{name}") {{
            forkCount
            stargazerCount
            url
        }}
    """
    query_parts = []
    for i, package in enumerate(packages):
        key = f'r{i}'
        query_parts.append(
            query_part_template.format(
                key=key,
                owner=package.github_owner,
                name=package.github_name,
            ),
        )
    query = '{' + ',\n'.join(query_parts) + '}'

    request = await httpx.AsyncClient().post(
        github_api_url,
        headers={'Authorization': f'Bearer {token}'},
        json={'query': query},
        timeout=30,
    )
    assert request.status_code == 200, request.text
    assert 'data' in request.json(), f'resp: {request.json()}, query: {query}'
    response = request.json()
    for i, package in enumerate(packages):
        key = f'r{i}'
        github_result = response['data'].get(key) or {}
        package.github_timestamp = int(time())
        package.github_url = github_result.get('url')
        package.forks = github_result.get('forkCount') or 0
        package.stars = github_result.get('stargazerCount') or 0
    return packages


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reload', action='store_true')
    parser.add_argument('--ip', type=str, default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()

    log_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'default': {
                'format': '%(asctime)s %(levelname)-7s %(message)s',
                'datefmt': '%Y-%m-%d %H:%M:%S',
            },
            'access': {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(asctime)s %(levelname)-7s %(client_addr)s - "%(request_line)s" %(status_code)s',  # noqa: E501
                'datefmt': '%Y-%m-%d %H:%M:%S',
            },
        },
        'handlers': {
            'default': {
                'level': 'INFO',
                'formatter': 'default',
                'class': 'logging.StreamHandler',
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
            },

        },
        'loggers': {
            '': {
                'handlers': ['default'],
                'level': 'INFO',
                'propagate': True,
            },
            "uvicorn.access": {
                "handlers": ["access"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }

    init_data()

    uvicorn.run(
        'main:app',
        host=args.ip,
        port=args.port,
        reload=args.reload,
        env_file='.env',
        log_config=log_config,
    )
