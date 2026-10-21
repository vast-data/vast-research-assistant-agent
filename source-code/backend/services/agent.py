"""
Agent service
Handles agent creation and management with VastDB storage
"""

import os
import sys
import logging
import importlib.util
from typing import Optional, Dict, Any, List, Callable, Tuple
import httpx

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.duckduckgo import DuckDuckGoTools
from agno.tools.reasoning import ReasoningTools

from config import (
    AGENT_TEMPERATURE, AGENT_MARKDOWN,
    AGENT_NAME, AGENT_INSTRUCTIONS, RAG_BASE_URL,
    RAG_SSL_VERIFY, RAG_TIMEOUT,
    AGENT_OPENAI_API_KEY, AGENT_OPENAI_BASE_URL, AGENT_OPENAI_MODEL_ID,
    MODEL_PROVIDER, GOOGLE_API_KEY, GEMINI_MODEL_ID,
    VERTEX_PROJECT_ID, VERTEX_LOCATION, VERTEX_MODEL_ID,
    VASTDB_BUCKET, VASTDB_SCHEMA,
    VASTDB_COLLECTIONS_BUCKET, VASTDB_COLLECTIONS_SCHEMA,
    RETRIEVE_WITH_RERANK, ENABLE_REASONING_TOOLS,
    GSF_BASE_URL,
)

logger = logging.getLogger(__name__)

# Shared singletons: avoid recreating expensive objects on every request
_shared_storage: Optional[Any] = None
_shared_model: Optional[Any] = None
_shared_model_provider: Optional[str] = None


def _uses_max_completion_tokens(model_id: str) -> bool:
    """Foundry GPT-5 / o-series / MAI reject max_tokens — use max_completion_tokens."""
    mid = (model_id or "").lower()
    return mid.startswith(("gpt-5", "o1", "o3", "o4", "mai")) or "mai-thinking" in mid


def _create_openai_chat_model(model_id: str) -> Any:
    """Build an OpenAIChat for Foundry / NVIDIA-compatible endpoints."""
    model_id = model_id or AGENT_OPENAI_MODEL_ID or ""
    model_id_l = model_id.lower()
    uses_completion_tokens = _uses_max_completion_tokens(model_id)

    openai_model_kwargs = {
        "id": model_id,
        "api_key": AGENT_OPENAI_API_KEY,
        "temperature": AGENT_TEMPERATURE,
        "retries": 3,
        "delay_between_retries": 20,
        "exponential_backoff": True,
    }
    if uses_completion_tokens:
        openai_model_kwargs["max_completion_tokens"] = 8192
    else:
        openai_model_kwargs["max_tokens"] = 8192
    if AGENT_OPENAI_BASE_URL:
        openai_model_kwargs["base_url"] = AGENT_OPENAI_BASE_URL
    if "nemotron" in model_id_l:
        openai_model_kwargs["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": False}
        }
        logger.info(
            f"Nemotron model detected ({model_id}): "
            "setting chat_template_kwargs.enable_thinking=false"
        )
    try:
        model = OpenAIChat(**openai_model_kwargs)
    except TypeError:
        for optional_key in (
            "extra_body",
            "max_completion_tokens",
            "max_tokens",
            "retries",
            "delay_between_retries",
            "exponential_backoff",
        ):
            openai_model_kwargs.pop(optional_key, None)
        if uses_completion_tokens:
            openai_model_kwargs["max_completion_tokens"] = 8192
        model = OpenAIChat(**openai_model_kwargs)
        if "nemotron" in model_id_l:
            try:
                model.extra_body = {
                    "chat_template_kwargs": {"enable_thinking": False}
                }
            except Exception:
                logger.warning("Could not set OpenAIChat.extra_body for Nemotron thinking disable")
    model.default_role_map = {
        "system": "system",
        "user": "user",
        "assistant": "assistant",
        "tool": "tool",
        "model": "assistant",
    }
    logger.info(
        f"Created OpenAI model: {model_id} "
        f"(completion_tokens={uses_completion_tokens}, retries=3)"
    )
    return model


# Import VastDBStorage from local lib directory
VastDBStorage = None
try:
    # Try direct import first (simpler and more reliable)
    try:
        from lib.vastdb_storage import VastDBStorage as VastDBStorageDirect
        VastDBStorage = VastDBStorageDirect
        logger.info("Successfully imported VastDBStorage via direct import")
    except ImportError:
        # Fallback to file-based import
        vastdb_path = os.path.join(os.path.dirname(__file__), '..', 'lib', 'vastdb_storage.py')
        if os.path.exists(vastdb_path):
            spec = importlib.util.spec_from_file_location("vastdb_storage", vastdb_path)
            vastdb_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(vastdb_module)
            VastDBStorage = vastdb_module.VastDBStorage
            logger.info("Successfully imported VastDBStorage via file import")
        else:
            logger.warning(f"vastdb_storage.py not found in lib directory: {vastdb_path}")
