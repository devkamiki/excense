FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV EXCENSE_DATA_DIR=/data

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

VOLUME /data
EXPOSE 5232

COPY ./docker/entrypoint.sh /usr/local/bin/excense-entrypoint
RUN chmod +x /usr/local/bin/excense-entrypoint

ENTRYPOINT ["excense-entrypoint"]
CMD ["serve"]