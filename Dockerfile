FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e .

ARG BUILD_GIT_SHA=unknown
ARG BUILD_GIT_BRANCH=unknown
ARG BUILD_TIME=unknown
ENV BUILD_GIT_SHA=${BUILD_GIT_SHA}
ENV BUILD_GIT_BRANCH=${BUILD_GIT_BRANCH}
ENV BUILD_TIME=${BUILD_TIME}

EXPOSE 8080
ENV SERVICE_CONFIG=/config/service.yaml
ENV SOURCES_CONFIG=/config/sources.yaml
CMD ["uvicorn", "openapi_merger.main:app", "--host", "0.0.0.0", "--port", "8080"]