except Exception as e:
    logger.error(f"Failed to import VastDBStorage: {e}", exc_info=True)



def _get_namespace_from_rag_url() -> str:
    """
    Extract Kubernetes namespace from RAG_BASE_URL hostname.

    Supports short and FQDN in-cluster forms:
    - http://backend-webserver.research-assistant:8080
      → research-assistant
    - http://collections-insight-engine.ie-collections.svc.cluster.local:8080
      → ie-collections

    Returns:
        namespace string (e.g., "research-assistant")
    """
    from urllib.parse import urlparse

    if not RAG_BASE_URL:
        return "research-assistant"

    hostname = urlparse(RAG_BASE_URL).hostname
    if not hostname:
        return "research-assistant"

    parts = hostname.split(".")

    # K8s DNS: <service>.<namespace>.svc[.cluster.local]
    if len(parts) >= 3 and parts[2] == "svc":
        return parts[1]

    # Short form: <service>.<namespace>
    if len(parts) == 2:
        return parts[1]

    return "research-assistant"


def _get_session_bucket_and_schema() -> Tuple[str, str]:
    """
    Resolve VastDB bucket and schema for session persistence.

    Priority:
    1. VASTDB_BUCKET / VASTDB_SCHEMA env overrides (when set)
    2. Derive from RAG_BASE_URL namespace: <ns>-bucket / <ns>-schema

    Returns:
        Tuple of (bucket_name, schema_name)
    """
    namespace = _get_namespace_from_rag_url()
    derived_bucket = f"{namespace}-bucket"
    derived_schema = f"{namespace}-schema"
    return VASTDB_BUCKET or derived_bucket, VASTDB_SCHEMA or derived_schema


def _get_collections_bucket_and_schema() -> Tuple[str, str]:
    """
    Resolve VastDB bucket and schema for IE chunk / collection metadata.

    Sessions often use VASTDB_SCHEMA=research-assistant-schema while IE chunks
    live in <namespace>-schema (e.g. insight-engine-schema). Do not reuse the
    session schema override unless VASTDB_COLLECTIONS_SCHEMA is set.

    Priority:
    1. VASTDB_COLLECTIONS_BUCKET / VASTDB_COLLECTIONS_SCHEMA
    2. VASTDB_BUCKET + schema derived from RAG_BASE_URL namespace
    """
    namespace = _get_namespace_from_rag_url()
    derived_bucket = f"{namespace}-bucket"
    derived_schema = f"{namespace}-schema"
    bucket = VASTDB_COLLECTIONS_BUCKET or VASTDB_BUCKET or derived_bucket
    schema = VASTDB_COLLECTIONS_SCHEMA or derived_schema
    return bucket, schema


