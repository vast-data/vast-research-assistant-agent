// Collection data models and interfaces (aligned with API_DOC.md)

export interface Collection {
  id: string; // full_collection_name
  description: string; // formatted description
  title: string; // extracted collection name from id
  private: boolean; // true if collection id has a prefix before '__'
  num_entities: number; // number of documents in the collection
}

export interface CollectionsResponse {
  collections: Collection[];
}

export interface CreateCollectionData {
  collection_name: string;
  is_public: boolean;
}

export interface CreateCollectionResponse {
  message: string;
  collection_name: string;
  result: {
    message: string;
    successful: string[];
    failed: null | any[];
    total_success: number;
    total_failed: number;
  };
}

export interface DeleteCollectionsResponse {
  message: string;
  deleted_collections: string[];
  result?: any;
}

export interface CollectionFilter {
  searchTerm?: string;
}
