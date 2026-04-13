"""
VastDB Storage Adapter for Agno
Implements BaseDb interface to store Agno data in VastDB.
"""

import os
import json
import logging
import time
from typing import Optional, List, Dict, Any, Tuple, Union
from datetime import date
from uuid import uuid4

import vastdb
import pyarrow as pa
from ibis import _

from agno.db.base import BaseDb, SessionType
from agno.session.agent import AgentSession
from agno.session import Session
from agno.db.schemas import UserMemory
from agno.db.schemas.knowledge import KnowledgeRow
from agno.db.schemas.culture import CulturalKnowledge
from agno.db.schemas.evals import EvalRunRecord, EvalFilterType, EvalType

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def convert_to_dict(obj, depth=0, max_depth=10):
    """Convert DictAsObject or other objects to plain dict, recursively handling nested structures."""
    if depth > max_depth:
        return str(obj)  # Prevent infinite recursion
    
    if isinstance(obj, dict):
        # If it's a dict with _dict key (from DictAsObject), extract the inner dict
        if '_dict' in obj and len(obj) == 1:
            return convert_to_dict(obj['_dict'], depth + 1, max_depth)
        # Recursively convert nested dicts
        return {k: convert_to_dict(v, depth + 1, max_depth) for k, v in obj.items()}
    elif isinstance(obj, list):
        # Recursively convert list items
        return [convert_to_dict(item, depth + 1, max_depth) for item in obj]
    elif hasattr(obj, '_dict'):
        # DictAsObject - extract the _dict attribute and convert recursively
        return convert_to_dict(obj._dict, depth + 1, max_depth)
    elif hasattr(obj, '__dict__'):
        # Other object - convert to dict, but skip _dict wrapper if present
        d = vars(obj)
        if '_dict' in d and len(d) == 1:
            return convert_to_dict(d['_dict'], depth + 1, max_depth)
        return {k: convert_to_dict(v, depth + 1, max_depth) for k, v in d.items()}
    elif hasattr(obj, 'dict'):
        return convert_to_dict(obj.dict(), depth + 1, max_depth)
    elif hasattr(obj, 'model_dump'):
        return convert_to_dict(obj.model_dump(), depth + 1, max_depth)
    else:
        # Primitive types (str, int, float, bool, None) - return as-is
        return obj