def get_collection_metadata(collection_name: str) -> Dict[str, Any]:
    """
    Get collection metadata (num_chunks, num_unique_files, total_doc_size) from VastDB.
    
    Chunks are stored in a shared 'chunks' table with a 'collection_name' column.
    Files are tracked via the VastDB catalog.
    
    Args:
        collection_name: Name of the collection
    
    Returns:
        Dictionary with num_chunks, num_unique_files, total_doc_size_bytes, total_doc_size_mb
    """
    import os
    import vastdb
    from ibis import _
    
    try:
        endpoint = os.getenv("VASTDB_ENDPOINT")
        access_key = os.getenv("VASTDB_ACCESS_KEY")
        secret_key = os.getenv("VASTDB_SECRET_KEY")
        bucket_name, schema_name = _get_collections_bucket_and_schema()
        
        if not all([endpoint, access_key, secret_key, bucket_name]):
            logger.warning("VastDB environment variables not configured for collection metadata")
            return {"num_chunks": 0, "num_unique_files": 0, "total_doc_size_bytes": 0, "total_doc_size_mb": 0.0}
        
        session = vastdb.connect(
            endpoint=endpoint,
            access=access_key,
            secret=secret_key,
            ssl_verify=False
        )
        
        with session.transaction() as tx:
            bucket_obj = tx.bucket(bucket_name)
            is_syncengine = collection_name.startswith("syncengine.")
            
            num_chunks = 0
            try:
                chunks_table = bucket_obj.schema(schema_name).table("chunks")
                if chunks_table:
                    try:
                        reader = chunks_table.select(["collection_name"])
                        result = reader.read_all()
                        df = result.to_pandas()
                        num_chunks = len(df[df["collection_name"] == collection_name])
                        logger.info(f"Found {num_chunks} chunks for collection {collection_name} (from {len(df)} total)")
                    except Exception as select_err:
                        logger.warning(f"Could not select from chunks table: {select_err}")
                        stats = chunks_table.stats
                        if stats and stats.num_rows > 0:
                            num_chunks = stats.num_rows
                            logger.info(f"Using total chunks count as fallback: {num_chunks}")
            except Exception as e:
                logger.warning(f"Error querying chunks table for {collection_name}: {e}")
                try:
                    lookup_schema = schema_name
                    lookup_table_name = collection_name
                    if not is_syncengine and '__' in collection_name:
                        parts = collection_name.split('__', 1)
                        if len(parts) == 2:
                            lookup_schema = parts[0]
                            lookup_table_name = parts[1]
                    table = bucket_obj.schema(lookup_schema).table(lookup_table_name)
                    if table:
                        stats = table.stats
                        num_chunks = stats.num_rows if stats else 0
                except Exception:
                    pass
            
            num_unique_files = 0
            total_doc_size = 0
            
            try:
                cat = tx.catalog()
                
                if is_syncengine:
                    predicate = _.parent_path.startswith(f"/{collection_name}/") & (_.element_type == "FILE")
                    logger.info(f"Looking for syncengine files at catalog path: /{collection_name}/")
                else:
                    s3_bucket = os.getenv("VASTDB_S3_BUCKET") or bucket_name
                    predicate = _.parent_path.startswith(f"/{s3_bucket}/{collection_name}/") & (_.element_type == "FILE")
                
                reader = cat.select(["name", "size", "parent_path", "element_type"], predicate=predicate)
                result = reader.read_all()
                df = result.to_pandas()
                
                if len(df) > 0:
                    num_unique_files = len(df)
                    total_doc_size = int(df["size"].sum())
                    logger.info(f"Found {num_unique_files} files, {total_doc_size} bytes for collection {collection_name}")
                else:
                    logger.info(f"No files found in catalog for collection {collection_name}")
                    try:
                        all_reader = cat.select(["parent_path"], predicate=(_.element_type == "FILE"))
                        all_result = all_reader.read_all()
                        all_df = all_result.to_pandas()
                        search_term = collection_name.split(".")[-1] if "." in collection_name else collection_name
                        matching = all_df[all_df["parent_path"].str.contains(search_term, case=False, na=False)]
                        if len(matching) > 0:
                            sample_paths = matching["parent_path"].unique()[:5]
                            logger.info(f"Debug: Found {len(matching)} files with '{search_term}' in path. Sample paths: {list(sample_paths)}")
                    except Exception as debug_err:
                        logger.debug(f"Debug catalog scan failed: {debug_err}")
                    
            except Exception as e:
                logger.warning(f"Catalog query failed for {collection_name}: {e}")
            
            return {
                "num_chunks": num_chunks,
                "num_unique_files": num_unique_files,
                "total_doc_size_bytes": total_doc_size,
                "total_doc_size_mb": round(total_doc_size / (1024 * 1024), 2) if total_doc_size else 0.0
            }
    except Exception as e:
        logger.warning(f"Error getting collection metadata for {collection_name}: {e}")
        return {"num_chunks": 0, "num_unique_files": 0, "total_doc_size_bytes": 0, "total_doc_size_mb": 0.0}


def _normalize_metadata_filters(metadata_filters: Any) -> Optional[List[str]]:
    """Normalize model-provided metadata_filters to a list of 'key:op:value' strings."""
    if metadata_filters is None or metadata_filters == "" or metadata_filters == []:
        return None
    if isinstance(metadata_filters, str):
        parts = [p.strip() for p in metadata_filters.split(",") if p.strip()]
        return parts or None
    if isinstance(metadata_filters, (list, tuple)):
        out: List[str] = []
        for item in metadata_filters:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                # Allow {"key":"subject_id","op":"eq","value":"10009035"}
                key = item.get("key") or item.get("field")
                op = item.get("op") or item.get("operator") or "eq"
                value = item.get("value")
                if key is not None and value is not None:
                    out.append(f"{key}:{op}:{value}")
        return out or None
    return None


def _parse_eq_filters(filters: List[str]) -> Dict[str, str]:
    """Extract key->value for eq filters only (used for doc-name / tag matching)."""
    out: Dict[str, str] = {}
    for raw in filters:
        parts = raw.split(":", 2)
        if len(parts) == 3 and parts[1].lower() == "eq":
            out[parts[0]] = parts[2]
    return out


def _augment_prompt_with_eq_filters(prompt: str, filters: Optional[List[str]]) -> str:
    """
    Bias vector search with eq-filter values.

    IE metadata_filters only match chunk.extra_metadata. Ingest/tagging often
    leaves that empty while subject_id / note_type still appear in the note text
    and filename — putting them in the prompt is what makes retrieval find the
    right patient when the SQL filter returns 0 rows.
    """
    if not filters:
        return prompt
    eq = _parse_eq_filters(filters)
    # Prefer stable order: subject/patient first, then note type, then others
    preferred = ("subject_id", "patient_id", "note_type")
    parts: List[str] = []
    for key in preferred:
        val = (eq.get(key) or "").strip()
        if val and val not in parts:
            parts.append(val)
    for key, val in eq.items():
        val = (val or "").strip()
        if key in preferred or not val:
            continue
        if val not in parts:
            parts.append(val)
    if not parts:
        return prompt
    bias = " ".join(parts)
    base = (prompt or "").strip()
    if not base:
        return bias
    if bias.lower() in base.lower():
        return base
    return f"{bias} {base}"


