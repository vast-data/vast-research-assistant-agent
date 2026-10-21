"""
Collections routes
"""
import json
import logging
from typing import Optional, List, Dict, Any, Tuple
from fastapi import HTTPException, Depends, UploadFile, File, Form

from models import (
    CollectionsResponse, CollectionItem,
    FullCollectionsResponse, FullCollectionItem,
    CreateCollectionRequest, CreateCollectionResponse,
    DeleteCollectionsRequest, DeleteCollectionsResponse,
    UploadDocumentRequest,
    ListDocumentsResponse, DocumentItem
)
from services.auth import get_token
from services.rag import list_collections_from_rag, create_collection, delete_collections, upload_document, list_documents

logger = logging.getLogger(__name__)


def _normalize_collections_list(collections_data: Any) -> List[Dict[str, Any]]:
    """Normalize IE/RAG list_collections response to a list of dicts."""
    if isinstance(collections_data, list):
        return [item for item in collections_data if isinstance(item, dict)]
    if isinstance(collections_data, dict):
        for key in ("collections", "items"):
            if key in collections_data and isinstance(collections_data[key], list):
                return [item for item in collections_data[key] if isinstance(item, dict)]
    return []


def _collection_id(item: Dict[str, Any]) -> str:
    return (
        item.get("full_collection_name")
        or item.get("collection_name")
        or item.get("id")
        or item.get("collection_id")
        or ""
    )


def _ie_collection_metrics(item: Dict[str, Any]) -> Tuple[int, int, int]:
    """
    Extract chunk/file/size metrics from Insight Engine collection list fields.

    IE returns: chunk_count, num_files, total_size_bytes (and full_collection_name).
    """
    chunk_count = item.get("chunk_count")
    if chunk_count is None:
        chunk_count = item.get("num_chunks")
    num_files = item.get("num_files")
    if num_files is None:
        num_files = item.get("num_unique_files")
    total_size = item.get("total_size_bytes")
    if total_size is None:
        total_size = item.get("total_doc_size_bytes")

    return int(chunk_count or 0), int(num_files or 0), int(total_size or 0)


