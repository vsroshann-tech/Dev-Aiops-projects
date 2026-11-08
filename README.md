Markdown
# 🤖 Docs Agent — Roshan Observability Platform Assistant

An intelligent, RAG-powered AI documentation assistant built using **CrewAI** and **Ollama (`llama3.2`)**. This agent is designed to read, analyze, and answer technical queries regarding the **Roshan Observability Platform** using internal engineering documentation.

---

## 🚀 Overview

The `docs-agent` project bridges the gap between complex infrastructure documentation and user queries. Instead of searching through dense 30-page PDF manuals manually, developers and operations engineers can query the AI agent to retrieve precise insights on metrics, logs, traces, architectures, and troubleshooting runbooks.

---

## 📊 About the Roshan Observability Platform

The documentation embedded within this project (`Roshan_Platform_Documentation.pdf`) details the **Roshan Observability Platform**, a next-generation telemetry stack designed to outperform traditional monitoring tools like Prometheus and Datadog.

* **Core Engine:** Utilizes **VictoriaMetrics** as its backend storage engine instead of a traditional time-series database, delivering superior ingestion performance, lower memory footprint, and lightning-fast query execution.
* **Telemetry Coverage:** Comprehensive coverage of **Metrics**, **Distributed Traces**, and **Structured Logs**.
* **Comparisons:** Detailed side-by-side architectural and performance benchmarks comparing Roshan against Prometheus, Loki, and Datadog.
* **Operations:** Includes full configuration schemas, API references, eBPF telemetry pipelines, and incident response runbooks.

---

## 📁 Project Structure

```text
docs-agent/
├── Roshan_Platform_Documentation.pdf  # Comprehensive internal documentation (Knowledge Base)
├── crew.jsonc                         # Main CrewAI crew definition, tasks, and runtime inputs
├── agents/                            # AI agent definitions (e.g., Roshan tool expert)
├── knowledge/                         # Embedded knowledge source configurations
├── tools/                             # Custom tool definitions (Python)
├── pyproject.toml                     # Python project dependencies and build system
└── README.md                          # Project documentation
🛠️ Tech Stack & Requirements
Orchestration: CrewAI (v1.15+)

LLM Engine: Local Ollama running llama3.2:latest

Knowledge Retrieval (RAG): Built-in CrewAI PDF Knowledge Source with vector embeddings

Environment: Python 3.12+ / Linux (WSL2) / Virtual Environment (.venv)

⚙️ Installation & Setup
Clone the Repository & Navigate to the Project:

Bash
cd ~/Dev-Aiops-projects/docs-agent
Activate the Virtual Environment:

Bash
source .venv/bin/activate
Verify Dependencies:
Ensure all packages (including crewai, pdfplumber, and chromadb) are installed:

Bash
pip install -e .
🏃‍♂️ Running the Agent
You can execute the documentation agent using the CrewAI CLI:

Bash
crewai run
Configuring Queries
By default, runtime inputs are defined in crew.jsonc under the inputs block. You can update your query directly in crew.jsonc:

JSON
"inputs": {
  "question": "What is the Roshan platform, and why is it better than Prometheus by using VictoriaMetrics?"
}
👤 Author
Roshan (vsroshann-tech) — DevOps & AIOps Engineer

GitHub Repository: vsroshann-tech/Dev-Aiops-projects


---

### Saving and Pushing to GitHub

1. Save and exit nano by pressing `Ctrl + O`, hitting `Enter`, then pressing `Ctrl + X`.
2. Push the new `README.md` to your GitHub repository:
   ```bash
   git add README.md
   git commit -m "Add comprehensive README for docs-agent"
   git push origin main
