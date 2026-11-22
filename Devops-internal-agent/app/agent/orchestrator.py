"""End-to-end DevOps AI Agent orchestrator combining Security, Skills, RAG, and MCP."""

import json
import re
import time
from typing import Any, Dict, List, Optional

from app.agent.llm import generate_answer
from app.config import settings
from app.mcp.client import MCPClientManager
from app.rag.embeddings import expand_query_tokens, tokenize
from app.rag.vector_store import LocalVectorStore, RetrievedChunk
from app.security.audit import AuditLogger
from app.security.auth import CallerIdentity
from app.security.redactor import redact_secrets
from app.skills.loader import AgentSkill, SkillRegistry


class DevOpsAgentOrchestrator:
    """Coordinates security redaction, skill selection, RAG retrieval, MCP tools, and auditing."""

    def __init__(
        self,
        vector_store: LocalVectorStore,
        skill_registry: SkillRegistry,
        mcp_client: MCPClientManager,
        audit_logger: AuditLogger,
    ) -> None:
        self.vector_store = vector_store
        self.skill_registry = skill_registry
        self.mcp_client = mcp_client
        self.audit_logger = audit_logger

    def handle_chat(
        self,
        message: str,
        caller: CallerIdentity,
        history: Optional[List[Dict[str, str]]] = None,
        classification_filter: Optional[str] = None,
        explicit_sql: Optional[str] = None,
        db_engine: str = "postgres",
    ) -> Dict[str, Any]:
        """Process a user troubleshooting request with full compliance logging."""
        started = time.perf_counter()

        # 1. Secret redaction on incoming prompt
        clean_prompt, prompt_redactions = (
            redact_secrets(message) if settings.enable_secret_redaction else (message, {})
        )

        # Handle meta / capability questions directly without unrelated RAG/MCP noise
        if self._is_capability_query(clean_prompt):
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            cap_reply = self._build_capability_reply()
            self.audit_logger.log_event(
                event_type="agent_chat",
                user_email=caller.user_email,
                role=caller.role,
                source=caller.source,
                details={
                    "prompt_redacted": clean_prompt,
                    "redactions": prompt_redactions,
                    "skills_matched": [],
                    "rag_sources": [],
                    "mcp_tools_called": [],
                    "llm_provider": "capability_router",
                    "latency_ms": latency_ms,
                },
            )
            return {
                "reply": cap_reply,
                "provider": "capability_router",
                "poc_mode": settings.poc_mode,
                "skills_used": [],
                "citations": [],
                "mcp_results": [],
                "security": {
                    "redactions_applied": prompt_redactions,
                    "read_only_db_enforced": settings.read_only_db_enforced,
                    "allow_ci_mutations": settings.allow_ci_mutations,
                },
                "latency_ms": latency_ms,
            }

        # 2. Match relevant Skills
        matched_skills: List[AgentSkill] = self.skill_registry.match(clean_prompt, max_skills=2)

        # 3. Retrieve internal documentation via RAG
        rag_hits: List[RetrievedChunk] = self.vector_store.search(
            clean_prompt,
            top_k=settings.max_rag_results,
            classification=classification_filter,
        )

        # 4. Select & execute MCP tools based on intent, matched skills, or explicit SQL
        tool_invocations = self._select_and_run_mcp_tools(
            clean_prompt, matched_skills, explicit_sql=explicit_sql, db_engine=db_engine
        )

        # 5. Build grounded context bundle
        context_bundle = self._format_context(matched_skills, rag_hits, tool_invocations)

        # 6. Generate response (Ollama / Private OpenAI-compatible / Local POC Synthesizer)
        llm_reply, provider_used = generate_answer(clean_prompt, context_bundle, history=history)
        if not llm_reply:
            llm_reply = self._synthesize_poc_response(
                clean_prompt, matched_skills, rag_hits, tool_invocations, prompt_redactions
            )

        # Redact any accidental secret in final response
        safe_reply, output_redactions = (
            redact_secrets(llm_reply) if settings.enable_secret_redaction else (llm_reply, {})
        )
        all_redactions = {**prompt_redactions}
        for k, v in output_redactions.items():
            all_redactions[k] = all_redactions.get(k, 0) + v

        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        # 7. Append immutable compliance audit log
        self.audit_logger.log_event(
            event_type="agent_chat",
            user_email=caller.user_email,
            role=caller.role,
            source=caller.source,
            details={
                "prompt_redacted": clean_prompt,
                "redactions": all_redactions,
                "skills_matched": [s.name for s in matched_skills],
                "rag_sources": [
                    {"filename": h.filename, "heading": h.heading, "score": h.score}
                    for h in rag_hits
                ],
                "mcp_tools_called": [t["tool"] for t in tool_invocations],
                "llm_provider": provider_used,
                "latency_ms": latency_ms,
            },
        )

        return {
            "reply": safe_reply,
            "provider": provider_used,
            "poc_mode": settings.poc_mode,
            "skills_used": [
                {"name": s.name, "description": s.description} for s in matched_skills
            ],
            "citations": [
                {
                    "filename": h.filename,
                    "heading": h.heading,
                    "classification": h.classification,
                    "score": h.score,
                    "excerpt": h.content[:220] + ("..." if len(h.content) > 220 else ""),
                }
                for h in rag_hits
            ],
            "mcp_results": tool_invocations,
            "security": {
                "redactions_applied": all_redactions,
                "read_only_db_enforced": settings.read_only_db_enforced,
                "allow_ci_mutations": settings.allow_ci_mutations,
            },
            "latency_ms": latency_ms,
        }

    def _is_capability_query(self, prompt: str) -> bool:
        q = prompt.lower().strip(" ?!.")
        patterns = (
            "what can you do",
            "what you can do",
            "how can you help",
            "who are you",
            "help",
            "capabilities",
            "what tools do you have",
        )
        return q in patterns or any(q.startswith(p) for p in ("what can you do", "what you can do", "how can you help"))

    def _build_capability_reply(self) -> str:
        docs = self.vector_store.list_documents()
        doc_names = ", ".join(f"`{d['filename']}`" for d in docs) if docs else "None uploaded yet"
        skill_lines = "\n".join(
            f"- **`{s.name}`**: {s.description}" for s in self.skill_registry.skills
        )
        tool_names = ", ".join(f"`{t['name']}`" for t in self.mcp_client.list_tools())
        return (
            "### Internal DevOps AI Agent — Capabilities\n"
            "I am your security-compliant DevOps troubleshooting assistant connected to **RAG**, **MCP Tools**, and **Skills**:\n\n"
            "1. **Internal Documentation Q&A (RAG):** Answer architecture, server, CI/CD, and project-mapping questions from your uploaded PDFs/Markdown runbooks.\n"
            "2. **GitHub Actions Triage:** Inspect failing workflow runs and job logs (`deploy-eks-prod`, `db-migration-verify`, etc.).\n"
            "3. **AWS Infrastructure Diagnostics:** Check EC2/EKS compute health, ALB 502/504 errors, CloudWatch error logs, RDS status, and security compliance posture.\n"
            "4. **PostgreSQL & MySQL Read-Only Diagnostics:** Inspect active connections, lock contention, slow queries, and validate read-only `SELECT`/`EXPLAIN`/`SHOW` SQL (mutations like `DROP`/`DELETE` are strictly blocked).\n"
            "5. **Compliance Guardrails:** Automatically redact AWS keys, GitHub tokens, JWTs, and DB passwords before inference and log every action to `audit_logs.jsonl`.\n\n"
            f"**Currently Indexed Docs ({len(docs)}):** {doc_names}\n\n"
            f"**Loaded Skills:**\n{skill_lines}\n\n"
            f"**Available MCP Tools:** {tool_names}"
        )

    def _select_and_run_mcp_tools(
        self,
        prompt: str,
        matched_skills: List[AgentSkill],
        explicit_sql: Optional[str] = None,
        db_engine: str = "postgres",
    ) -> List[Dict[str, Any]]:
        q = prompt.lower()
        planned_calls: List[tuple[str, Dict[str, Any]]] = []

        if explicit_sql:
            planned_calls.append(("db_run_readonly_sql", {"engine": db_engine, "sql": explicit_sql}))

        # Detect inline SQL snippet in prompt for direct guardrail verification
        sql_match = re.search(
            r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|SHOW|EXPLAIN)\b[\s\S]{3,160}",
            prompt,
            re.IGNORECASE,
        )
        if sql_match and not explicit_sql and any(w in q for w in ("query", "sql", "run ", "execute")):
            engine = "mysql" if "mysql" in q else "postgres"
            planned_calls.append(("db_run_readonly_sql", {"engine": engine, "sql": sql_match.group(0)}))

        # Only invoke live/mock MCP telemetry tools when troubleshooting/checking/auditing,
        # not for pure documentation lookup questions (e.g., "how many servers are there").
        diagnostic_intent = bool(explicit_sql or sql_match) or any(
            k in q
            for k in (
                "check", "fail", "failed", "failing", "error", "502", "504",
                "cloudwatch", "logs", "lock", "slow", "connection", "audit",
                "scan", "health", "status", "troubleshoot", "debug", "diagnose",
                "oom", "timeout", "exhausted", "lag", "unhealthy",
            )
        )
        if not diagnostic_intent:
            return []

        skill_names = {s.name for s in matched_skills}

        if "github-actions-triage" in skill_names or any(
            re.search(rf"(?<![a-z0-9_]){re.escape(k)}(?![a-z0-9_])", q)
            for k in ("github", "workflow", "ci/cd", "pipeline", "actions")
        ):
            planned_calls.append(("github_list_workflow_runs", {}))
            run_id_match = re.search(r"\b(984\d{4}|\d{6,})\b", prompt)
            run_id = run_id_match.group(1) if run_id_match else "9842101"
            planned_calls.append(("github_get_failed_job_logs", {"run_id": run_id}))

        if "aws-infra-troubleshooting" in skill_names or any(
            re.search(rf"(?<![a-z0-9_]){re.escape(k)}(?![a-z0-9_])", q)
            for k in ("aws", "ec2", "eks", "alb", "502", "504", "cloudwatch", "rds")
        ):
            # Only fetch compute/CloudWatch when infra/logs/errors are relevant
            if any(k in q for k in ("ec2", "eks", "alb", "502", "504", "compute", "cloudwatch", "server", "aws")) and not (
                any(k in q for k in ("mysql", "postgres", "postgresql", "lock", "connection"))
                and not any(k in q for k in ("502", "504", "eks", "ec2", "alb", "cloudwatch", "compute"))
            ):
                planned_calls.append(("aws_list_compute_health", {"environment": "prod"}))
                planned_calls.append(("aws_query_cloudwatch_logs", {"filter_pattern": "ERROR"}))
            if any(k in q for k in ("rds", "database", "mysql", "postgres", "postgresql")):
                planned_calls.append(("aws_check_rds_status", {}))

        if (
            "database-health-check" in skill_names
            or any(k in q for k in ("postgres", "postgresql", "mysql", "lock", "slow query", "connection"))
        ) and not (sql_match and any(k in q for k in ("drop ", "delete ", "insert ", "update ", "alter "))):
            if "mysql" in q:
                planned_calls.append(("db_get_active_connections", {"engine": "mysql"}))
                planned_calls.append(("db_get_slow_queries", {"engine": "mysql"}))
            if "postgres" in q or "postgresql" in q or "mysql" not in q:
                planned_calls.append(("db_get_active_connections", {"engine": "postgres"}))
                planned_calls.append(("db_get_slow_queries", {"engine": "postgres"}))

        if "security-compliance-audit" in skill_names or any(
            k in q for k in ("compliance", "security", "audit", "soc2", "iso27001", "iam", "public")
        ):
            planned_calls.append(("aws_compliance_scan", {}))

        # Deduplicate preserving order
        seen = set()
        results: List[Dict[str, Any]] = []
        for tool_name, args in planned_calls:
            sig = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
            if sig in seen:
                continue
            seen.add(sig)
            output = self.mcp_client.call_tool(tool_name, args)
            results.append({"tool": tool_name, "arguments": args, "output": output})

        return results

    def _format_context(
        self,
        skills: List[AgentSkill],
        rag_hits: List[RetrievedChunk],
        tool_invocations: List[Dict[str, Any]],
    ) -> str:
        parts = []
        if skills:
            parts.append("### Active Skills")
            for s in skills:
                parts.append(f"- **{s.name}**: {s.description}\n{s.instructions[:700]}")

        if rag_hits:
            parts.append("### Internal Documentation (RAG)")
            for h in rag_hits:
                parts.append(
                    f"- [{h.filename} > {h.heading}] (score={h.score}, class={h.classification}):\n{h.content}"
                )

        if tool_invocations:
            parts.append("### MCP Tool Telemetry")
            for inv in tool_invocations:
                parts.append(f"- Tool `{inv['tool']}`:\n{json.dumps(inv['output'], indent=2)}")

        return "\n\n".join(parts)

    def _synthesize_poc_response(
        self,
        prompt: str,
        skills: List[AgentSkill],
        rag_hits: List[RetrievedChunk],
        tool_invocations: List[Dict[str, Any]],
        redactions: Dict[str, int],
    ) -> str:
        lines: List[str] = []
        lines.append(f"### DevOps Troubleshooting Analysis (`{settings.poc_mode.upper()}` POC Mode)")

        if redactions:
            redacted_summary = ", ".join(f"{k} ({v})" for k, v in redactions.items())
            lines.append(
                f"> **Security Guardrail:** Redacted sensitive tokens from input before processing: `{redacted_summary}`."
            )

        if skills:
            lines.append(
                "**Active Skills:** " + ", ".join(f"`{s.name}`" for s in skills)
            )

        if tool_invocations:
            lines.append("\n**Live / Simulated MCP Diagnostics:**")
            for inv in tool_invocations:
                out = inv["output"]
                tool = inv["tool"]
                if out.get("blocked"):
                    lines.append(f"- `{tool}`: **BLOCKED** — {out.get('reason')}")
                elif tool == "github_list_workflow_runs":
                    failures = [r for r in out.get("runs", []) if r.get("conclusion") == "failure"]
                    lines.append(
                        f"- `{tool}` ({out.get('repo')}): Found **{len(failures)} failing workflow(s)** "
                        + (
                            "("
                            + ", ".join(f"`{f['name']}` run `{f['id']}`: {f.get('failed_step')}" for f in failures)
                            + ")"
                            if failures
                            else "(all recent runs succeeded)"
                        )
                    )
                elif tool == "github_get_failed_job_logs":
                    excerpt = out.get("log_excerpt") or json.dumps(out.get("failed_jobs", []))
                    lines.append(f"- `{tool}` (run `{out.get('run_id')}`):\n  ```\n  {excerpt}\n  ```")
                elif tool == "aws_list_compute_health":
                    alb = out.get("alb_target_health", {})
                    alerts = [i for i in out.get("ec2_instances", []) if i.get("alert")]
                    msg = f"- `{tool}`: "
                    if alerts:
                        msg += "; ".join(f"`{a['name']}` ({a['alert']})" for a in alerts)
                    else:
                        msg += f"{len(out.get('ec2_instances', out.get('instances', [])))} compute nodes checked"
                    if alb:
                        msg += f" | ALB `{alb.get('target_group')}` unhealthy={alb.get('unhealthy_targets')} ({alb.get('last_error')})"
                    lines.append(msg)
                elif tool == "aws_query_cloudwatch_logs":
                    events = out.get("events", [])
                    lines.append(
                        f"- `{tool}` (`{out.get('log_group')}`): {len(events)} matching event(s)"
                        + (f" — latest: `{events[0]}`" if events else "")
                    )
                elif tool == "aws_check_rds_status":
                    for db in out.get("instances", [out]):
                        lines.append(
                            f"- `{tool}` (`{db.get('id')}` / {db.get('engine')}): status={db.get('status')}, "
                            f"encrypted={db.get('storage_encrypted')}, conn_pct={db.get('connections_used_pct', 'n/a')}%"
                            + (f" — **{db['alert']}**" if db.get("alert") else "")
                        )
                elif tool == "aws_compliance_scan":
                    findings = out.get("findings", [])
                    lines.append(
                        f"- `{tool}`: {len(findings)} compliance finding(s) — "
                        + "; ".join(f"[{f['severity']}] {f['resource']}: {f['issue']}" for f in findings)
                    )
                elif tool == "db_get_active_connections":
                    eng = out.get("engine")
                    if eng == "postgres":
                        blocked = out.get("blocked_sessions", [])
                        lines.append(
                            f"- `{tool}` (postgres): {out.get('active_connections')}/{out.get('max_connections')} active; "
                            f"{len(blocked)} blocked/long-running session(s)."
                        )
                    else:
                        lines.append(
                            f"- `{tool}` (mysql): {out.get('threads_connected')}/{out.get('max_connections')} connections. "
                            + (out.get("warning") or "")
                        )
                elif tool == "db_get_slow_queries":
                    sq = out.get("slow_queries", [])
                    if sq:
                        lines.append(
                            f"- `{tool}` ({out.get('engine')}): Top slow query (`{sq[0].get('query')}`) — {sq[0].get('recommendation')}"
                        )
                else:
                    lines.append(f"- `{tool}`: `{json.dumps(out)[:240]}`")

        if rag_hits:
            lines.append("\n**Answer from Internal Documentation (RAG):**")
            top_excerpts = self._extract_relevant_lines(prompt, rag_hits[0].content, max_lines=10)
            if top_excerpts:
                lines.append(f"From **{rag_hits[0].filename} › {rag_hits[0].heading}**:")
                lines.append(top_excerpts)

            lines.append("\n**Supporting Citations:**")
            for hit in rag_hits[:3]:
                focused = self._extract_relevant_lines(prompt, hit.content, max_lines=3)
                lines.append(f"- **{hit.filename} › {hit.heading}** (score `{hit.score}`):\n  {focused}")
        else:
            lines.append(
                "\n**Internal Docs (RAG):** No internal docs matched yet. Upload runbooks via `/api/v1/docs/upload` or run `scripts/setup_ubuntu.sh` to load `sample_docs/`."
            )

        lines.append(
            "\n> *Note: Running in `LLM_PROVIDER=local_fallback`. Enable local Ollama (`LLM_PROVIDER=ollama`) for full conversational synthesis.*"
        )
        return "\n".join(lines)

    def _extract_relevant_lines(self, prompt: str, content: str, max_lines: int = 8) -> str:
        """Select the lines/sentences in a chunk most relevant to the user's question."""
        q_tokens = set(expand_query_tokens(prompt))
        raw_lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        if not raw_lines:
            return content[:400]

        scored_lines = []
        for idx, ln in enumerate(raw_lines):
            ln_tokens = set(tokenize(ln, remove_stopwords=True))
            overlap = len(q_tokens & ln_tokens)
            # Prefer lines containing specific resource/project identifiers or query hits
            scored_lines.append((overlap, -idx, idx, ln))

        # Keep top scoring lines, then restore original reading order
        top = sorted(scored_lines, key=lambda x: (x[0], x[1]), reverse=True)[:max_lines]
        if all(item[0] == 0 for item in top):
            return "\n".join(raw_lines[:max_lines])
        ordered = sorted(top, key=lambda x: x[2])
        return "\n".join(item[3] for item in ordered)
