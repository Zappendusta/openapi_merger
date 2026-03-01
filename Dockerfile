FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e .
EXPOSE 8080
ENV SERVICE_CONFIG=/config/service.yaml
ENV SOURCES_CONFIG=/config/sources.yaml
CMD ["uvicorn", "openapi_merger.main:app", "--host", "0.0.0.0", "--port", "8080"]
