# Data Model: Infrastructure Foundation

## Entities

### `Product`
- `id`: UUID (PK)
- `sku`: String (Unique, Index)
- `name`: String
- `description`: Text
- `price`: Decimal
- `metadata`: JSONB
- `created_at`: DateTime (UTC)
- `updated_at`: DateTime (UTC)

### `TextEmbedding`
- `id`: UUID (PK)
- `source_id`: UUID (FK to Product or other entities)
- `source_type`: String (e.g., 'product_description', 'query')
- `embedding`: Vector(1024)  -- *Dimension optimized for bge-m3*
- `model_name`: String
- `model_version`: String
- `created_at`: DateTime (UTC)

### `ConversationSession`
- `id`: UUID (PK)
- `external_id`: String (e.g., Telegram Chat ID, Unique)
- `metadata`: JSONB
- `created_at`: DateTime (UTC)

### `ConversationMessage`
- `id`: UUID (PK)
- `session_id`: UUID (FK to ConversationSession)
- `role`: Enum (user, assistant, system)
- `content`: Text
- `token_count`: Integer
- `model_name`: String
- `source_chunk_ids`: JSONB array of {product_id: string, chunk_id: string} (for RAG responses, Article IX)
- `metadata`: JSONB
- `created_at`: DateTime (UTC)

### `SemanticCache`
- `query_hash`: String (SHA256, PK)
- `query_text`: Text (Canonicalized)
- `response`: Text
- `embedding`: Vector(1024)
- `model_name`: String
- `similarity_score`: Float
- `created_at`: DateTime (UTC)

## Relationships
- `ConversationSession` 1:N `ConversationMessage`
- `Product` 1:N `TextEmbedding`

## Validation Rules
- `agent_v1` schema MUST be used for all tables.
- `sku` must be non-empty and formatted.
- `price` must be non-negative.
- `vector` dimensions must be consistent within the environment (default 1024).
