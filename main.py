import argparse
import json
import logging
import logging.config
import os
import re
from datetime import datetime
from time import time

import httpx
import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger('web')
DATA = None


def _read_file(filename: str) -> dict:
    if not os.path.exists(filename):
        return {}
    with open(filename, 'r') as f:
        return json.load(f)  # type: ignore[no-any-return]


def _write_file(filename: str, data: dict) -> None:
    with open(filename, 'w') as f:
        json.dump(data, f)


def combine_data() -> dict:
    downloads = _read_file(os.path.join('data', 'downloads.json'))
    metadata = _read_file(os.path.join('data', 'metadata.json'))
    github = _read_file(os.path.join('data', 'github.json'))
    data = {}
    for name, meta in metadata.items():
        data[name] = {
            'metadata': meta,
            'github': github.get(name, {}),
            'downloads': downloads.get(name, {}),
        }
    return data


@app.get("/")
async def root() -> Response:
    return templates.TemplateResponse('index.html', {"request": {}})


@app.get("/search/")
async def search(query: str | None = None, limit: int = 5, order: str = 'downloads') -> Response:
    global DATA
    if DATA is None:
        DATA = combine_data()

    GLOBAL_LIMIT = 2000
    if order not in ('downloads', 'stars', 'forks', 'latest_upload'):
        order = 'downloads'
    if not query or not query.strip():
        return RedirectResponse(url='/', status_code=302)
    found_name_start = set()
    found_name = set()
    found_summary = set()
    for name, data in DATA.items():
        if name.startswith(query):
            found_name_start.add(name)
        elif query in name:
            found_name.add(name)
        elif query.lower() in (data['metadata']['summary'] or '').lower():
            found_summary.add(name)
    names = list(found_name_start) + list(found_name) + list(found_summary)
    total_count = len(names)
    limit = min(limit, GLOBAL_LIMIT)
    names = names[:limit]

    await update_github_info(names)

    packages = []
    for name in names:
        metadata = DATA[name]['metadata']
        github = DATA[name]['github'] or {}
        downloads = DATA[name]['downloads'] or {}
        latest_upload = datetime.strptime(metadata['upload_time'][:10], '%Y-%m-%d').date()
        packages.append({
            'name': name,
            'summary': metadata.get('summary', ''),
            'version': metadata.get('version', ''),
            'url': metadata.get('project_url'),
            'stars': github.get('stargazerCount', 0),
            'forks': github.get('forkCount', 0),
            'github': github.get('url'),
            'latest_upload': latest_upload,
            'downloads': downloads.get('download_count', 0),
        })

    packages = sorted(packages, key=lambda x: x.get(order, ''), reverse=True)

    return templates.TemplateResponse('search.html', {
        'request': {},
        'packages': packages,
        'showed': len(names),
        'total': total_count,
        'query': query or '',
        'limit': limit,
        'order': order,
    })


async def update_github_info(names: list[str]) -> None:
    def get_github_url(name: str) -> tuple[str, str] | None:
        metadata = DATA[name]['metadata']
        urls = []
        for keyword in ('home_page', 'package_url', 'project_url'):
            if metadata.get(keyword):
                urls.append(metadata[keyword])
        project_urls_list = metadata.get('project_urls') or []
        for url_with_name in project_urls_list:
            url = url_with_name.split(',', 1)[1].strip()
            urls.append(url)
        for url in urls:
            if m := re.match(r'https:\/\/github.com\/([\w\.-]+)\/([\w\.-]+)\/?', url):
                return m.group(1), m.group(2)
        return None

    github_data = _read_file(os.path.join('data', 'github.json'))
    github_requests = {}
    for name in names:
        if name in github_data:
            continue
        github_url = get_github_url(name)
        if not github_url:
            continue
        github_requests[name] = github_url
    if github_requests:
        github_results = await get_github_info(github_requests)
        for name, github_result in github_results.items():
            github_data[name] = github_result
            DATA[name]['github'] = github_result
        _write_file(os.path.join('data', 'github.json'), github_data)


async def get_github_info(repos: dict[str, tuple[str, str]]) -> dict[str, dict]:
    """Get info about github repos via GraphQL endpoint."""
    logger.info(f'github call: {len(repos)}')
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
    for i, name in enumerate(repos.keys()):
        owner, repo_name = repos[name]
        key = f'r{i}'
        query_parts.append(query_part_template.format(key=key, owner=owner, name=repo_name).strip())
    query = '{' + ',\n'.join(query_parts) + '}'

    request = await httpx.AsyncClient().post(
        github_api_url,
        headers={'Authorization': f'Bearer {token}'},
        json={'query': query},
    )
    assert request.status_code == 200, request.text
    assert 'data' in request.json(), f'resp: {request.json()}, query: {query}'
    response = request.json()
    result = {}
    for i, name in enumerate(repos.keys()):
        key = f'r{i}'
        result[name] = response['data'].get(key) or {}
        result[name]['timestamp'] = int(time())
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reload', action='store_true')
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

    uvicorn.run(
        'main:app',
        port=args.port,
        reload=args.reload,
        env_file='.env',
        log_config=log_config,
    )
