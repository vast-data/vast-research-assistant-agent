# Deploy K8s Application (Backend/Frontend)

Deploy the Research Assistant web application to Kubernetes.

## Prerequisites

- **Kubernetes access:**
  - `kubectl` installed and configured for your cluster
  - Ability to create namespaces and deploy resources

- **VAST cluster access:**
  - Cluster name (e.g., `v151`) - used as part of the URL
  - InsightEngine backend already deployed (see [insight-engine-pipeline](../insight-engine-pipeline/README.md))

- **AI/ML services (one of the following):**
  - NVIDIA NIM endpoint or API key (for local GPU or NVIDIA cloud)
  - Google AI Studio API key (for Gemini)
  - Google Cloud service account with Vertex AI User role (for Vertex AI)

- **Network access:**
  - Ability to modify `/etc/hosts` on your local machine

---

## Step 1: Configure Secrets

Edit `.env` with your credentials (or use `.env.template` as a starting point):

| Section | Key Settings |
|---------|--------------|
| **VastDB** | `VASTDB_ENDPOINT`, `VASTDB_ACCESS_KEY`, `VASTDB_SECRET_KEY`; optional `VASTDB_BUCKET` / `VASTDB_SCHEMA` (override derived `<ns>-bucket` / `<ns>-schema`) |
| **RAG** | `RAG_BASE_URL` (InsightEngine backend) |
| **LLM Provider** | `MODEL_PROVIDER` (`nvidia`, `gemini`, or `vertex`) |
| **NVIDIA** | `AGENT_OPENAI_API_KEY`, `AGENT_OPENAI_BASE_URL`, `AGENT_OPENAI_MODEL_ID` |
| **Gemini** | `GOOGLE_API_KEY`, `GEMINI_MODEL_ID` |
| **Vertex AI** | `VERTEX_MODEL_ID`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` (see below) |
| **Azure AD** | `AUTH__AZURE__*` keys (optional, for SSO) |

---

## Step 2: Generate Kubernetes Manifests

```bash
# From deployments/k8s-application/
./generate-from-env.sh -e .env

# This creates (in the same directory):
#   - configmap.yaml (non-sensitive config)
#   - secret.yaml (sensitive credentials, base64 encoded)
```

---

## Step 3: Deploy

```bash
./install.sh \
  -n research-assistant \
  -r <YOUR_REGISTRY> \
  -t <YOUR_TAG> \
  -s secret.yaml \
  -c <YOUR_CLUSTER_NAME> \
  -g <YOUR_GUI_HOSTNAME> \
  --tls-secret <YOUR_TLS_SECRET> \
  -i
```

**Example:**
```bash
./install.sh \
  -n research-assistant \
  -r docker.io/vastdatasolutions \
  -t v2 \
  -s secret.yaml \
  -c v151 \
  -g vast-research151.vastdata.com \
  --tls-secret research-assistant-wildcard \
  -i
```

**What gets deployed:**
- Backend Service (FastAPI) - Agent API
- Frontend Service (Angular) - Chat UI
- Ingress Resources - External access

---

## Step 4: Wait for Pods

```bash
kubectl get pods -n research-assistant -w
```

Expected:
```
NAME                                      READY   STATUS    RESTARTS   AGE
backend-collections-ui-xxx                1/1     Running   0          8d
backend-webserver-xxx                     1/1     Running   0          8d
research-assistant-xxx                    1/1     Running   0          5m
research-assistant-gui-xxx                1/1     Running   0          5m
```

---

## Step 5: Configure DNS

Get ingress IP:
```bash
kubectl get ingress -n research-assistant
```

Add to `/etc/hosts`:
```
<INGRESS_IP> agent.<cluster_name> vast-research<cluster_name>.vastdata.com
```

---

## Step 6: Access UI

```
https://<YOUR_GUI_HOSTNAME>
```

---

## LLM Provider Setup

The application supports three LLM providers, controlled by the `MODEL_PROVIDER` setting in the ConfigMap.

### NVIDIA NIM (default)

Works with local GPU (vLLM/NIM) or NVIDIA cloud. Set in `.env`:
```
MODEL_PROVIDER=nvidia
AGENT_OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1
AGENT_OPENAI_MODEL_ID=meta/llama-3.3-70b-instruct
AGENT_OPENAI_API_KEY=<your-nvidia-api-key>
```

### Google AI Studio (Gemini)

Uses a Google AI Studio API key. Set in `.env`:
```
MODEL_PROVIDER=gemini
GEMINI_MODEL_ID=gemini-2.5-pro
GOOGLE_API_KEY=<your-google-ai-studio-key>
```

### Google Vertex AI

Uses a GCP service account for authentication instead of an API key.

**Prerequisites:**
1. A GCP project with the **Vertex AI API** enabled
2. A GCP service account with the **Vertex AI User** role (or equivalent)
3. A service account JSON key file exported for that account

**Step 1:** Set the provider in `.env`:
```
MODEL_PROVIDER=vertex
VERTEX_MODEL_ID=gemini-2.5-pro
GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>
GOOGLE_CLOUD_LOCATION=us-central1
```

**Step 2:** Create a Kubernetes secret from the service account JSON file:
```bash
kubectl -n <namespace> create secret generic vertex-sa-credentials \
  --from-file=credentials.json=</path/to/your-service-account.json>
```

**Step 3:** Patch the deployment to mount the credentials and set `GOOGLE_APPLICATION_CREDENTIALS`:
```bash
kubectl -n <namespace> patch deployment research-assistant --type='strategic' -p='{
  "spec": {
    "template": {
      "spec": {
        "volumes": [
          {"name": "vertex-sa", "secret": {"secretName": "vertex-sa-credentials"}}
        ],
        "containers": [{
          "name": "research-assistant",
          "env": [
            {"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/etc/vertex/credentials.json"}
          ],
          "volumeMounts": [
            {"name": "vertex-sa", "mountPath": "/etc/vertex", "readOnly": true}
          ]
        }]
      }
    }
  }
}'
```

> **Note:** The Vertex AI secret and volume mount are only required when using `MODEL_PROVIDER=vertex`. Other providers do not need them.

---

## Troubleshooting

### Pods not starting
```bash
kubectl describe pod -l app=research-assistant -n research-assistant
kubectl get events -n research-assistant --sort-by='.lastTimestamp'
```

### View Logs
```bash
# Backend
kubectl logs -f deployment/research-assistant -n research-assistant

# Frontend
kubectl logs -f deployment/research-assistant-gui -n research-assistant
```

### Uninstall
```bash
./install.sh -n research-assistant -u
```