class CollectionsRouter:
    """Router for collections endpoints"""
    
    @staticmethod
    async def get_collections(
        limit: Optional[int] = None,
        token: str = Depends(get_token)
    ) -> CollectionsResponse:
        """
        List all available collections for RAG queries.
        
        Args:
            limit: Optional limit on number of collections to return (-1 for unlimited)
            token: JWT token from Authorization header
        
        Returns:
            CollectionsResponse with list of collections
        """
        try:
            collections_list = _normalize_collections_list(list_collections_from_rag(token=token))

            # Apply limit if specified (limit=-1 means unlimited)
            if limit is not None and limit != -1 and limit > 0:
                collections_list = collections_list[:limit]

            collection_items = []
            for item in collections_list:
                coll_id = _collection_id(item)
                if not coll_id:
                    continue

                chunk_count, num_files, _ = _ie_collection_metrics(item)
                # GUI treats num_entities as document/file count (empty = 0 files).
                num_entities = num_files

                desc_parts = []
                if item.get("description"):
                    desc_parts.append(item.get("description"))
                desc_parts.append(f"{num_files} files")
                if chunk_count:
                    desc_parts.append(f"{chunk_count} chunks")
                if item.get("source"):
                    desc_parts.append(f"source: {item.get('source')}")
                if item.get("allowed_users"):
                    desc_parts.append(f"users: {', '.join(item.get('allowed_users', []))}")

                coll_desc = " | ".join(desc_parts) if desc_parts else (
                    item.get("name") or f"Collection: {coll_id}"
                )

                collection_items.append(CollectionItem(
                    id=coll_id,
                    description=coll_desc,
                    num_entities=num_entities,
                    num_files=num_files,
                    num_chunks=chunk_count,
                ))

            return CollectionsResponse(collections=collection_items)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting collections: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to get collections: {str(e)}")
    
    @staticmethod
    async def create_collection_endpoint(
        request: CreateCollectionRequest,
        token: str = Depends(get_token)
    ) -> CreateCollectionResponse:
        """
        Create a new collection in the RAG backend.
        
        Args:
            request: CreateCollectionRequest with collection_name and is_public
            token: JWT token from Authorization header
        
        Returns:
            CreateCollectionResponse with creation result
        """
        try:
            result = create_collection(
                collection_name=request.collection_name,
                token=token,
                is_public=request.is_public
            )
            return CreateCollectionResponse(
                message="Collection created successfully",
                collection_name=request.collection_name,
                result=result
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error creating collection: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to create collection: {str(e)}")
    
    @staticmethod
    async def delete_collections_endpoint(
        collection_names: List[str],
        token: str = Depends(get_token)
    ) -> DeleteCollectionsResponse:
        """
        Delete one or more collections from the RAG backend.
        
        Args:
            collection_names: List of collection names to delete (passed as JSON body)
            token: JWT token from Authorization header
        
        Returns:
            DeleteCollectionsResponse with deletion result
        """
        try:
            result = delete_collections(
                collection_names=collection_names,
                token=token
            )
            return DeleteCollectionsResponse(
                message="Collections deleted successfully",
                deleted_collections=collection_names,
                result=result
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting collections: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to delete collections: {str(e)}")
    
    @staticmethod
    async def upload_document_endpoint(
        collection_name: str = Form(...),
        file: UploadFile = File(...),
        token: str = Depends(get_token),
        allowed_groups: Optional[str] = Form(None),
        is_public: bool = Form(False),
        tags: Optional[str] = Form(None),
    ) -> dict:
        """
        Upload a document to a collection in the RAG backend.
        
        Args:
            collection_name: Name of the collection to upload to
            file: The file to upload
            token: JWT token from Authorization header
            allowed_groups: Optional comma-separated list of allowed groups
            is_public: Whether the document should be public
            tags: Optional JSON object string of metadata tags,
                  e.g. '{"subject_id":"10009035","note_type":"Discharge_summary"}'
        
        Returns:
            Dictionary with upload result
        """
        try:
            # Parse allowed_groups if provided
            groups_list = None
            if allowed_groups:
                groups_list = [g.strip() for g in allowed_groups.split(",") if g.strip()]

            tags_dict = None
            if tags:
                parsed = json.loads(tags)
                if not isinstance(parsed, dict):
                    raise HTTPException(status_code=400, detail="tags must be a JSON object")
                tags_dict = {str(k): str(v) for k, v in parsed.items()}
            
            # Read file content
            file_content = await file.read()
            
            result = upload_document(
                collection_name=collection_name,
                file_content=file_content,
                filename=file.filename,
                token=token,
                allowed_groups=groups_list or ["public-read"],
                is_public=is_public,
                tags=tags_dict,
            )
            
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error uploading document: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to upload document: {str(e)}")

    @staticmethod
    async def get_full_collections(
        collection_name: Optional[str] = None,
        token: str = Depends(get_token)
    ) -> FullCollectionsResponse:
        """
        Get full collection details with metadata from Insight Engine list API.

        IE stores chunks in a shared schema (no per-collection VastDB tables).
        Metrics come from IE GET /collections fields:
        chunk_count, num_files, total_size_bytes, full_collection_name.
        """
        try:
            collections_list = _normalize_collections_list(list_collections_from_rag(token=token))

            if collection_name:
                collections_list = [
                    item for item in collections_list
                    if _collection_id(item) == collection_name
                ]
                if not collections_list:
                    # Still return a zeroed row so the GUI can render the id
                    collections_list = [{"full_collection_name": collection_name}]

            collection_items: List[FullCollectionItem] = []
            for item in collections_list:
                coll_id = _collection_id(item)
                if not coll_id:
                    continue

                num_chunks, num_unique_files, total_doc_size = _ie_collection_metrics(item)
                total_doc_size_mb = round(total_doc_size / (1024 * 1024), 2) if total_doc_size else 0.0
                collection_items.append(FullCollectionItem(
                    name=coll_id,
                    num_chunks=num_chunks,
                    num_unique_files=num_unique_files,
                    total_doc_size_bytes=total_doc_size,
                    total_doc_size_mb=total_doc_size_mb,
                ))

            return FullCollectionsResponse(
                collections=collection_items,
                total=len(collection_items),
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting full collections: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to get full collections: {str(e)}")
    
    @staticmethod
    async def list_documents_endpoint(
        collection_name: str,
        token: str = Depends(get_token),
        is_public: bool = False,
        limit: int = 0
    ) -> ListDocumentsResponse:
        """
        List documents in a collection from the RAG backend.
        
        Args:
            collection_name: Name of the collection to list documents from
            token: JWT token from Authorization header
            is_public: Whether to list only public documents (default: False)
            limit: Maximum number of documents to return (0 means no limit, default: 0)
        
        Returns:
            ListDocumentsResponse with list of documents
        """
        try:
            result = list_documents(
                collection_name=collection_name,
                token=token,
                is_public=is_public,
                limit=limit
            )
            
            # Convert to response model
            documents = []
            if "documents" in result:
                for doc in result["documents"]:
                    if isinstance(doc, dict):
                        doc_name = doc.get("document_name", "")
                        if doc_name:
                            documents.append(DocumentItem(document_name=doc_name))
            
            return ListDocumentsResponse(
                message=result.get("message", "Documents retrieved successfully"),
                total_documents=result.get("total_documents", len(documents)),
                documents=documents
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error listing documents: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to list documents: {str(e)}")