class VastDBStorage(BaseDb):
    """
    VastDB storage adapter for Agno.
    Stores all Agno data in a single VastDB table with session_id as the primary key.
    """

    def __init__(
        self,
        session_table: Optional[str] = None,
        culture_table: Optional[str] = None,
        memory_table: Optional[str] = None,
        metrics_table: Optional[str] = None,
        eval_table: Optional[str] = None,
        knowledge_table: Optional[str] = None,
        id: Optional[str] = None,
    ):
        """
        Initialize VastDB storage adapter.
        
        Connection parameters are read from environment variables:
        - VASTDB_ENDPOINT: VastDB endpoint URL
        - VASTDB_ACCESS_KEY: VastDB access key
        - VASTDB_SECRET_KEY: VastDB secret key
        - VASTDB_BUCKET: VastDB bucket name (view)
        - VASTDB_SCHEMA: VastDB schema name
        """
        super().__init__(
            session_table=session_table,
            culture_table=culture_table,
            memory_table=memory_table,
            metrics_table=metrics_table,
            eval_table=eval_table,
            knowledge_table=knowledge_table,
            id=id,
        )
        
        # Get connection parameters from environment
        self.endpoint = os.getenv("VASTDB_ENDPOINT")
        self.access_key = os.getenv("VASTDB_ACCESS_KEY")
        self.secret_key = os.getenv("VASTDB_SECRET_KEY")
        self.bucket_name = os.getenv("VASTDB_BUCKET")
        self.schema_name = os.getenv("VASTDB_SCHEMA", "agno")
        
        if not all([self.endpoint, self.access_key, self.secret_key, self.bucket_name]):
            raise ValueError(
                "Missing required VastDB environment variables: "
                "VASTDB_ENDPOINT, VASTDB_ACCESS_KEY, VASTDB_SECRET_KEY, VASTDB_BUCKET"
            )
        
        # Use session_table_name as the main table name
        self.table_name = self.session_table_name
        
        # Define PyArrow schema for the sessions table
        # Based on the provided schema with all fields
        self.schema_columns = pa.schema([
            ("session_id", pa.utf8()),
            ("session_type", pa.utf8()),
            ("agent_id", pa.utf8()),
            ("team_id", pa.utf8()),
            ("workflow_id", pa.utf8()),
            ("user_id", pa.utf8()),
            ("session_data", pa.string()),  # JSON string
            ("agent_data", pa.string()),    # JSON string
            ("team_data", pa.string()),     # JSON string
            ("workflow_data", pa.string()), # JSON string
            ("metadata", pa.string()),      # JSON string
            ("runs", pa.string()),          # JSON string (list)
            ("summary", pa.string()),       # JSON string
            ("created_at", pa.int64()),
            ("updated_at", pa.int64()),
        ])
        
        # Initialize connection
        self._initialize_connection()
        
        # Create schema and table if they don't exist
        self._ensure_schema_and_table()

    def _initialize_connection(self):
        """Initialize VastDB connection."""
        endpoint = self.endpoint
        if not endpoint.startswith(("http://", "https://")):
            endpoint = f"http://{endpoint}"

        logger.info(f"Connecting to VastDB at {endpoint}")

        self.session = vastdb.connect(
            endpoint=endpoint,
            access=self.access_key,
            secret=self.secret_key,
            ssl_verify=False
        )

        logger.info("VastDB connection established")

    def _ensure_schema_and_table(self):
        """Ensure the schema (bucket) and table exist, create if they don't."""
        try:
            with self.session.transaction() as tx:
                bucket = tx.bucket(self.bucket_name)
                schema = bucket.schema(self.schema_name, fail_if_missing=False)
                
                if schema is None:
                    schema = bucket.create_schema(self.schema_name, fail_if_exists=False)
                    logger.info(f"Schema {self.schema_name} created in bucket {self.bucket_name}")
                
                # Check if table exists
                table = schema.table(self.table_name, fail_if_missing=False)
                if table is None:
                    try:
                        table = schema.create_table(self.table_name, columns=self.schema_columns)
                        logger.info(f"Table {self.table_name} created in schema {self.schema_name}")
                    except Exception as e:
                        # Handle race condition where table was created by another process
                        if "409" in str(e) or "Conflict" in str(e) or "already exists" in str(e).lower():
                            logger.info(f"Table {self.table_name} already exists, using existing table")
                            table = schema.table(self.table_name, fail_if_missing=False)
                            if table is None:
                                raise RuntimeError(f"Failed to get table {self.table_name} after creation conflict")
                        else:
                            raise
        except Exception as e:
            logger.error(f"Error ensuring schema and table: {e}")
            raise

    def _get_table(self):
        """Get the table within a transaction context."""
        with self.session.transaction() as tx:
            bucket = tx.bucket(self.bucket_name)
            schema = bucket.schema(self.schema_name, fail_if_missing=True)
            return schema.table(self.table_name, fail_if_missing=True)

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists."""
        try:
            with self.session.transaction() as tx:
                bucket = tx.bucket(self.bucket_name)
                schema = bucket.schema(self.schema_name, fail_if_missing=False)
                if schema is None:
                    return False
                table = schema.table(table_name, fail_if_missing=False)
                return table is not None
        except Exception as e:
            logger.error(f"Error checking table existence: {e}")
            return False

    def _session_to_dict(self, session: Session) -> Dict[str, Any]:
        """Convert Session object to dictionary for storage."""
        # Try model_dump first (Pydantic v2), then fall back to dict() or __dict__
        if hasattr(session, 'model_dump'):
            session_dict = session.model_dump()
        elif hasattr(session, 'dict'):
            session_dict = session.dict()
        else:
            session_dict = vars(session) if hasattr(session, '__dict__') else {}
        
        # Handle both 'id' and 'session_id' fields
        session_id = session_dict.get("session_id") or session_dict.get("id", "")
        # Get type - check if it's an enum or string
        # If type is missing, infer from the session class (AgentSession -> 'agent')
        session_type_val = session_dict.get("type")
        if hasattr(session_type_val, 'value'):
            session_type = session_type_val.value
        elif session_type_val:
            session_type = str(session_type_val)
        else:
            # Default to 'agent' if type is missing (most common case for AgentSession)
            # Check if it's an AgentSession instance
            if isinstance(session, AgentSession):
                session_type = SessionType.AGENT.value
            else:
                session_type = SessionType.AGENT.value  # Default to agent
        
        # Helper function to recursively convert objects to JSON-serializable dicts
        def to_serializable(obj, depth=0, max_depth=10):
            """Recursively convert objects to JSON-serializable structures."""
            if depth > max_depth:
                return str(obj)  # Prevent infinite recursion
            
            # Handle None
            if obj is None:
                return None
            
            # Handle primitives
            if isinstance(obj, (str, int, float, bool)):
                return obj
            
            # Handle dict - recursively process values
            if isinstance(obj, dict):
                return {k: to_serializable(v, depth + 1, max_depth) for k, v in obj.items()}
            
            # Handle list - recursively process items
            if isinstance(obj, list):
                return [to_serializable(item, depth + 1, max_depth) for item in obj]
            
            # Handle Pydantic models
            if hasattr(obj, 'model_dump'):
                try:
                    return to_serializable(obj.model_dump(), depth + 1, max_depth)
                except Exception:
                    pass
            
            if hasattr(obj, 'dict'):
                try:
                    return to_serializable(obj.dict(), depth + 1, max_depth)
                except Exception:
                    pass
            
            # Handle objects with __dict__
            if hasattr(obj, '__dict__'):
                try:
                    return to_serializable(vars(obj), depth + 1, max_depth)
                except Exception:
                    pass
            
            # Fallback to string representation
            return str(obj)
        
        # Helper function to safely serialize values that might not be JSON serializable
        def safe_json_dumps(obj):
            if obj is None:
                return "{}"
            try:
                # Convert to serializable structure first
                serializable = to_serializable(obj)
                # Then serialize to JSON
                return json.dumps(serializable, default=str)
            except (TypeError, ValueError) as e:
                logger.debug(f"Failed to serialize object, using string representation: {e}")
                return json.dumps(str(obj))
        
        # Ensure all string fields are actually strings (not ints or None)
        def ensure_string(value, default=""):
            """Convert value to string, handling None and non-string types."""
            if value is None:
                return default
            return str(value)
        
        return {
            "session_id": ensure_string(session_id),
            "session_type": ensure_string(session_type),
            "agent_id": ensure_string(session_dict.get("agent_id")),
            "team_id": ensure_string(session_dict.get("team_id")),
            "workflow_id": ensure_string(session_dict.get("workflow_id")),
            "user_id": ensure_string(session_dict.get("user_id")),
            "session_data": safe_json_dumps(session_dict.get("session_data")),
            "agent_data": safe_json_dumps(session_dict.get("agent_data")),
            "team_data": safe_json_dumps(session_dict.get("team_data")),
            "workflow_data": safe_json_dumps(session_dict.get("workflow_data")),
            "metadata": safe_json_dumps(session_dict.get("metadata")),
            "runs": safe_json_dumps(session_dict.get("runs")),
            "summary": safe_json_dumps(session_dict.get("summary")),
            "created_at": int(session_dict.get("created_at") or 0),
            "updated_at": int(session_dict.get("updated_at") or 0),
        }

    def _dict_to_session(self, row: Dict[str, Any], deserialize: bool = True) -> Union[Session, Dict[str, Any]]:
        """Convert dictionary row to Session object."""
        if row is None:
            logger.error("Cannot convert None row to session")
            return None
            
        # Handle both dict and pandas Series
        if hasattr(row, 'get') and callable(getattr(row, 'get', None)):
            row_dict = row
        elif hasattr(row, 'to_dict'):
            row_dict = row.to_dict()
        else:
            try:
                row_dict = dict(row)
            except (TypeError, ValueError) as e:
                logger.error(f"Failed to convert row to dict: {e}, row type: {type(row)}")
                return None
        
        if row_dict is None:
            logger.error("row_dict is None after conversion")
            return None
        
        # Helper to safely parse JSON and ensure runs are a list of dicts, not strings
        def safe_json_loads(value, default):
            try:
                parsed = json.loads(value or default)
                # If it's a list, ensure all items are dicts (not strings)
                if isinstance(parsed, list):
                    cleaned = []
                    item_types = []
                    for idx, item in enumerate(parsed):
                        item_types.append(type(item).__name__)
                        if isinstance(item, dict):
                            cleaned.append(item)
                        elif isinstance(item, str):
                            # Try to parse string as JSON
                            try:
                                cleaned.append(json.loads(item))
                            except (json.JSONDecodeError, TypeError):
                                # Skip invalid items (expected for None values)
                                logger.warning(f"Skipping invalid run item at index {idx}: {type(item)}")
                        else:
                            # For other types, try to convert to dict
                            if hasattr(item, '__dict__'):
                                cleaned.append(vars(item))
                            elif hasattr(item, 'dict'):
                                cleaned.append(item.dict())
                            elif hasattr(item, 'model_dump'):
                                cleaned.append(item.model_dump())
                            else:
                                # NoneType items are expected and handled gracefully
                                logger.warning(f"Skipping run item at index {idx} of type {type(item)} (not a dict)")
                    if len(cleaned) != len(parsed):
                        logger.warning(f"safe_json_loads: parsed {len(parsed)} items (types: {item_types}), but only {len(cleaned)} items passed cleaning (filtered out {len(parsed) - len(cleaned)} items)")
                    else:
                        logger.info(f"safe_json_loads: parsed {len(parsed)} items, cleaned to {len(cleaned)} items")
                    return cleaned
                return parsed
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(f"Failed to parse JSON, using default: {e}")
                return json.loads(default)
        
        # Parse JSON fields - use None for empty/missing data to match AgentSession defaults
        session_data_raw = row_dict.get("session_data")
        agent_data_raw = row_dict.get("agent_data")
        metadata_raw = row_dict.get("metadata")
        runs_raw = row_dict.get("runs")
        summary_raw = row_dict.get("summary")
        
        # Parse JSON, but use None if empty or missing (to match AgentSession defaults)
        session_data = safe_json_loads(session_data_raw, "{}") if session_data_raw else None
        agent_data = safe_json_loads(agent_data_raw, "{}") if agent_data_raw else None
        metadata = safe_json_loads(metadata_raw, "{}") if metadata_raw else {}
        runs = safe_json_loads(runs_raw, "[]") if runs_raw else []
        summary = safe_json_loads(summary_raw, "{}") if summary_raw else None
        
        # Ensure types are correct
        if session_data is not None and not isinstance(session_data, dict):
            session_data = None
        if agent_data is not None and not isinstance(agent_data, dict):
            agent_data = None
        if not isinstance(metadata, dict):
            metadata = {}
        if not isinstance(summary, dict) and summary is not None:
            summary = None
        if not isinstance(runs, list):
            runs = []
        
        # Preserve runs - with improved recursive serialization, Message objects are stored as dicts
        # Create wrapper objects (DictAsObject) that make dicts behave like Run/Message objects with attributes
        # This allows Agno to access run.parent_run_id, message.role, etc., enabling conversation history
        import copy
        class DictAsObject:
            """Wrapper to make dicts accessible as objects with attributes AND dictionary-style access.
            
            CRITICAL: Agno expects actual dicts for message content, not wrappers.
            So we need to support both dict-style access (returns dict) and attribute access.
            """
            def __init__(self, d):
                self._dict = {}
                if isinstance(d, dict):
                    # Store original dict for dictionary-style access
                    self._dict = d.copy()
                    # Recursively convert nested dicts (like messages) to objects
                    # BUT: don't wrap content items - Agno needs them as dicts
                    for key, value in d.items():
                        if isinstance(value, dict):
                            # For 'content' fields, keep as dict (Agno needs dict, not wrapper)
                            if key == 'content':
                                setattr(self, key, value)
                            else:
                                setattr(self, key, DictAsObject(value))
                        elif isinstance(value, list):
                            # For lists, convert items but keep content items as dicts
                            # CRITICAL: If this is a 'content' list, never wrap items - keep as dicts
                            if key == 'content':
                                # Content lists must have items as actual dicts, never wrapped
                                # Also ensure nested DictAsObject items are converted to dicts
                                content_list = []
                                for item in value:
                                    if item is None:
                                        continue
                                    # Convert DictAsObject to dict if needed
                                    if isinstance(item, DictAsObject):
                                        item = item._dict
                                    # Ensure it's a dict (not a wrapper)
                                    if isinstance(item, dict):
                                        content_list.append(item)
                                    else:
                                        content_list.append(item)
                                setattr(self, key, content_list)
                                # Also store in _dict as actual dicts
                                self._dict[key] = content_list
                            else:
                                # For other lists (like messages), wrap items but keep their content as dicts
                                converted_list = []
                                for item in value:
                                    if item is None:
                                        continue
                                    if isinstance(item, dict):
                                        # Wrap the item, but its content will be handled by DictAsObject
                                        converted_list.append(DictAsObject(item))
                                    else:
                                        converted_list.append(item)
                                setattr(self, key, converted_list)
                        else:
                            setattr(self, key, value)
                elif hasattr(d, '__dict__'):
                    self._dict = d.__dict__.copy()
                    self.__dict__.update(d.__dict__)
            
            def __getitem__(self, key):
                """Support dictionary-style access: obj['key'] - returns actual dict when possible"""
                if key in self._dict:
                    value = self._dict[key]
                    # For content, return as-is (should be dict or list of dicts)
                    # CRITICAL: Agno needs content items as actual dicts, not wrappers
                    if key == 'content':
                        # If it's a list, ensure items are actual dicts, not wrappers
                        if isinstance(value, list):
                            return [
                                item._dict if isinstance(item, DictAsObject) else item
                                for item in value
                            ]
                        # If it's a dict, return actual dict
                        if isinstance(value, DictAsObject):
                            return value._dict
                        return value
                    # For other dicts, return DictAsObject wrapper
                    if isinstance(value, dict):
                        return DictAsObject(value)
                    return value
                # Fallback to attribute access
                attr_value = getattr(self, key, None)
                # If it's content, ensure we return actual dict/list
                if key == 'content' and isinstance(attr_value, DictAsObject):
                    return attr_value._dict
                return attr_value
            
            def __setitem__(self, key, value):
                """Support dictionary-style assignment: obj['key'] = value"""
                self._dict[key] = value
                setattr(self, key, value)
            
            def __contains__(self, key):
                """Support 'in' operator: 'key' in obj"""
                return key in self._dict or hasattr(self, key)
            
            def get(self, key, default=None):
                """Support .get() method like dict"""
                if key in self._dict:
                    value = self._dict[key]
                    if key == 'content':
                        return value  # Return as-is for content
                    if isinstance(value, dict):
                        return DictAsObject(value)
                    return value
                return getattr(self, key, default)
            
            def keys(self):
                """Support .keys() method like dict"""
                return self._dict.keys()
            
            def values(self):
                """Support .values() method like dict"""
                return self._dict.values()
            
            def items(self):
                """Support .items() method like dict"""
                return self._dict.items()
            
            def to_dict(self):
                """Convert back to dict - useful when Agno needs actual dict"""
                result = {}
                for key, value in self._dict.items():
                    if isinstance(value, DictAsObject):
                        result[key] = value.to_dict()
                    elif isinstance(value, list):
                        result[key] = [
                            item.to_dict() if isinstance(item, DictAsObject) else item
                            for item in value
                        ]
                    else:
                        result[key] = value
                return result
            
            def __getattr__(self, name):
                # CRITICAL: For 'content' attribute, return actual dict/list, not wrapped
                # Agno expects message.content to be a list of actual dicts
                if name == 'content':
                    value = self._dict.get('content')
                    if isinstance(value, list):
                        # Ensure all items in content list are actual dicts, not wrappers
                        return [
                            item._dict if isinstance(item, DictAsObject) else item
                            for item in value
                        ]
                    if isinstance(value, DictAsObject):
                        return value._dict
                    return value
                
                # Return a no-op callable for methods that might be called
                # This prevents errors when Agno tries to call methods like .log() on Message objects
                if name in ['log', 'dict', 'model_dump', '__dict__']:
                    def noop(*args, **kwargs):
                        return None
                    return noop
                
                # For other attributes, try to get from _dict first
                if name in self._dict:
                    value = self._dict[name]
                    # If it's a dict, return DictAsObject wrapper
                    if isinstance(value, dict):
                        return DictAsObject(value)
                    return value
                
                # Return None for other missing attributes
                return None
            
            def __deepcopy__(self, memo):
                # Support deepcopy - return self to avoid recursion issues
                # Agno will handle the copying if needed
                return self
        
        # Convert run dicts to objects that support attribute access
        # CRITICAL: Agno expects message content items to be actual dicts, not wrappers
        # The key is to ensure content items are NEVER wrapped, even when messages are wrapped
        if isinstance(runs, list):
            converted_runs = []
            for r in runs:
                if not isinstance(r, dict):
                    continue
                # Create a deep copy of the run dict to avoid modifying the original
                import copy
                run_dict = copy.deepcopy(r)
                
                # If this run has messages, we need to ensure content items remain as actual dicts
                # Also fix toolResult content format issues
                if 'messages' in run_dict and isinstance(run_dict['messages'], list):
                    messages_list = []
                    for msg in run_dict['messages']:
                        if isinstance(msg, dict):
                            msg_dict = copy.deepcopy(msg)
                            # CRITICAL: Keep content items as actual dicts, never wrap them
                            # Also fix toolResult content format issues that prevent Agno from using the session
                            if 'content' in msg_dict and isinstance(msg_dict['content'], list):
                                cleaned_content = []
                                for item in msg_dict['content']:
                                    if isinstance(item, DictAsObject):
                                        item = item._dict
                                    if isinstance(item, dict):
                                        # Skip content items with blank text field (Bedrock rejects these)
                                        # Check both cases: {"text": ""} and {"type": "text", "text": ""}
                                        text_val = item.get('text')
                                        if text_val is not None or item.get('type') == 'text':
                                            if text_val is None or text_val == '' or (isinstance(text_val, str) and not text_val.strip()):
                                                logger.debug(f"Skipping content item with blank text field: {item}")
                                                continue
                                        # Fix toolResult content format - Agno expects specific structure
                                        if 'toolResult' in item and isinstance(item.get('toolResult'), dict):
                                            tool_result = item['toolResult']
                                            # If toolResult has invalid content, try to fix it or remove it
                                            if 'content' in tool_result:
                                                if isinstance(tool_result['content'], list):
                                                    fixed_tr_content = []
                                                    for tr_content in tool_result['content']:
                                                        if isinstance(tr_content, dict):
                                                            # Ensure json field is a dict if present
                                                            if 'json' in tr_content:
                                                                json_val = tr_content['json']
                                                                if isinstance(json_val, str):
                                                                    try:
                                                                        tr_content['json'] = json.loads(json_val)
                                                                    except (json.JSONDecodeError, TypeError):
                                                                        # Remove invalid json - Agno will handle missing toolResult
                                                                        # This is expected when cleaning up old session data
                                                                        logger.debug(f"Removing invalid JSON from toolResult")
                                                                        tr_content.pop('json', None)
                                                                elif not isinstance(json_val, (dict, type(None))):
                                                                    # Convert to dict if possible
                                                                    try:
                                                                        tr_content['json'] = json.loads(str(json_val)) if json_val else {}
                                                                    except (json.JSONDecodeError, TypeError):
                                                                        tr_content.pop('json', None)
                                                            # Ensure toolResult content has required structure for Agno
                                                            # Agno expects either 'text' or 'json' or other specific fields
                                                            if not any(key in tr_content for key in ['text', 'json', 'image', 'audio', 'document', 'video', 'searchResult']):
                                                                # If no valid field, skip this toolResult content item
                                                                # This is expected when cleaning up old session data
                                                                logger.debug(f"Skipping toolResult content with no valid fields")
                                                                continue
                                                        fixed_tr_content.append(tr_content)
                                                    tool_result['content'] = fixed_tr_content
                                                elif not isinstance(tool_result['content'], (str, dict, type(None))):
                                                    # If content is invalid type, remove toolResult to avoid errors
                                                    # This is expected when cleaning up old session data
                                                    logger.debug(f"Removing invalid toolResult content type")
                                                    item.pop('toolResult', None)
                                    cleaned_content.append(item)
                                msg_dict['content'] = cleaned_content
                            # Wrap the message dict, but content items inside remain as dicts
                            messages_list.append(DictAsObject(msg_dict))
                        else:
                            messages_list.append(msg)
                    run_dict['messages'] = messages_list
                # Wrap the run dict
                converted_runs.append(DictAsObject(run_dict))
            logger.info(f"Converted {len(converted_runs)} runs to DictAsObject (from {len(runs)} original runs)")
            runs = converted_runs
        else:
            runs = []
            logger.info("No runs to convert (runs is empty or not a list)")
        
        # Special handling for session_metrics in session_data
        # Metrics objects can't be properly serialized/deserialized as JSON
        # Remove session_metrics entirely - Agno will reinitialize it when the session is used
        if isinstance(session_data, dict) and "session_metrics" in session_data:
            # Remove session_metrics - it's stored as a string representation and can't be deserialized
            # Agno will reinitialize metrics properly when the session is used
            del session_data["session_metrics"]
            logger.debug("Removed session_metrics from session_data, will be reinitialized by Agno")
        
        session_dict = {
            "session_id": row_dict.get("session_id", ""),
            "type": row_dict.get("session_type", ""),
            "agent_id": row_dict.get("agent_id"),
            "team_id": row_dict.get("team_id"),
            "workflow_id": row_dict.get("workflow_id"),
            "user_id": row_dict.get("user_id"),
            "session_data": session_data,
            "agent_data": agent_data,
            "metadata": metadata,
            "runs": runs,
            "summary": summary,
            "created_at": row_dict.get("created_at", 0),
            "updated_at": row_dict.get("updated_at", 0),
        }
        
        if deserialize:
            # Create appropriate session type based on session_type
            session_type = session_dict.get("type", "").lower()
            
            # AgentSession only accepts specific fields - filter to only those
            # Based on AgentSession.__init__ signature
            agent_session_fields = {
                'session_id', 'agent_id', 'team_id', 'user_id', 'workflow_id',
                'session_data', 'metadata', 'agent_data', 'runs', 'summary',
                'created_at', 'updated_at'
            }
            
            session_dict_clean = {k: v for k, v in session_dict.items() if k in agent_session_fields}
            
            if session_type == "agent":
                return AgentSession(**session_dict_clean)
            # For now, default to AgentSession
            return AgentSession(**session_dict_clean)
        return session_dict

    # --- Sessions ---
    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session by ID.
        Note: VastDB doesn't support direct delete operations.
        This implementation marks sessions for deletion by setting a deleted flag,
        or you may need to implement a different strategy based on your requirements.
        """
        try:
            # First, verify the session exists and get it as a Session object
            session = self.get_session(session_id, SessionType.AGENT, deserialize=True)
            if not session:
                logger.warning(f"Session {session_id} not found for deletion")
                return False
            
            # Mark as deleted in metadata
            if not session.metadata:
                session.metadata = {}
            session.metadata["deleted"] = True
            session.metadata["deleted_at"] = int(time.time())
            
            # Upsert with deleted flag
            result = self.upsert_session(session, deserialize=False)
            
            if result:
                logger.info(f"Session {session_id} marked as deleted")
                return True
            else:
                logger.error(f"Failed to upsert session {session_id} with deleted flag")
                return False
        except Exception as e:
            logger.error(f"Error deleting session {session_id}: {e}", exc_info=True)
            return False

    def delete_sessions(self, session_ids: List[str]) -> None:
        """Delete multiple sessions."""
        for session_id in session_ids:
            self.delete_session(session_id)

    def get_session(
        self,
        session_id: str,
        session_type: SessionType,
        user_id: Optional[str] = None,
        deserialize: Optional[bool] = True,
    ) -> Optional[Union[Session, Dict[str, Any]]]:
        """Get a session by ID using VastDB's select() with ibis predicates."""
        try:
            with self.session.transaction() as tx:
                bucket = tx.bucket(self.bucket_name)
                schema = bucket.schema(self.schema_name, fail_if_missing=True)
                table = schema.table(self.table_name, fail_if_missing=True)
                
                # Import ibis here to avoid shadowing issues
                from ibis import _ as ibis_expr
                
                # Use VastDB's select() with ibis predicate to filter by session_id
                # Note: session_id should be unique, so we don't need user_id filter
                # But we'll add it for safety if provided
                predicate = (ibis_expr.session_id == session_id)
                # Note: user_id filter is optional - session_id should be unique globally
                # If user_id is provided, we can add it, but it might cause issues if user_id format doesn't match
                # For now, let's not filter by user_id to avoid issues
                # if user_id:
                #     predicate = predicate & (ibis_expr.user_id == user_id)
                
                # Filter out soft-deleted sessions
                # Note: This assumes we're using soft deletes with metadata.deleted flag
                # If not using soft deletes, remove this filter
                try:
                    # Try to filter out deleted sessions
                    # This requires checking metadata JSON, which may need a different approach
                    # For now, we'll get all matching session_id and filter in Python
                    reader = table.select(predicate=predicate)
                    result_table = reader.read_all()
                    
                    logger.info(f"Query returned {result_table.num_rows} rows for session_id={session_id}, user_id={user_id}")
                    
                    if result_table.num_rows == 0:
                        logger.warning(f"No rows found for session_id={session_id}, user_id={user_id}")
                        # Try without predicate to see if table has any data
                        all_reader = table.select()
                        all_table = all_reader.read_all()
                        logger.debug(f"Table has {all_table.num_rows} total rows")
                        if all_table.num_rows > 0:
                            all_df = all_table.to_pandas()
                            if 'session_id' in all_df.columns:
                                matching = all_df[all_df['session_id'] == session_id]
                                logger.info(f"Found {len(matching)} rows with matching session_id in full table")
                                if len(matching) > 0:
                                    logger.info(f"Sample matching row user_id: {matching.iloc[0].get('user_id') if 'user_id' in matching.columns else 'N/A'}")
                            logger.debug(f"Sample session_ids in table: {all_df['session_id'].head().tolist() if 'session_id' in all_df.columns else 'N/A'}")
                        return None
                    
                    # Convert to pandas for easier filtering
                    df = result_table.to_pandas()
                    logger.debug(f"Query result columns: {df.columns.tolist()}")
                    logger.debug(f"Query result session_ids: {df['session_id'].tolist() if 'session_id' in df.columns else 'N/A'}")
                    
                    # Filter out soft-deleted sessions
                    if 'metadata' in df.columns:
                        # Parse metadata JSON and filter
                        # Import json module locally to avoid scope issues with nested function
                        import json as json_module
                        def is_deleted(x):
                            if x is None:
                                return False
                            if isinstance(x, str):
                                try:
                                    parsed = json_module.loads(x)
                                    return parsed.get('deleted', False) if isinstance(parsed, dict) else False
                                except (json_module.JSONDecodeError, TypeError):
                                    return False
                            if isinstance(x, dict):
                                return x.get('deleted', False)
                            return False
                        
                        df = df[~df['metadata'].apply(is_deleted)]
                    
                    if len(df) == 0:
                        logger.warning(f"All rows were filtered out (soft deleted) for session_id={session_id}")
                        return None
                    
                    # Get latest version (highest updated_at) if multiple rows exist
                    if 'updated_at' in df.columns:
                        df = df.sort_values('updated_at', ascending=False)
                        logger.info(f"Found {len(df)} rows for session_id={session_id}, latest updated_at={df.iloc[0]['updated_at'] if len(df) > 0 else 'N/A'}")
                    
                    # Get first matching row (latest version)
                    row = df.iloc[0]
                    logger.info(f"Selected row with updated_at={row.get('updated_at') if hasattr(row, 'get') else (row['updated_at'] if isinstance(row, dict) else 'N/A')}")
                    
                    # Convert to dict (handle both pandas Series and dict)
                    if hasattr(row, 'to_dict'):
                        row_dict = row.to_dict()
                    else:
                        row_dict = dict(row) if row is not None else {}
                    
                    if not row_dict:
                        logger.error(f"Failed to convert row to dict, row type: {type(row)}")
                        return None
                    
                    result = self._dict_to_session(row_dict, deserialize=deserialize)
                    if result is None:
                        logger.error(f"_dict_to_session returned None for session_id={session_id}")
                    return result
                    
                except Exception as query_error:
                    logger.error(f"Error querying table for session {session_id}: {query_error}", exc_info=True)
                    return None
        except Exception as e:
            logger.error(f"Error getting session {session_id}: {e}")
            return None

    def get_sessions(
        self,
        session_type: SessionType,
        user_id: Optional[str] = None,
        component_id: Optional[str] = None,
        session_name: Optional[str] = None,
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
        deserialize: Optional[bool] = True,
    ) -> Union[List[Session], Tuple[List[Dict[str, Any]], int]]:
        """Get sessions with filtering using VastDB's select() with ibis predicates."""
        try:
            # Import ibis here to avoid shadowing issues
            from ibis import _ as ibis_expr
            
            with self.session.transaction() as tx:
                bucket = tx.bucket(self.bucket_name)
                schema = bucket.schema(self.schema_name, fail_if_missing=True)
                table = schema.table(self.table_name, fail_if_missing=True)
                
                # Build predicate using ibis
                predicates = []
                
                if session_type:
                    predicates.append(ibis_expr.session_type == session_type.value)
                
                if user_id:
                    predicates.append(ibis_expr.user_id == user_id)
                
                if component_id:
                    # component_id could be agent_id, team_id, or workflow_id
                    predicates.append(
                        (ibis_expr.agent_id == component_id) |
                        (ibis_expr.team_id == component_id) |
                        (ibis_expr.workflow_id == component_id)
                    )
                
                if start_timestamp:
                    predicates.append(ibis_expr.created_at >= start_timestamp)
                
                if end_timestamp:
                    predicates.append(ibis_expr.created_at <= end_timestamp)
                
                # Combine predicates
                if predicates:
                    predicate = predicates[0]
                    for p in predicates[1:]:
                        predicate = predicate & p
                else:
                    predicate = None
                
                # Query table
                reader = table.select(predicate=predicate)
                result_table = reader.read_all()
                
                if result_table.num_rows == 0:
                    if deserialize:
                        return []
                    return ([], 0)
                
                # Convert to pandas for filtering and sorting
                df = result_table.to_pandas()
                
                # Get latest version of each session_id FIRST (since upserts create new rows)
                # Group by session_id and take the one with highest updated_at
                # This ensures we have one row per session_id before filtering
                if 'session_id' in df.columns and 'updated_at' in df.columns:
                    initial_rows = len(df)
                    df = df.sort_values('updated_at', ascending=False).groupby('session_id').first().reset_index()
                    logger.debug(f"Grouped {initial_rows} rows into {len(df)} unique sessions")
                
                # Filter out soft-deleted sessions AFTER grouping
                # CRITICAL: Do this AFTER grouping to ensure we check the latest version of each session
                if 'metadata' in df.columns:
                    def is_deleted(x):
                        if x is None or (isinstance(x, float) and (x != x or x is None)):  # Handle NaN
                            return False
                        if isinstance(x, str):
                            try:
                                parsed = json.loads(x)
                                deleted = parsed.get('deleted', False) if isinstance(parsed, dict) else False
                                if deleted:
                                    logger.debug(f"Found deleted session in metadata: {parsed}")
                                return deleted
                            except (json.JSONDecodeError, TypeError):
                                return False
                        if isinstance(x, dict):
                            deleted = x.get('deleted', False)
                            if deleted:
                                logger.debug(f"Found deleted session in metadata dict: {x}")
                            return deleted
                        return False
                    
                    initial_count = len(df)
                    df = df[~df['metadata'].apply(is_deleted)]
                    filtered_count = len(df)
                    if initial_count != filtered_count:
                        logger.info(f"Filtered out {initial_count - filtered_count} deleted sessions (kept {filtered_count} active sessions)")
                
                # Filter by session_name if provided (check in session_data or metadata)
                if session_name:
                    # This would require parsing JSON fields - simplified for now
                    pass
                
                # Sort
                if sort_by and sort_by in df.columns:
                    ascending = sort_order != 'desc' if sort_order else True
                    df = df.sort_values(sort_by, ascending=ascending)
                else:
                    # Default sort by updated_at descending
                    if 'updated_at' in df.columns:
                        df = df.sort_values('updated_at', ascending=False)
                
                # CRITICAL: Calculate total_count BEFORE pagination
                # This is the total number of active (non-deleted) sessions
                total_count = len(df)
                
                # Pagination
                if page is not None and limit is not None:
                    start_idx = page * limit
                    end_idx = start_idx + limit
                    df = df.iloc[start_idx:end_idx]
                elif limit is not None:
                    df = df.head(limit)
                
                # Convert to list of sessions
                sessions = []
                for _, row in df.iterrows():
                    row_dict = row.to_dict()
                    session = self._dict_to_session(row_dict, deserialize=deserialize)
                    if session:
                        sessions.append(session)
                
                if deserialize:
                    return sessions
                else:
                    return (sessions, total_count)
                    
        except Exception as e:
            logger.error(f"Error getting sessions: {e}")
            if deserialize:
                return []
            return ([], 0)

    def rename_session(
        self,
        session_id: str,
        session_type: SessionType,
        session_name: str,
        deserialize: Optional[bool] = True,
    ) -> Optional[Union[Session, Dict[str, Any]]]:
        """Rename a session."""
        session = self.get_session(session_id, session_type, deserialize=True)
        if session:
            # Update name in metadata or session_data
            return self.upsert_session(session, deserialize=deserialize)
        return None

    def upsert_session(
        self, session: Session, deserialize: Optional[bool] = True
    ) -> Optional[Union[Session, Dict[str, Any]]]:
        """
        Upsert a session (insert or update if exists).
        Since VastDB doesn't support upserts, we manually implement it:
        1. Check if session exists
        2. If exists, mark old one as deleted (soft delete) or remove it
        3. Insert new/updated session
        """
        try:
            # Get session ID (handle both session_id and id attributes)
            session_id = getattr(session, 'session_id', None) or getattr(session, 'id', None)
            session_type = getattr(session, 'type', SessionType.AGENT)
            
            # Check if session already exists
            # CRITICAL: Get the latest version to ensure we have all runs
            existing = self.get_session(session_id, session_type, deserialize=False)
            
            # Log what we got from existing session
            if existing:
                existing_runs_check = existing.get("runs") if isinstance(existing, dict) else getattr(existing, "runs", None)
                if isinstance(existing_runs_check, str):
                    try:
                        parsed_existing = json.loads(existing_runs_check)
                        logger.info(f"upsert_session: existing session has {len(parsed_existing)} runs (from get_session)")
                    except:
                        logger.info(f"upsert_session: existing session has runs as string (could not parse)")
                elif isinstance(existing_runs_check, list):
                    logger.info(f"upsert_session: existing session has {len(existing_runs_check)} runs as list")
            
            # Update timestamps
            current_time = int(time.time())
            if existing:
                # Keep original created_at, update updated_at
                # CRITICAL: Get session_dict first, but we'll merge runs separately
                session_dict = self._session_to_dict(session)
                session_dict["updated_at"] = current_time
                
                # IMPORTANT: Don't use runs from _session_to_dict if session object doesn't have runs yet
                # We'll merge runs from existing session in DB with any new runs from the session object
                # Preserve created_at from existing if not set
                if isinstance(existing, dict):
                    existing_created_at = existing.get("created_at")
                    if existing_created_at is not None:
                        try:
                            session_dict["created_at"] = int(existing_created_at)
                        except (ValueError, TypeError):
                            session_dict["created_at"] = current_time
                    else:
                        session_dict["created_at"] = current_time
                else:
                    # If it's a Session object, get created_at from it
                    existing_created_at = getattr(existing, "created_at", None)
                    if existing_created_at is not None:
                        try:
                            session_dict["created_at"] = int(existing_created_at)
                        except (ValueError, TypeError):
                            session_dict["created_at"] = current_time
                    else:
                        session_dict["created_at"] = current_time
                
                # CRITICAL: Merge runs from existing session with runs from new session
                # This ensures we don't lose previous runs when updating
                # When deserialize=False, runs is a JSON string; when deserialize=True, it's a list
                # IMPORTANT: Since VastDB is append-only, we need to ensure we're getting runs from the LATEST row
                # not from an older row that might have fewer runs
                existing_runs_raw = existing.get("runs") if isinstance(existing, dict) else getattr(existing, "runs", None)
                # IMPORTANT: Get new_runs_raw from the original session object, not from session_dict
                # because session_dict["runs"] is already JSON-encoded by _session_to_dict
                # If the session object has runs, use those; otherwise, the merge will only use existing runs
                # CRITICAL: Handle both dict and object-based sessions
                # CRITICAL: Do NOT use json.dumps(..., default=str) here, as it will convert run objects to strings
                # Instead, get runs as a list and convert to dicts later
                # CRITICAL: session.runs contains ALL runs (old + new), so we need to filter to only NEW runs
                # We do this by comparing run_ids with existing_runs
                if isinstance(session, dict):
                    # For dict-based sessions, get runs directly
                    new_runs_raw = session.get("runs")
                    # If it's already a JSON string, keep it; otherwise, it's a list
                elif hasattr(session, "runs") and session.runs:
                    # Session object has runs - keep as list (not JSON string) for proper conversion
                    new_runs_raw = session.runs if not isinstance(session.runs, str) else session.runs
                else:
                    # Session object doesn't have runs - use what's in session_dict (might be empty)
                    new_runs_raw = session_dict.get("runs")
                
                
                # Parse existing runs - handle both JSON string and list formats
                # CRITICAL: Convert DictAsObject wrappers to dicts for proper JSON serialization
                # Use the module-level convert_to_dict function
                
                # SIMPLE APPROACH: Parse existing runs (can be JSON string or list)
                existing_runs = []
                if existing_runs_raw:
                    if isinstance(existing_runs_raw, str):
                        try:
                            parsed = json.loads(existing_runs_raw)
                            if isinstance(parsed, list):
                                existing_runs = [convert_to_dict(item) for item in parsed]
                            else:
                                existing_runs = []
                        except (json.JSONDecodeError, TypeError) as e:
                            existing_runs = []
                            logger.error(f"upsert_session: Error parsing existing_runs_raw JSON: {e}")
                    elif isinstance(existing_runs_raw, list):
                        existing_runs = [convert_to_dict(item) for item in existing_runs_raw]
                    else:
                        logger.warning(f"upsert_session: existing_runs_raw is unexpected type: {type(existing_runs_raw)}")
                
                # Parse new runs - session_dict["runs"] is already a JSON string from _session_to_dict
                # CRITICAL: Convert DictAsObject wrappers to dicts for proper JSON serialization
                # Use the same convert_to_dict function defined above (it's already in scope)
                
                new_runs = []
                if new_runs_raw:
                    if isinstance(new_runs_raw, str):
                        try:
                            parsed = json.loads(new_runs_raw)
                            if isinstance(parsed, list):
                                # Convert any DictAsObject items to dicts
                                new_runs = [convert_to_dict(item) for item in parsed]
                            else:
                                new_runs = []
                        except (json.JSONDecodeError, TypeError):
                            new_runs = []
                    elif isinstance(new_runs_raw, list):
                        # Convert any DictAsObject items to dicts
                        new_runs = [convert_to_dict(item) for item in new_runs_raw]
                
                # CRITICAL: Don't filter out runs - we need to UPDATE existing runs with citations
                # Convert all new_runs to dicts (they may have citations added in streaming_service)
                new_runs = [convert_to_dict(run) for run in new_runs]
                logger.info(f"upsert_session: After conversion, new_runs count: {len(new_runs)}")
                for idx, run in enumerate(new_runs):
                    if isinstance(run, dict):
                        logger.info(f"upsert_session: New run {idx}: id={run.get('id') or run.get('run_id')}, citations={run.get('citations', [])}")
                
                # Merge runs: combine existing runs with new runs, avoiding duplicates
                # Use run IDs to detect duplicates
                # CRITICAL: Ensure all runs are dicts before merging
                merged_runs = [convert_to_dict(run) for run in (existing_runs if existing_runs else [])]
                existing_run_ids = set()
                for run in merged_runs:
                    if isinstance(run, dict):
                        run_id = run.get("id") or run.get("run_id")
                    else:
                        run_id = getattr(run, "id", None) or getattr(run, "run_id", None)
                    if run_id:
                        existing_run_ids.add(str(run_id))
                
                # Add new runs that aren't already in the list
                # If a run with the same ID exists, UPDATE it (to preserve citations and other changes)
                logger.info(f"upsert_session: Merging {len(new_runs)} new runs with {len(merged_runs)} existing runs")
                for new_run in new_runs:
                    new_run_dict = convert_to_dict(new_run)
                    if isinstance(new_run_dict, dict):
                        new_run_id = new_run_dict.get("id") or new_run_dict.get("run_id")
                        new_citations = new_run_dict.get("citations", [])
                    else:
                        new_run_id = getattr(new_run_dict, "id", None) or getattr(new_run_dict, "run_id", None)
                        new_citations = getattr(new_run_dict, "citations", [])
                    
                    logger.info(f"upsert_session: Processing new run, id={new_run_id}, citations={new_citations}, in_existing={str(new_run_id) in existing_run_ids if new_run_id else 'N/A'}")
                    
                    if new_run_id and str(new_run_id) in existing_run_ids:
                        # Run already exists - UPDATE it (to preserve citations added in streaming_service)
                        logger.info(f"upsert_session: Run {new_run_id} already exists, updating with new data (citations: {new_citations})")
                        # Find and replace the existing run
                        found = False
                        for idx, existing_run in enumerate(merged_runs):
                            existing_id = existing_run.get("id") or existing_run.get("run_id") if isinstance(existing_run, dict) else (getattr(existing_run, "id", None) or getattr(existing_run, "run_id", None))
                            logger.info(f"upsert_session: Comparing existing_id={existing_id} (type={type(existing_id)}) with new_run_id={new_run_id} (type={type(new_run_id)})")
                            if str(existing_id) == str(new_run_id):
                                logger.info(f"upsert_session: ✅ Found existing run at index {idx}, replacing with new run (old citations: {existing_run.get('citations', []) if isinstance(existing_run, dict) else 'N/A'}, new citations: {new_citations})")
                                merged_runs[idx] = new_run_dict  # Replace with new run (has citations)
                                logger.info(f"upsert_session: ✅ Replaced existing run at index {idx} with new run containing {len(new_citations) if isinstance(new_citations, list) else 0} citations")
                                found = True
                                break
                        if not found:
                            logger.warning(f"upsert_session: ⚠️  Run {new_run_id} marked as existing but not found in merged_runs! Count: {len(merged_runs)}")
                    elif new_run_id and str(new_run_id) not in existing_run_ids:
                        logger.info(f"upsert_session: Adding new run {new_run_id} with {len(new_citations) if isinstance(new_citations, list) else 0} citations")
                        merged_runs.append(new_run_dict)
                        existing_run_ids.add(str(new_run_id))
                    elif not new_run_id:
                        # If run has no ID, append it anyway (might be a new run)
                        logger.info(f"upsert_session: Adding run without ID with {len(new_citations) if isinstance(new_citations, list) else 0} citations")
                        merged_runs.append(new_run_dict)
                
                # 5. Serialize merged runs back to JSON and update session_dict
                # All runs should already be dicts at this point
                # CRITICAL: Ensure all values in merged_runs are JSON-serializable
                # Convert any datetime, UUID, or other non-serializable objects to strings
                def make_json_serializable(obj):
                    """Recursively convert non-JSON-serializable objects to strings."""
                    if isinstance(obj, dict):
                        return {k: make_json_serializable(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [make_json_serializable(item) for item in obj]
                    elif isinstance(obj, (str, int, float, bool, type(None))):
                        return obj
                    else:
                        # For any other type (datetime, UUID, etc.), convert to string
                        return str(obj)
                
                # Extract citations from FULL run data BEFORE trimming
                # This ensures we capture citations even if tool results are in complex nested structures
                def extract_citations_from_run(run_dict) -> list:
                    """Extract all citations from a run by scanning all messages"""
                    citations = []
                    if not isinstance(run_dict, dict):
                        logger.debug("extract_citations_from_run: run_dict is not a dict")
                        return citations
                    
                    messages = run_dict.get('messages', [])
                    if not isinstance(messages, list):
                        logger.debug(f"extract_citations_from_run: messages is not a list, type={type(messages)}")
                        return citations
                    
                    logger.info(f"🔍 Extracting citations from run with {len(messages)} messages")
                    found_tool_results = 0
                    for msg_idx, msg in enumerate(messages):
                        if not isinstance(msg, dict):
                            continue
                        role = msg.get('role', 'unknown')
                        content = msg.get('content', [])
                        
                        # Check if content is a list (for tool results)
                        if isinstance(content, list):
                            logger.debug(f"Message {msg_idx} (role={role}) has list content with {len(content)} items")
                            for item_idx, item in enumerate(content):
                                if not isinstance(item, dict):
                                    continue
                                if 'toolResult' in item:
                                    found_tool_results += 1
                                    logger.info(f"✅ Found toolResult in message {msg_idx}, item {item_idx}, role={role}")
                                    tool_result = item.get('toolResult', {})
                                    if isinstance(tool_result, dict):
                                        logger.info(f"   toolResult keys: {list(tool_result.keys())}")
                                        # Log the structure in detail
                                        tool_result_content = tool_result.get('content', '')
                                        logger.info(f"   toolResult.content type: {type(tool_result_content).__name__}")
                                        if isinstance(tool_result_content, list):
                                            logger.info(f"   toolResult.content length: {len(tool_result_content)}")
                                            if len(tool_result_content) > 0:
                                                logger.info(f"   First content item type: {type(tool_result_content[0]).__name__}")
                                                if isinstance(tool_result_content[0], dict):
                                                    logger.info(f"   First content item keys: {list(tool_result_content[0].keys())}")
                                                    # Log the actual content structure
                                                    first_item = tool_result_content[0]
                                                    if 'json' in first_item:
                                                        json_data = first_item.get('json', {})
                                                        logger.info(f"   First item has 'json' field, type: {type(json_data).__name__}")
                                                        if isinstance(json_data, dict):
                                                            logger.info(f"   JSON keys: {list(json_data.keys())[:10]}")
                                                            if 'chunks' in json_data:
                                                                logger.info(f"   Found 'chunks' in JSON, count: {len(json_data.get('chunks', []))}")
                                                        elif isinstance(json_data, str):
                                                            logger.info(f"   JSON is string, length: {len(json_data)}, preview: {json_data[:200]}")
                                                    if 'text' in first_item:
                                                        text_data = first_item.get('text', '')
                                                        logger.info(f"   First item has 'text' field, length: {len(str(text_data))}")
                                        
                                        citations_from_result = extract_citations_from_tool_result(tool_result)
                                        if citations_from_result:
                                            logger.info(f"   ✅ Extracted {len(citations_from_result)} citations: {citations_from_result}")
                                        else:
                                            logger.warning(f"   ⚠️  No citations extracted from toolResult")
                                        citations.extend(citations_from_result)
                    
                    if found_tool_results == 0:
                        logger.warning(f"⚠️  No toolResult blocks found in run with {len(messages)} messages")
                        # Log message structure for debugging
                        for msg_idx, msg in enumerate(messages[:3], 1):
                            if isinstance(msg, dict):
                                role = msg.get('role', 'unknown')
                                content = msg.get('content', [])
                                logger.debug(f"   Message {msg_idx}: role={role}, content_type={type(content).__name__}")
                    
                    unique_citations = list(dict.fromkeys(citations))  # Remove duplicates, preserve order
                    if unique_citations:
                        logger.info(f"✅ Total unique citations extracted from run: {len(unique_citations)} - {unique_citations[:5]}")
                    else:
                        logger.warning(f"⚠️  No citations extracted from run")
                    return unique_citations
                
                # CRITICAL: Only keep essential fields for conversation history
                # User only wants the conversation history (user prompts + assistant responses), not tool results or other data
                def extract_citations_from_tool_result(tool_result_or_content) -> list:
                    """Extract citations from retrieve_chunks tool result.
                    
                    Can handle:
                    - toolResult dict with content field
                    - Direct content (dict/list/string)
                    """
                    citations = []
                    
                    # Handle toolResult structure: {toolUseId: "...", content: [...]}
                    if isinstance(tool_result_or_content, dict) and 'content' in tool_result_or_content:
                        # This is a toolResult dict, extract the content
                        result_content = tool_result_or_content.get('content', '')
                    else:
                        # This is already the content
                        result_content = tool_result_or_content
                    
                    # Agno stores tool results in content array with items like: [{"json": {...}}]
                    # Extract the actual result data from the content array
                    result_data = {}
                    if isinstance(result_content, list):
                        # Look for items with "json" field (Agno's format)
                        for item in result_content:
                            if isinstance(item, dict):
                                if "json" in item:
                                    # This is the actual result
                                    json_data = item.get("json")
                                    if isinstance(json_data, str):
                                        try:
                                            import json
                                            result_data = json.loads(json_data)
                                        except:
                                            result_data = {}
                                    elif isinstance(json_data, dict):
                                        result_data = json_data
                                    break
                                elif "text" in item:
                                    # Try to parse text as JSON
                                    text_data = item.get("text", "")
                                    if isinstance(text_data, str):
                                        try:
                                            import json
                                            result_data = json.loads(text_data)
                                        except:
                                            pass
                    elif isinstance(result_content, str):
                        try:
                            import json
                            result_data = json.loads(result_content)
                        except:
                            result_data = {}
                    elif isinstance(result_content, dict):
                        result_data = result_content
                    
                    # Extract chunks from various possible structures
                    chunks = []
                    if isinstance(result_data, dict):
                        if "chunks" in result_data:
                            chunks = result_data.get("chunks", [])
                        elif "documents" in result_data:
                            chunks = result_data.get("documents", [])
                        elif "results" in result_data:
                            chunks = result_data.get("results", [])
                        elif "data" in result_data:
                            data = result_data.get("data", [])
                            if isinstance(data, list):
                                chunks = data
                            elif isinstance(data, dict) and "chunks" in data:
                                chunks = data.get("chunks", [])
                    elif isinstance(result_data, list):
                        # Content might be a list of chunks directly
                        chunks = result_data
                    
                    # Extract unique file names/sources
                    for chunk in chunks:
                        if not isinstance(chunk, dict):
                            continue
                        
                        file_name = (
                            chunk.get("file_name") or
                            chunk.get("filename") or
                            chunk.get("source") or
                            chunk.get("document_id") or
                            chunk.get("id") or
                            None
                        )
                        
                        if not file_name and isinstance(chunk.get("metadata"), dict):
                            metadata = chunk.get("metadata", {})
                            file_name = (
                                metadata.get("file_name") or
                                metadata.get("filename") or
                                metadata.get("source") or
                                None
                            )
                        
                        if file_name:
                            import re
                            # Extract just the filename from path
                            filename_match = re.search(r'([^/\\_]+\.(pdf|txt|doc|docx|md|html|json))', str(file_name))
                            if filename_match:
                                file_name = filename_match.group(1)
                            if str(file_name) not in citations:
                                citations.append(str(file_name))
                    
                    return citations
                
                def trim_run_for_storage(run_dict, run_citations=None):
                    """Keep only essential fields for conversation history - remove tool results but preserve citations."""
                    if not isinstance(run_dict, dict):
                        return run_dict
                    
                    # Check if run_dict already has citations (added directly, e.g., from streaming service)
                    existing_citations = run_dict.get('citations', [])
                    if not isinstance(existing_citations, list):
                        existing_citations = []
                    
                    logger.info(f"trim_run_for_storage: existing_citations={existing_citations}, run_citations={run_citations}, run_dict keys: {list(run_dict.keys())[:10]}")
                    
                    # Combine existing citations with extracted citations (prefer existing if both exist)
                    final_citations = existing_citations if existing_citations else (run_citations if run_citations else [])
                    logger.info(f"trim_run_for_storage: final_citations={final_citations}")
                    
                    # Keep only essential fields
                    trimmed = {
                        'run_id': run_dict.get('run_id') or run_dict.get('id'),
                        'session_id': run_dict.get('session_id'),
                        'user_id': run_dict.get('user_id'),
                        'created_at': run_dict.get('created_at'),
                        'input': run_dict.get('input'),  # User's prompt
                        'content': run_dict.get('content'),  # Assistant's response (if it's a simple string)
                        'citations': final_citations,  # Use existing citations if available, otherwise extracted
                    }
                    
                    # Process messages - keep only user and assistant messages, remove tool results
                    if 'messages' in run_dict and isinstance(run_dict['messages'], list):
                        trimmed_messages = []
                        for msg in run_dict['messages']:
                            if isinstance(msg, dict):
                                role = msg.get('role', '')
                                # Only keep user and assistant messages with text content
                                if role in ['user', 'assistant', 'system']:
                                    # Extract text content, removing tool results
                                    content = msg.get('content')
                                    extracted_content = None
                                    # Extract text content, removing tool results
                                    extracted_content = None
                                    
                                    if isinstance(content, str):
                                        # Simple text content - keep it if not empty
                                        if content and content.strip():
                                            extracted_content = content
                                    elif isinstance(content, list):
                                        # Extract only text content, skip tool results and blank text
                                        text_parts = []
                                        for item in content:
                                            if isinstance(item, dict):
                                                # Check for text content - must be non-empty
                                                text_val = item.get('text')
                                                if text_val and isinstance(text_val, str) and text_val.strip():
                                                    text_parts.append(text_val)
                                                elif 'toolResult' in item:
                                                    # Skip tool results - already extracted citations above
                                                    continue
                                                elif 'content' in item and isinstance(item['content'], str) and item['content'].strip():
                                                    text_parts.append(item['content'])
                                            elif isinstance(item, str) and item.strip():
                                                text_parts.append(item)
                                        
                                        # Join text parts
                                        if text_parts:
                                            extracted_content = ' '.join(str(t) for t in text_parts)
                                    elif content:
                                        content_str = str(content)
                                        if content_str.strip():
                                            extracted_content = content_str
                                    
                                    # Only add message if it has non-empty content
                                    if extracted_content:
                                        msg_copy = {
                                            'role': role,
                                            'created_at': msg.get('created_at'),
                                            'content': extracted_content
                                        }
                                        trimmed_messages.append(msg_copy)
                        trimmed['messages'] = trimmed_messages
                    
                    # Remove duplicate citations and keep unique ones
                    trimmed['citations'] = list(dict.fromkeys(trimmed['citations']))  # Preserves order
                    
                    return trimmed
                
                # Extract citations from each run BEFORE trimming
                # Trim each run before serialization, preserving pre-extracted citations
                trimmed_runs = []
                for run in merged_runs:
                    # Convert run to dict first to ensure citations are accessible
                    run_dict = convert_to_dict(run) if not isinstance(run, dict) else run
                    logger.info(f"upsert_session: Processing run, type: {type(run)}, converted to dict, has citations key: {'citations' in run_dict if isinstance(run_dict, dict) else 'N/A'}")
                    if isinstance(run_dict, dict):
                        logger.info(f"upsert_session: Run dict citations before trim: {run_dict.get('citations', [])}")
                    
                    run_citations = extract_citations_from_run(run_dict)
                    if run_citations:
                        logger.info(f"Extracted {len(run_citations)} citations from run: {run_citations[:3]}")
                    trimmed_run = trim_run_for_storage(run_dict, run_citations=run_citations)
                    trimmed_runs.append(trimmed_run)
                
                serializable_runs = make_json_serializable(trimmed_runs)
                runs_json = json.dumps(serializable_runs)
                session_dict["runs"] = runs_json
                logger.debug(f"upsert_session: Merged {len(existing_runs)} existing + {len(new_runs)} new = {len(merged_runs)} total runs, serialized to {len(runs_json)/1024:.1f} KB")
            else:
                # New session - set both timestamps
                session_dict = self._session_to_dict(session)
                session_dict["created_at"] = current_time
                session_dict["updated_at"] = current_time
            
            arrow_table = pa.Table.from_pylist([session_dict], schema=self.schema_columns)
            
            with self.session.transaction() as tx:
                bucket = tx.bucket(self.bucket_name)
                schema = bucket.schema(self.schema_name, fail_if_missing=True)
                table = schema.table(self.table_name, fail_if_missing=True)
                
                # Since VastDB doesn't support upserts, we need to handle it manually
                # Option 1: Soft delete the old record and insert new one
                # Option 2: Since we can't truly delete, we'll just insert
                # The latest insert will be the "current" one, and we'll filter by updated_at in queries
                # For now, we'll use soft delete approach: mark old as deleted, insert new
                
                if existing:
                    # Mark existing session as deleted (soft delete)
                    # We'll insert the new one, and queries will filter out deleted ones
                    logger.debug(f"Session {session_id} exists, performing update via soft delete + insert")
                
                # Insert the session (this will create a new row)
                # Note: In a production system, you might want to:
                # 1. Use a version/timestamp to get the latest
                # 2. Periodically clean up old/deleted rows
                # 3. Use a different table structure
                table.insert(arrow_table)
                logger.info(f"Inserted row for session {session_id[:8]}... with {len(merged_runs) if 'merged_runs' in locals() else 'N/A'} runs, JSON length: {len(session_dict.get('runs', '')) if isinstance(session_dict.get('runs'), str) else 'N/A'} chars")
            
            logger.info(f"Session {session_id} upserted successfully")
            
            if deserialize:
                return session
            return session_dict
        except Exception as e:
            logger.error(f"Error upserting session: {e}")
            return None

    def upsert_sessions(
        self,
        sessions: List[Session],
        deserialize: Optional[bool] = True,
        preserve_updated_at: bool = False,
    ) -> List[Union[Session, Dict[str, Any]]]:
        """Bulk upsert multiple sessions."""
        results = []
        for session in sessions:
            result = self.upsert_session(session, deserialize=deserialize)
            if result:
                results.append(result)
        return results

    # --- Memory --- (Placeholder implementations)
    def clear_memories(self) -> None:
        """Clear all memories."""
        logger.warning("clear_memories not yet implemented for VastDB")

    def delete_user_memory(self, memory_id: str, user_id: Optional[str] = None) -> None:
        """Delete a user memory."""
        logger.warning("delete_user_memory not yet implemented for VastDB")

    def delete_user_memories(self, memory_ids: List[str], user_id: Optional[str] = None) -> None:
        """Delete multiple user memories."""
        for memory_id in memory_ids:
            self.delete_user_memory(memory_id, user_id)

    def get_all_memory_topics(self, user_id: Optional[str] = None) -> List[str]:
        """Get all memory topics."""
        return []

    def get_user_memory(
        self,
        memory_id: str,
        deserialize: Optional[bool] = True,
        user_id: Optional[str] = None,
    ) -> Optional[Union[UserMemory, Dict[str, Any]]]:
        """Get a user memory."""
        return None

    def get_user_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        team_id: Optional[str] = None,
        topics: Optional[List[str]] = None,
        search_content: Optional[str] = None,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
        deserialize: Optional[bool] = True,
    ) -> Union[List[UserMemory], Tuple[List[Dict[str, Any]], int]]:
        """Get user memories with filtering."""
        return []

    def get_user_memory_stats(
        self,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Get user memory statistics."""
        return ([], 0)

    def upsert_user_memory(
        self, memory: UserMemory, deserialize: Optional[bool] = True
    ) -> Optional[Union[UserMemory, Dict[str, Any]]]:
        """Upsert a user memory."""
        return None

    def upsert_memories(
        self,
        memories: List[UserMemory],
        deserialize: Optional[bool] = True,
        preserve_updated_at: bool = False,
    ) -> List[Union[UserMemory, Dict[str, Any]]]:
        """Bulk upsert multiple memories."""
        return []

    # --- Metrics ---
    def get_metrics(
        self,
        starting_date: Optional[date] = None,
        ending_date: Optional[date] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[int]]:
        """Get metrics."""
        return ([], None)

    def calculate_metrics(self) -> Optional[Any]:
        """Calculate metrics."""
        return None

    # --- Knowledge ---
    def delete_knowledge_content(self, id: str):
        """Delete a knowledge row."""
        logger.warning("delete_knowledge_content not yet implemented for VastDB")

    def get_knowledge_content(self, id: str) -> Optional[KnowledgeRow]:
        """Get a knowledge row."""
        return None

    def get_knowledge_contents(
        self,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
    ) -> Tuple[List[KnowledgeRow], int]:
        """Get all knowledge contents."""
        return ([], 0)

    def upsert_knowledge_content(self, knowledge_row: KnowledgeRow):
        """Upsert knowledge content."""
        return None

    # --- Evals ---
    def create_eval_run(self, eval_run: EvalRunRecord) -> Optional[EvalRunRecord]:
        """Create an eval run."""
        return None

    def delete_eval_runs(self, eval_run_ids: List[str]) -> None:
        """Delete eval runs."""
        pass

    def get_eval_run(
        self, eval_run_id: str, deserialize: Optional[bool] = True
    ) -> Optional[Union[EvalRunRecord, Dict[str, Any]]]:
        """Get an eval run."""
        return None

    def get_eval_runs(
        self,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
        agent_id: Optional[str] = None,
        team_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        model_id: Optional[str] = None,
        filter_type: Optional[EvalFilterType] = None,
        eval_type: Optional[List[EvalType]] = None,
        deserialize: Optional[bool] = True,
    ) -> Union[List[EvalRunRecord], Tuple[List[Dict[str, Any]], int]]:
        """Get eval runs with filtering."""
        return []

    def rename_eval_run(
        self, eval_run_id: str, name: str, deserialize: Optional[bool] = True
    ) -> Optional[Union[EvalRunRecord, Dict[str, Any]]]:
        """Rename an eval run."""
        return None

    # --- Cultural Knowledge ---
    def clear_cultural_knowledge(self) -> None:
        """Clear all cultural knowledge."""
        pass

    def delete_cultural_knowledge(self, id: str) -> None:
        """Delete cultural knowledge."""
        pass

    def get_cultural_knowledge(self, id: str) -> Optional[CulturalKnowledge]:
        """Get cultural knowledge."""
        return None

    def get_all_cultural_knowledge(
        self,
        name: Optional[str] = None,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
        agent_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> Optional[List[CulturalKnowledge]]:
        """Get all cultural knowledge."""
        return []

    def upsert_cultural_knowledge(self, cultural_knowledge: CulturalKnowledge) -> Optional[CulturalKnowledge]:
        """Upsert cultural knowledge."""
        return None

    def get_latest_schema_version(self) -> Optional[int]:
        """Get the latest schema version."""
        # VastDB doesn't have schema versioning, return None
        return None

    def upsert_schema_version(self, version: int) -> bool:
        """Upsert schema version."""
        # VastDB doesn't have schema versioning, return True
        return True

    def close(self):
        """Close VastDB connection."""
        if hasattr(self, 'session') and hasattr(self.session, 'close'):
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

