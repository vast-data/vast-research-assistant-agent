"""
Collections routes
"""
import logging
from typing import Optional, List
from fastapi import HTTPException, Depends, UploadFile, File, Form

from models import (
    CollectionsResponse, CollectionItem,
    CreateCollectionRequest, CreateCollectionResponse,
    DeleteCollectionsResponse,
    ListDocumentsResponse, DocumentItem
)
from services.auth import get_token
from services.rag import list_collections_from_rag, create_collection, delete_collections, upload_document, list_documents

logger = logging.getLogger(__name__)


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
            collections_data = list_collections_from_rag(token=token)
            
            # Extract collections from response
            if isinstance(collections_data, list):
                collections_list = collections_data
            elif isinstance(collections_data, dict) and "collections" in collections_data:
                collections_list = collections_data["collections"]
            elif isinstance(collections_data, dict) and "items" in collections_data:
                collections_list = collections_data["items"]
            else:
                collections_list = []
            
            # Apply limit if specified (limit=-1 means unlimited)
            if limit is not None and limit != -1 and limit > 0:
                collections_list = collections_list[:limit]
            
            # Convert to CollectionItem models
            collection_items = []
            for item in collections_list:
                if isinstance(item, dict):
                    # Extract collection name/ID from various possible fields
                    coll_id = (
                        item.get("full_collection_name") or 
                        item.get("collection_name") or 
                        item.get("id") or 
                        item.get("collection_id") or 
                        ""
                    )
                    
                    # IE returns chunk_count/num_files; older APIs used num_entities
                    raw_entities = (
                        item.get("num_entities")
                        if item.get("num_entities") is not None
                        else item.get("chunk_count")
                        if item.get("chunk_count") is not None
                        else item.get("num_files")
                    )
                    num_entities = int(raw_entities) if raw_entities is not None else 0

                    # Build a descriptive string from available metadata
                    desc_parts = []
                    if item.get("description"):
                        desc_parts.append(item.get("description"))
                    if num_entities:
                        desc_parts.append(f"{num_entities} chunks")
                    if item.get("num_files") is not None:
                        desc_parts.append(f"{item.get('num_files')} files")
                    if item.get("source"):
                        desc_parts.append(f"source: {item.get('source')}")
                    if item.get("allowed_users"):
                        desc_parts.append(f"users: {', '.join(item.get('allowed_users', []))}")
                    
                    coll_desc = " | ".join(desc_parts) if desc_parts else (
                        item.get("name") or 
                        f"Collection: {coll_id}" if coll_id else 
                        "No description"
                    )

                    if coll_id:
                        collection_items.append(CollectionItem(
                            id=coll_id,
                            description=coll_desc,
                            num_entities=num_entities
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
        is_public: bool = Form(False)
    ) -> dict:
        """
        Upload a document to a collection in the RAG backend.
        
        Args:
            collection_name: Name of the collection to upload to
            file: The file to upload
            token: JWT token from Authorization header
            allowed_groups: Optional comma-separated list of allowed groups
            is_public: Whether the document should be public
        
        Returns:
            Dictionary with upload result
        """
        try:
            # Parse allowed_groups if provided
            groups_list = None
            if allowed_groups:
                groups_list = [g.strip() for g in allowed_groups.split(",") if g.strip()]
            
            # Read file content
            file_content = await file.read()
            
            result = upload_document(
                collection_name=collection_name,
                file_content=file_content,
                filename=file.filename,
                token=token,
                allowed_groups=groups_list or ["public-read"],
                is_public=is_public
            )
            
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error uploading document: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to upload document: {str(e)}")

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
