# VAST DataEngine - Research Assistant Blueprint Foundation Stack

An Enterprise RAG-Powered Research Assistant system powered by VAST DataEngine, with flexible LLM provider support (NVIDIA NIM, Google AI Studio, Google Vertex AI).

This Blueprint was created to showcase the end-to-end utilization of the full VAST AI OS production-grade capabilities, including an Agentic Chat Interface, Serverless Event-based Document Ingestion, and VastDB as the unified Vector-Store for both document storage and conversation persistence.

## Overview

The system has two main parts:
1. **K8s Application** - Chat UI and Agent API (Kubernetes)
2. **InsightEngine Pipeline** - Serverless document ingestion (VAST DataEngine)

![Architecture Diagram](docs/architecture.png)

---

## Deployment

| Component | Guide |
|-----------|-------|
| **K8s Application** (Backend Agent, Frontend GUI) | [k8s-application](deployments/k8s-application/README.md) |
| **InsightEngine Pipeline** (Document Ingestion) | [insight-engine-pipeline](deployments/insight-engine-pipeline/README.md) |



---

## Key Features

| Feature | Description | Documentation |
|---------|-------------|---------------|
| **RAG Q&A** | Retrieval-augmented generation on document collections | [backend](source-code/backend/README.md) |
| **Streaming Responses** | Real-time token streaming with tool execution visibility | [backend](source-code/backend/README.md) |
| **Document Upload** | Single/multi-file and directory upload via GUI | [gui](source-code/gui/README.md) |
| **Collection Management** | Create and manage document collections | [gui](source-code/gui/README.md) |
| **Internet Search** | Optional DuckDuckGo web search augmentation | [backend](source-code/backend/README.md) |
| **Flexible LLM** | NVIDIA NIM, Google AI Studio (Gemini), or Google Vertex AI | [backend](source-code/backend/README.md) |
| **Authentication** | JWT auth with optional Azure AD SSO | [k8s-application](deployments/k8s-application/README.md) |

---

## Component Documentation

| Component | Description |
|-----------|-------------|
| [backend](source-code/backend/README.md) | FastAPI agent service with RAG, streaming, session management |
| [gui](source-code/gui/README.md) | Angular chat UI with collection selector, citations, tool blocks |

---

## LLM Provider Configuration

The application supports three LLM providers, controlled by the `MODEL_PROVIDER` environment variable:

| Provider | Value | Description |
|----------|-------|-------------|
| **NVIDIA NIM** | `nvidia` (default) | OpenAI-compatible API — local GPU (vLLM/NIM) or NVIDIA cloud |
| **Google AI Studio** | `gemini` | Google's Gemini API using an API key |
| **Google Vertex AI** | `vertex` | Google Cloud Vertex AI using service account credentials |

### NVIDIA NIM (default)

Set in the ConfigMap:
```
MODEL_PROVIDER=nvidia
AGENT_OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1   # or your local NIM endpoint
AGENT_OPENAI_MODEL_ID=meta/llama-3.3-70b-instruct
```
Set `AGENT_OPENAI_API_KEY` in the secrets.

### Google AI Studio (Gemini)

Set in the ConfigMap:
```
MODEL_PROVIDER=gemini
GEMINI_MODEL_ID=gemini-2.5-pro
```
Set `GOOGLE_API_KEY` in the secrets.

### Google Vertex AI

**Prerequisites:**
1. A GCP project with the Vertex AI API enabled
2. A GCP service account with the `Vertex AI User` role (or equivalent)
3. A service account JSON key file for that account

**Setup steps:**

1. Create the Kubernetes secret from your service account JSON file:
   ```bash
   kubectl -n <namespace> create secret generic vertex-sa-credentials \
     --from-file=credentials.json=</path/to/your-service-account.json>
   ```

2. Patch the deployment to mount the credentials:
   ```bash
   kubectl -n <namespace> patch deployment research-assistant --type='strategic' -p='{
     "spec": {
       "template": {
         "spec": {
           "volumes": [{"name": "vertex-sa", "secret": {"secretName": "vertex-sa-credentials"}}],
           "containers": [{
             "name": "research-assistant",
             "env": [{"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/etc/vertex/credentials.json"}],
             "volumeMounts": [{"name": "vertex-sa", "mountPath": "/etc/vertex", "readOnly": true}]
           }]
         }
       }
     }
   }'
   ```

3. Set the ConfigMap values:
   ```
   MODEL_PROVIDER=vertex
   VERTEX_MODEL_ID=gemini-2.5-pro
   GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>
   GOOGLE_CLOUD_LOCATION=us-central1
   ```

> **Note:** The Vertex AI secret and volume mount are only required when using `MODEL_PROVIDER=vertex`. Other providers do not need them.

---

## Pipeline Flow

```
Upload Document → S3 bucket
                      ↓
              InsightEngine Ingest
              (chunk, embed, store)
                      ↓
                 VastDB (vectors)
                      ↓
               Search Ready (RAG)
                      ↓
          Agent retrieves & answers
```

---

## Need Help?

- **K8s Deployment**: See [K8s Application Guide](deployments/k8s-application/README.md)
- **InsightEngine**: See [Pipeline Guide](deployments/insight-engine-pipeline/README.md)
- **Community**: [VAST Community Forums](https://community.vastdata.com/)