def _chunk_doc_path(chunk: Dict[str, Any]) -> str:
    return str(chunk.get("doc_path") or chunk.get("source") or chunk.get("document_name") or "")


def _filter_chunks_by_document_names(
    chunks: List[Dict[str, Any]],
    document_names: List[str],
) -> List[Dict[str, Any]]:
    if not document_names:
        return chunks
    names = [n for n in document_names if n]
    if not names:
        return chunks
    filtered: List[Dict[str, Any]] = []
    for chunk in chunks:
        path = _chunk_doc_path(chunk)
        if any(name == path or path.endswith(f"/{name}") or name in path for name in names):
            filtered.append(chunk)
    return filtered


def _document_matches_eq_filters(document: Dict[str, Any], eq_filters: Dict[str, str]) -> bool:
    """Match a listed document against eq filters via tags and/or filename conventions."""
    if not eq_filters:
        return True
    name = str(document.get("document_name") or "")
    tags = document.get("tags") if isinstance(document.get("tags"), dict) else {}
    for key, value in eq_filters.items():
        tag_val = str(tags.get(key) or "")
        if tag_val == value:
            continue
        # Filename conventions: 10009035__Discharge_summary__....pdf
        if key in ("subject_id", "patient_id") and value and value in name:
            continue
        if key == "note_type" and value and (f"__{value}__" in name or value in name):
            continue
        return False
    return True


def _resolve_scoped_document_names(
    *,
    collection_names: List[str],
    token: str,
    base_url: str,
    filters: List[str],
) -> List[str]:
    """
    Resolve which document names a metadata_filters clause should keep.

    IE metadata_filters query chunk.extra_metadata, but ingest often leaves that
    empty while document tags / filenames still carry subject_id, note_type, etc.
    """
    from services.rag import list_documents

    eq_filters = _parse_eq_filters(filters)
    names: List[str] = []
    seen: set[str] = set()

    def _add(name: Optional[str]) -> None:
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    # 1) Prefer IE tag_filters (chunks.tags column) across all selected collections
    for collection_name in collection_names:
        try:
            listed = list_documents(
                collection_name=collection_name,
                token=token,
                base_url=base_url,
                tag_filters=filters,
            )
            for d in listed.get("documents") or []:
                if isinstance(d, dict):
                    _add(d.get("document_name"))
        except Exception as e:
            logger.warning(
                f"Tag-scoped document list failed for {collection_name}: {e}",
                exc_info=True,
            )

    if names:
        return names

    # 2) Fall back: list all docs per collection and match tags + filename
    for collection_name in collection_names:
        try:
            listed = list_documents(
                collection_name=collection_name,
                token=token,
                base_url=base_url,
            )
            for d in listed.get("documents") or []:
                if isinstance(d, dict) and _document_matches_eq_filters(d, eq_filters):
                    _add(d.get("document_name"))
        except Exception as e:
            logger.warning(
                f"Full document list for filter fallback failed for {collection_name}: {e}",
                exc_info=True,
            )

    if names:
        return names

    # 3) Last resort: synthesize filename needles from eq values (no list API)
    needles: List[str] = []
    for key, value in eq_filters.items():
        if key in ("subject_id", "patient_id", "note_type") and value:
            needles.append(value)
    return needles


