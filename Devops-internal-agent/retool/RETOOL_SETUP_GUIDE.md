# Retool Integration Guide — Internal DevOps AI Agent

This guide shows how to connect **Retool** (Self-Hosted on Ubuntu/VPC for secret company docs, or Retool Cloud + VPC tunnel for POC testing) to the DevOps AI Agent API (`http://localhost:8005` / `http://host.docker.internal:8005`).

---

## Security Recommendation for Secret Company Documentation
- **Do NOT send secret internal runbooks to public LLM endpoints or unencrypted tunnels.**
- Run **Self-Hosted Retool** inside your Ubuntu/AWS private VPC (`retool/docker-compose.retool.yml`) alongside:
  - This FastAPI Agent (`app/main.py`)
  - Local **Ollama** (`llama3.1:8b` + `nomic-embed-text`) or private AWS Bedrock VPC endpoint
  - Read-only Postgres & MySQL credentials (`devops_ro`)

---

## Step 1: Create the REST API Resource in Retool
1. In Retool, go to **Resources → Create new → REST API**.
2. Name it: `DevOps_AI_Agent_API`.
3. **Base URL**:
   - If Retool runs in Docker on the same Ubuntu host: `http://host.docker.internal:8005`
   - If testing from Retool Cloud via ngrok/Cloudflare Tunnel: your HTTPS tunnel URL.
4. **Headers**:
   - `X-API-Key`: `dev-retool-poc-key-change-me` (match `API_KEYS` in `.env`)
   - `X-User-Email`: `{{ current_user.email }}`
   - `X-User-Role`: `devops`
   - `X-Client-Source`: `retool`

---

## Step 2: Wire the 5 Retool Queries

### 1. `chatAgent` (POST `/api/v1/chat`)
- **Body (JSON)**:
  ```json
  {
    "message": "{{ chat1.lastMessage }}",
    "history": "{{ chat1.messageHistory.slice(-6).map(m => ({ role: m.sender === 'user' ? 'user' : 'assistant', content: m.value })) }}"
  }
  ```
- Bind the **Retool Chat component**:
  - Trigger `chatAgent` on **Submit** (`onSubmit`).
  - Display `{{ chatAgent.data.reply }}` as the assistant response.
  - Show citations & MCP tools in an expandable Markdown/JSON panel using `{{ chatAgent.data.citations }}` and `{{ chatAgent.data.mcp_results }}`.

### 2. `uploadInternalDocs` (POST `/api/v1/docs/upload-batch-base64`)
- Add a **File Dropzone** (`fileDropzone1`) with **Selection type = Multiple** (accepting `.pdf, .md, .txt, .yaml, .json`) and a **Select** (`classificationSelect`: `internal`, `confidential`, `public-poc`).
- **Body (JSON)**:
  ```json
  {
    "files": "{{ fileDropzone1.value.map(f => ({ filename: f.name, content_base64: f.base64Data })) }}",
    "classification": "{{ classificationSelect.value }}"
  }
  ```
- Single-file fallback endpoint is also available at `POST /api/v1/docs/upload-base64`.
- On Success: trigger `listDocs.trigger()`.

### 3. `listDocs` (GET `/api/v1/docs`)
- Bind to a **Table** (`docsTable`) with `{{ listDocs.data.documents }}`.

### 4. `listSkillsAndTools` (GET `/api/v1/skills` & `/api/v1/mcp/tools`)
- Display active DevOps Skills and registered MCP tools (`github-actions-mcp`, `aws-infra-mcp`, `db-diagnostics-mcp`) in a sidebar tab.

### 5. `getAuditLogs` (GET `/api/v1/audit?limit=50`)
- Bind to a **Compliance Audit Table** (`auditTable`) with `{{ getAuditLogs.data.events }}` for SOC2/ISO27001 visibility.
