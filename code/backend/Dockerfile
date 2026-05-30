# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:3.12-slim
ARG PIP_INDEX_URL=http://mirrors.cloud.aliyuncs.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.cloud.aliyuncs.com

FROM ${PYTHON_IMAGE} AS runtime
ARG PIP_INDEX_URL=http://mirrors.cloud.aliyuncs.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.cloud.aliyuncs.com

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    PORT=8000

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