def create_retrieve_chunks_tool(
    token: str,
    base_url: str,
    target_collections: Optional[List[str]] = None,
):
    """
    Create a retrieve_chunks tool that calls the /api/v1/retrieve endpoint.

    Args:
        token: JWT token for authentication
        base_url: Base URL of the RAG API
        target_collections: One or more collections to search (IE collection_names)

    Returns:
        Function that can be used as an agent tool
    """
    configured = [c for c in (target_collections or []) if c]

    def retrieve_chunks(
        prompt: str,
        collection_name: str = "",
        with_rerank: bool = False,
        number_of_docs: int = 10,
        top_k_from_vectorstore: int = 30,
        metadata_filters: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Retrieve relevant document chunks from the knowledge base using similarity search.

        Args:
            prompt: The search query/prompt to find relevant documents (REQUIRED)
            collection_name: Ignored — uses pre-configured collection(s) from the UI
            with_rerank: Ignored; rerank is controlled by RETRIEVE_WITH_RERANK (default False)
            number_of_docs: Number of documents to return (default: 10)
            top_k_from_vectorstore: Number of documents to retrieve from vector store (default: 30)
            metadata_filters: Optional list of 'key:op:value' filters, e.g.
                ["subject_id:eq:10009035", "note_type:eq:Discharge_summary"].
                Supported operators: eq, ne, gt, gte, lt, lte, contains, startswith.
                Use this to scope a multi-patient collection to one patient / note type.

        Returns:
            Dictionary containing retrieved chunks
        """
        base_url_clean = base_url.rstrip('/')
        client = httpx.Client(
            base_url=base_url_clean,
            verify=RAG_SSL_VERIFY,
            timeout=RAG_TIMEOUT
        )

        try:
            collections = list(configured)
            if not collections:
                return {"error": "No collection selected. Please select a collection before sending prompts."}

            effective_rerank = RETRIEVE_WITH_RERANK
            filters = _normalize_metadata_filters(metadata_filters)
            search_prompt = _augment_prompt_with_eq_filters(prompt, filters)
            logger.info(
                f"Calling retrieve_chunks with collection_names={collections} "
                f"with_rerank={effective_rerank} (RETRIEVE_WITH_RERANK) "
                f"metadata_filters={filters} search_prompt={search_prompt[:160]!r}"
            )

            auth_headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "accept": "application/json",
            }

            def _do_retrieve(
                *,
                query: str,
                use_filters: Optional[List[str]],
                n_docs: int,
                top_k: int,
            ) -> List[Dict[str, Any]]:
                # IE: collection_names searches/merges across multiple collections.
                # collection_name remains as a single-collection alias when len==1.
                payload: Dict[str, Any] = {
                    "prompt": query,
                    "with_rerank": effective_rerank,
                    "number_of_docs": n_docs,
                    "top_k_from_vectorstore": top_k,
                    "extra_metadata_fields": [
                        "source-url",
                        "source-title",
                        "subject_id",
                        "note_type",
                        "patient_id",
                    ],
                }
                if len(collections) == 1:
                    payload["collection_name"] = collections[0]
                else:
                    payload["collection_names"] = collections
                if use_filters:
                    payload["metadata_filters"] = use_filters
                response = client.post("/api/v1/retrieve", json=payload, headers=auth_headers)
                response.raise_for_status()
                body = response.json()
                if isinstance(body, list):
                    return [c for c in body if isinstance(c, dict)]
                if isinstance(body, dict):
                    raw = body.get("chunks") or body.get("documents") or []
                    return [c for c in raw if isinstance(c, dict)]
                return []

            chunks = _do_retrieve(
                query=search_prompt,
                use_filters=filters,
                n_docs=number_of_docs,
                top_k=top_k_from_vectorstore,
            )

            scoped_docs: List[str] = []
            if filters:
                scoped_docs = _resolve_scoped_document_names(
                    collection_names=collections,
                    token=token,
                    base_url=base_url_clean,
                    filters=filters,
                )
                logger.info(
                    f"metadata_filters bridge: ie_chunks={len(chunks)} "
                    f"scoped_docs={scoped_docs}"
                )

                filtered = (
                    _filter_chunks_by_document_names(chunks, scoped_docs)
                    if scoped_docs
                    else chunks
                )
                if not filtered:
                    # IE filter on empty extra_metadata returns 0; retry without SQL
                    # filters using the subject-biased prompt and escalating top_k.
                    top_k_ladder = [
                        max(top_k_from_vectorstore * 3, 90),
                        250,
                        500,
                        1000,
                    ]
                    for wider_top_k in top_k_ladder:
                        wider_n = max(number_of_docs * 3, min(wider_top_k, 80))
                        logger.info(
                            "IE metadata_filters returned no usable chunks; "
                            f"retrying without IE filters "
                            f"(top_k={wider_top_k}, prompt_biased=True) then scoping"
                        )
                        wider = _do_retrieve(
                            query=search_prompt,
                            use_filters=None,
                            n_docs=wider_n,
                            top_k=wider_top_k,
                        )
                        filtered = (
                            _filter_chunks_by_document_names(wider, scoped_docs)
                            if scoped_docs
                            else wider
                        )
                        if filtered:
                            break

                if not filtered and scoped_docs:
                    # Last resort: one retrieve per scoped document name
                    merged: List[Dict[str, Any]] = []
                    seen_ids: set = set()
                    for doc_name in scoped_docs[:8]:
                        doc_query = f"{search_prompt} {doc_name}".strip()
                        wider = _do_retrieve(
                            query=doc_query,
                            use_filters=None,
                            n_docs=max(number_of_docs * 2, 20),
                            top_k=200,
                        )
                        for chunk in _filter_chunks_by_document_names(wider, [doc_name]):
                            cid = chunk.get("id") or chunk.get("pk") or id(chunk)
                            if cid in seen_ids:
                                continue
                            seen_ids.add(cid)
                            merged.append(chunk)
                    filtered = merged
                    logger.info(
                        f"Per-document retrieve fallback produced {len(filtered)} chunks "
                        f"for {len(scoped_docs)} scoped docs"
                    )

                chunks = filtered[:number_of_docs]

            result: Dict[str, Any] = {
                "chunks": chunks,
                "collection_names": collections,
            }
            if filters:
                result["metadata_filters"] = filters
            if scoped_docs:
                result["tag_scoped_documents"] = scoped_docs

            try:
                # Prefer metadata for the first collection (GUI stats); multi is merged at retrieve
                metadata = get_collection_metadata(collections[0])
                logger.info(f"Got collection metadata: {metadata}")
                result["collection_metadata"] = metadata
            except Exception as e:
                logger.warning(f"Failed to get collection metadata: {e}", exc_info=True)

            return result
        except httpx.HTTPStatusError as e:
            logger.error(f"Retrieve chunks failed: {e.response.status_code} - {e.response.text}")
            return {
                "error": f"Failed to retrieve chunks: {e.response.status_code}",
                "detail": e.response.text
            }
        except Exception as e:
            logger.error(f"Error retrieving chunks: {e}", exc_info=True)
            return {
                "error": f"Error retrieving chunks: {str(e)}"
            }
        finally:
            client.close()

    return retrieve_chunks


def create_gsf_query_tool(on_step: Optional[Callable[[Dict[str, Any]], None]] = None):
    """
    Create a query_database tool that calls the GSF API for structured data queries.

    Args:
        on_step: Optional callback invoked with each GSF pipeline step as it streams in,
            used to push live progress to the client.

    Returns:
        Function that can be used as an agent tool
    """
    from services.gsf import query_gsf

    def query_database(
        question: str,
    ) -> Dict[str, Any]:
        """
        Query the structured database via natural-language SQL. Returns rows and columns only.

        Ask one focused question per call. Prefer real table/column names when known.
        Do not call this tool many times in parallel for the same analysis — run
        sequential questions instead.

        Args:
            question: Natural language question answerable from tabular data

        Returns:
            Dictionary with sql_code, columns, results (rows), and optional error
        """
        # Never forward an LLM-supplied conversation_id: GSF requires a valid UUID from
        # its own session, and a random/RA session id yields 422 or 401.
        result = query_gsf(
            question=question,
            conversation_id=None,
            on_step=on_step,
        )
        return result

    return query_database


def get_available_tools() -> List[Dict[str, Any]]:
    """
    Get list of all available tools with their names and descriptions.
    
    Returns:
        List of tool dictionaries with 'name' and 'description' fields
    """
    tools = []
    
    # retrieve_chunks tool
    tools.append({
        "name": "retrieve_chunks",
        "description": "Search internal knowledge base (RAG) for documents. Optional metadata_filters (e.g. subject_id:eq:10009035) scope a multi-patient collection.",
        "category": "rag"
    })
    
    # DuckDuckGo tools (check what methods it provides)
    try:
        duckduckgo_tools = DuckDuckGoTools()
        # DuckDuckGoTools typically provides 'search' method
        tools.append({
            "name": "duckduckgo_search",
            "description": "Search the internet for current/recent information, news, or information not in knowledge base",
            "category": "internet"
        })
    except Exception:
        pass
    
    # Reasoning )
    try:
        reasoning_tools = ReasoningTools()
        # ReasoningTools typically provides 'think' and 'analyze' methods
        tools.append({
            "name": "think",
            "description": "Use reasoning to break down complex problems step-by-step",
            "category": "reasoning"
        })
        tools.append({
            "name": "analyze",
            "description": "Analyze and evaluate information systematically",
            "category": "reasoning"
        })
    except Exception:
        pass

    # GSF tool (listed when configured; registration still gated by gsf_enabled at agent create)
    if GSF_BASE_URL:
        tools.append({
            "name": "query_database",
            "description": "Query structured databases using natural language (translates to SQL). Use for patient data, lab results, diagnoses, admissions.",
            "category": "database"
        })

    return tools


def filter_tools(tools: List[Any], allowed_tool_names: Optional[List[str]] = None) -> List[Any]:
    """
    Filter tools list based on allowed tool names.
    
    Args:
        tools: List of tool objects/functions
        allowed_tool_names: Optional list of tool names to allow. If None, all tools are returned.
    
    Returns:
        Filtered list of tools
    """
    if not allowed_tool_names:
        return tools
    
    # Map tool names to actual tools
    # retrieve_chunks is a function
    # DuckDuckGoTools and ReasoningTools are objects with methods
    filtered = []
    
    for tool in tools:
        # Check if it's the retrieve_chunks function
        if callable(tool) and tool.__name__ == "retrieve_chunks":
            if "retrieve_chunks" in allowed_tool_names:
                filtered.append(tool)
        # Check if it's DuckDuckGoTools
        elif isinstance(tool, type(DuckDuckGoTools())) if DuckDuckGoTools else False:
            if "duckduckgo_search" in allowed_tool_names:
                filtered.append(tool)
        # Check if it's ReasoningTools
        elif isinstance(tool, type(ReasoningTools())) if ReasoningTools else False:
            if "think" in allowed_tool_names or "analyze" in allowed_tool_names:
                filtered.append(tool)
    
    return filtered


def get_or_create_agent(user_id: str, token: str, session_id: Optional[str] = None, tool_hooks: Optional[List[Callable]] = None, allowed_tools: Optional[List[str]] = None, collection_name: Optional[str] = None, collection_names: Optional[List[str]] = None, internet_search: Optional[bool] = None, custom_instructions: Optional[str] = None, gsf_step_callback: Optional[Callable[[Dict[str, Any]], None]] = None, gsf_enabled: Optional[bool] = None) -> Optional[Agent]:
    """
    Get or create an Agent instance for the user with VastDB storage.
    
    Uses NVIDIA API (OpenAI-compatible) for model inference.
    
    Args:
        user_id: User ID from JWT token (will be converted to string)
        token: JWT token for RAG backend authentication
        session_id: Optional session_id - if provided, creates agent with this session_id in constructor
        tool_hooks: Optional list of tool hooks for monitoring tool execution
        allowed_tools: Optional list of tool names to use (e.g., ["retrieve_chunks", "duckduckgo_search"])
        collection_name: Optional single collection (legacy alias for collection_names)
        collection_names: Optional list of collections for multi-collection IE retrieve
        internet_search: If False, skip DuckDuckGo tools. If None or True, include them.
        custom_instructions: Optional custom system prompt
        gsf_step_callback: Optional callback for live GSF pipeline steps
        gsf_enabled: If True and GSF_BASE_URL is set, register the query_database tool
    
    Returns:
        Agent instance or None if creation fails
    """
    # Ensure user_id is a string for caching and database queries
    user_id_str = str(user_id)
    
    if session_id:
        logger.debug(f"Creating agent with session_id={session_id}")
    else:
        logger.debug(f"Creating agent for new session")
    
    if not Agent or not VastDBStorage:
        logger.error("Agno modules not available")
        return None
    
    try:
        global _shared_storage, _shared_model, _shared_model_provider

        # Reuse VastDBStorage singleton
        if _shared_storage is None:
            _shared_storage = VastDBStorage()
            logger.info("Created shared VastDBStorage instance")
        storage = _shared_storage

        # Validate API key / credentials
        if MODEL_PROVIDER == "gemini":
            if not GOOGLE_API_KEY:
                logger.error("GOOGLE_API_KEY not configured for Gemini provider")
                return None
        elif MODEL_PROVIDER == "vertex":
            if not VERTEX_PROJECT_ID:
                logger.error("GOOGLE_CLOUD_PROJECT not configured for Vertex AI provider")
                return None
        elif not AGENT_OPENAI_API_KEY:
            logger.error("AGENT_OPENAI_API_KEY not configured")
            return None
        
        # Create tools list — order matters for the model; put the active primary tool first
        tools = []
        gsf_tool = None
        retrieve_tool = None
        duckduckgo_tools = None

        # GSF mode: register query_database first so structured EHR questions prefer it
        if gsf_enabled and GSF_BASE_URL:
            try:
                gsf_tool = create_gsf_query_tool(on_step=gsf_step_callback)
                tools.append(gsf_tool)
                logger.info("Added GSF query_database tool for structured data queries (primary)")
            except Exception as e:
                logger.warning(f"Failed to add GSF tool: {e}")
        elif gsf_enabled and not GSF_BASE_URL:
            logger.error(
                "GSF toggle is ON but GSF_BASE_URL is not set — query_database will NOT be available. "
                "Set GSF_BASE_URL in the ConfigMap/env."
            )

        # RAG retrieve_chunks — always available when a collection is selected (GSF hybrid + docs)
        resolved_collections: List[str] = []
        if collection_names:
            resolved_collections = [c for c in collection_names if c]
        elif collection_name:
            resolved_collections = [collection_name]

        logger.info(f"Creating retrieve_chunks tool with collection_names={resolved_collections}")
        retrieve_tool = create_retrieve_chunks_tool(
            token=token,
            base_url=RAG_BASE_URL,
            target_collections=resolved_collections or None,
        )
        tools.append(retrieve_tool)
        
        # Internet search — only when internet_search is explicitly True (exclusive mode)
        if internet_search is True:
            backends_to_try = ['brave', 'duckduckgo', 'auto', None]
            for backend in backends_to_try:
                try:
                    if backend:
                        duckduckgo_tools = DuckDuckGoTools(backend=backend)
                        logger.info(f"Added DuckDuckGoTools for internet search (using '{backend}' backend)")
                    else:
                        duckduckgo_tools = DuckDuckGoTools()
                        logger.info("Added DuckDuckGoTools for internet search (using default backend)")
                    tools.append(duckduckgo_tools)
                    break
                except Exception as e:
                    logger.warning(f"Failed to add DuckDuckGoTools with backend '{backend}': {e}")
                    continue
            if not duckduckgo_tools:
                logger.warning("Could not initialize DuckDuckGoTools with any backend")
        else:
            logger.info("Skipping DuckDuckGoTools (internet_search not enabled)")
        
        # ReasoningTools off by default (ENABLE_REASONING_TOOLS). Weak OpenAI-compatible
        # models often emit tool-call JSON as the final answer when these are enabled.
        # Also skip for custom/deep-research prompts (Gemini text→tool pattern).
        if ENABLE_REASONING_TOOLS and not custom_instructions:
            try:
                reasoning_tools = ReasoningTools()
                tools.append(reasoning_tools)
                logger.info("Added ReasoningTools for enhanced thinking capabilities")
            except Exception as e:
                logger.warning(f"Failed to add ReasoningTools: {e}")
        elif custom_instructions:
            logger.info("Skipping ReasoningTools (custom/deep-research prompt uses visible reasoning)")
        else:
            logger.info("Skipping ReasoningTools (ENABLE_REASONING_TOOLS=false)")
        
        # Filter tools if allowed_tools is specified
        if allowed_tools:
            filtered_tools = []
            needs_retrieve = False
            needs_duckduckgo = False
            needs_reasoning = False
            needs_gsf = False
            
            for tool_name in allowed_tools:
                if tool_name == "retrieve_chunks":
                    needs_retrieve = True
                elif tool_name == "duckduckgo_search":
                    needs_duckduckgo = True
                elif tool_name in ["think", "analyze"]:
                    needs_reasoning = True
                elif tool_name == "query_database":
                    needs_gsf = True
            
            # Add tools based on what's needed
            if needs_retrieve:
                filtered_tools.append(retrieve_tool)
                logger.info("Added tool: retrieve_chunks")
            
            if needs_duckduckgo and duckduckgo_tools:
                filtered_tools.append(duckduckgo_tools)
                logger.info("Added tool: duckduckgo_search")
            
            if needs_reasoning and 'reasoning_tools' in locals():
                filtered_tools.append(reasoning_tools)
                logger.info("Added tool: reasoning (think/analyze)")

            if needs_gsf and gsf_tool:
                filtered_tools.append(gsf_tool)
                logger.info("Added tool: query_database (GSF)")
            
            if filtered_tools:
                tools = filtered_tools
                logger.info(f"Filtered tools to: {[t.__name__ if callable(t) else type(t).__name__ for t in tools]}")
            else:
                logger.warning(f"No valid tools found in allowed_tools={allowed_tools}, using all tools")
        
        # Reuse model singleton (recreate only if provider changed)
        if _shared_model is None or _shared_model_provider != MODEL_PROVIDER:
            if MODEL_PROVIDER == "gemini":
                from agno.models.google import Gemini
                _shared_model = Gemini(
                    id=GEMINI_MODEL_ID,
                    api_key=GOOGLE_API_KEY,
                    temperature=AGENT_TEMPERATURE,
                    generation_config={"max_output_tokens": 65536},
                )
                logger.info(f"Created Gemini model: {GEMINI_MODEL_ID} (max_output_tokens=65536)")
            elif MODEL_PROVIDER == "vertex":
                from agno.models.google import Gemini
                _shared_model = Gemini(
                    id=VERTEX_MODEL_ID,
                    vertexai=True,
                    project_id=VERTEX_PROJECT_ID,
                    location=VERTEX_LOCATION,
                    temperature=AGENT_TEMPERATURE,
                    generation_config={"max_output_tokens": 65536},
                )
                logger.info(f"Created Vertex AI model: {VERTEX_MODEL_ID} (project={VERTEX_PROJECT_ID}, location={VERTEX_LOCATION})")
            else:
                _shared_model = _create_openai_chat_model(AGENT_OPENAI_MODEL_ID or "")
            _shared_model_provider = MODEL_PROVIDER
        model = _shared_model
        
        # Use custom instructions if provided, otherwise use default from config
        instructions = custom_instructions if custom_instructions else AGENT_INSTRUCTIONS
        if custom_instructions:
            logger.info(f"Using custom system prompt ({len(custom_instructions)} chars)")
        
        agent_kwargs = {
            'name': AGENT_NAME,
            'model': model,
            'instructions': instructions,
            'db': storage,
            'tools': tools,
            'markdown': AGENT_MARKDOWN,
            'user_id': user_id_str,
            'add_history_to_context': True,
        }
        
        # If session_id is provided, add it to constructor (required for history to work)
        if session_id:
            agent_kwargs['session_id'] = session_id
            logger.info(f"Creating agent with session_id={session_id} in constructor")
        
        # Add tool hooks if provided (for streaming tool usage updates)
        if tool_hooks:
            agent_kwargs['tool_hooks'] = tool_hooks
            logger.info(f"Adding {len(tool_hooks)} tool hook(s) to agent")
        
        agent = Agent(**agent_kwargs)
        logger.info(f"Created agent for user_id={user_id_str} session_id={session_id}")
        return agent
    except Exception as e:
        logger.error(f"Error creating agent: {e}", exc_info=True)
        return None
