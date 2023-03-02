location / {
    proxy_pass http://pypi_web:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_connect_timeout 120s;
    proxy_read_timeout 120s;
}
