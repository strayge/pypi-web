# PyPI Web

![preview](https://user-images.githubusercontent.com/2664578/221611530-f67ca2b2-df3f-4229-bf3d-2f19db2988aa.png)

Custom PyPI web interface for search with additional information:

* Downloads
* GitHub stars
* GitHub forks

Plus sorting by this fields.

## Configuration

`.env` file is used to configure the application. The following variables are used:

* GITHUB_TOKEN - legacy Github token to access GraphQL API
* QUERY_KEY - Google Service Account key (JSON with base64 encoding) to access Google BigQuery  
(guide for creation can be found at [pypinfo readme](https://github.com/ofek/pypinfo#installation))

## Running

```sh
python main.py --port 8080
```

## Update database

```sh
python update.py --all
```

* `--all` - update all databases
* `--metadata` - update PyPI packages metadata from Google BigQuery
* `--downloads` - update PyPI packages downloads from Google BigQuery
