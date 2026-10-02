# Quickstart: Core Infrastructure

## Setup Instructions

1.  **Environment Setup**:
    ```bash
    uv sync
    ```

2.  **Infrastructure**:
    ```bash
    docker-compose up -d
    ```

3.  **Database Migrations**:
    ```bash
    # (After implementation)
    uv run alembic upgrade head
    ```

4.  **Running the API**:
    ```bash
    # Ensure X_ADMIN_KEY is set in .env
    uv run python -m api.main
    ```

5.  **Running the RAG CLI**:
    ```bash
    uv run python -m cli.rag_admin --help
    ```

## Verification
- Health check: `curl http://localhost:8000/health`
- Admin search (Auth required): `curl -H "X-Admin-Key: <your_key>" -X POST http://localhost:8000/admin/rag/search -d '{"query": "test"}'`
- Logs: `docker-compose logs -f app` (JSON format in stdout)
