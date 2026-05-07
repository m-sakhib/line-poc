# Data Lineage Tracker — Implementation Plan

## Goal

A Python CLI app that uses `github-copilot-sdk` with Azure Foundry BYOK to analyze any given repository and extract data lineage records into a structured, configurable JSON output.

## Key Decisions (Answered)

| Decision | Answer |
|----------|--------|
| Azure model | **gpt5.1** (provisioned and ready) |
| Repo size | **Large (500+ files)** |
| Frameworks | **Framework-agnostic** — the LLM handles any framework; AST skills are per-language, not per-framework |
| Cross-file lineage | **Critical** — must trace full call chains (Controller → Service → DAO → DB) |
| Output consumer | **JSON file** for now (future: Purview/Atlas integration possible) |
| Auth | **BYOK only via Azure Foundry** — no GitHub token needed; all LLM calls route to Azure OpenAI |
| Chunking | **AST-first flow discovery** — AST identifies data flows, agent receives only relevant code snippets (not whole files) |

---

## Architecture

```
lineage_poc/
├── main.py                        # CLI entry point
├── config/
│   ├── settings.py                # App config (Azure creds, model, paths)
│   └── lineage_schema.yaml        # Configurable lineage output fields
├── client/
│   ├── session.py                 # CopilotClient + Azure BYOK session factory
│   └── events.py                  # Event collector (captures agent responses)
├── tools/
│   ├── registry.py                # Discovers & registers tools from skills/
│   ├── ast_tool.py                # Generic AST-query tool exposed to the agent
│   └── lineage_output.py          # Tool the agent calls to emit lineage records
├── skills/
│   ├── _base/
│   │   └── SKILL.md               # Shared lineage instructions + output schema
│   ├── python/
│   │   ├── SKILL.md               # Python-specific lineage guidance
│   │   └── ast_parser.py          # Uses stdlib `ast` module
│   └── java/
│       ├── SKILL.md               # Java-specific lineage guidance
│       └── ast_parser.py          # Uses `javalang` (or tree-sitter-java)
├── schema/
│   └── lineage_record.py          # Pydantic model for lineage output
├── requirements.txt
└── README.md
```

---

## Phase 1 — Project Bootstrap & Config

| # | Task | Details |
|---|------|---------|
| 1.1 | Scaffold project | Create directory structure, `pyproject.toml`, `requirements.txt` |
| 1.2 | Configurable lineage schema | `lineage_schema.yaml` defines all fields (name, type, required/optional, per-language overrides). A Pydantic model is generated/validated from it at startup. |
| 1.3 | Settings | Env-var driven config: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_API_VERSION`, `MODEL_NAME`, `TARGET_REPO_PATH`, `SKILLS_DIR`. |

---

## Phase 2 — Copilot SDK + Azure Foundry BYOK Session

| # | Task | Details |
|---|------|---------|
| 2.1 | Session factory | `CopilotClient()` → `create_session()` with Azure BYOK `ProviderConfig` |
| 2.2 | Non-interactive mode | `on_permission_request=PermissionHandler.approve_all`. No `on_user_input_request` → agent cannot pause for input. |
| 2.3 | System message | Inject lineage task, output schema, relevant SKILL.md content. |
| 2.4 | Workspace CWD | `SubprocessConfig(cwd=TARGET_REPO_PATH)` so agent file tools operate on the target repo. |

### BYOK Provider Config

```python
provider={
    "type": "azure",
    "base_url": os.environ["AZURE_OPENAI_ENDPOINT"],
    "api_key": os.environ["AZURE_OPENAI_KEY"],
    "azure": {"api_version": os.environ.get("AZURE_API_VERSION", "2024-10-21")},
}
model = os.environ.get("MODEL_NAME", "gpt5.1")
```

**Auth note:** With BYOK, all LLM calls go directly to Azure Foundry. The Copilot CLI is used only as the agent runtime (tool execution, file access). No GitHub token is needed — set `use_logged_in_user=False` and rely entirely on the Azure provider.

---

## Phase 3 — AST-First Flow Discovery + Incremental Processing

### Problem

Large repositories (500+ files) will exceed the agent's context window if we try to:
- Load all source files at once
- Have the agent produce all lineage records in a single response
- Let the agent browse freely (it will get lost or skip files)

### Solution: AST Finds the Flows, Agent Interprets the Lineage

The core insight: **our code does the heavy lifting of finding data flows via AST; the LLM only interprets small, relevant code snippets.** The agent never reads entire files.

```
┌─────────────────────────────────────────────────────────────┐
│  Phase A: AST Pre-Scan (our Python code, zero LLM calls)   │
│                                                             │
│  1. Walk repo → find all source files by language           │
│  2. Parse each file's AST                                   │
│  3. Build call graph (who calls whom, across files)         │
│  4. Identify "data-touching" patterns:                      │
│     - DB calls (JDBC, SQLAlchemy, Django ORM, raw SQL)      │
│     - File I/O (read/write CSV, JSON, Parquet)              │
│     - API calls (HTTP clients, REST endpoints)              │
│     - Message queue ops (Kafka produce/consume)             │
│     - DataFrame transforms (pandas, Spark)                  │
│  5. For each pattern found, extract the code snippet        │
│     (function body + relevant imports + called functions)    │
│  6. Build work manifest with snippets + call chain context  │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase B: LLM Interpretation (one snippet at a time)        │
│                                                             │
│  For each data-flow snippet:                                │
│    1. Send snippet + call chain context to agent            │
│    2. Agent calls emit_lineage_record() per data flow       │
│    3. Record appended to JSONL on disk immediately          │
│    4. Agent context stays small (just current snippet)      │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase C: Cross-File Lineage Resolution                     │
│                                                             │
│  1. Use call graph from Phase A to chain records            │
│     Controller.getUser() → UserService.find()               │
│       → UserDAO.query() → SELECT * FROM users               │
│  2. Agent resolves ambiguous connections with context        │
│  3. Merge into complete lineage chains                      │
└─────────────────────────────────────────────────────────────┘
```

#### Step 1 — AST Pre-Scan: Building the Work Manifest

Our code (NOT the LLM) scans every source file and produces:

```python
@dataclass
class DataFlowSnippet:
    file_path: str
    language: str
    function_name: str
    class_name: str | None
    snippet: str              # Just the function body (typically 5-50 lines)
    call_chain: list[str]     # ["OrderController.create", "OrderService.save", "OrderDAO.insert"]
    detected_patterns: list[str]  # ["jdbc_call", "sql_string", "http_request"]
    imports: list[str]        # Relevant imports for context
    called_by: list[str]      # Functions that call this one
    calls: list[str]          # Functions this one calls

manifest = [
    DataFlowSnippet(
        file_path="src/dao/OrderDAO.java",
        language="java",
        function_name="insertOrder",
        class_name="OrderDAO",
        snippet='public void insertOrder(Order order) {\n    jdbcTemplate.update("INSERT INTO orders ...", order.getId());\n}',
        call_chain=["OrderController.create", "OrderService.save", "OrderDAO.insertOrder"],
        detected_patterns=["jdbc_call", "sql_string"],
        imports=["org.springframework.jdbc.core.JdbcTemplate"],
        called_by=["OrderService.save"],
        calls=[],
    ),
    ...
]
```

The AST detects data-touching patterns using heuristics per language:

| Language | Pattern | AST Detection Method |
|----------|---------|---------------------|
| Java | JDBC calls | Method calls on `JdbcTemplate`, `PreparedStatement`, `Connection` |
| Java | JPA/Hibernate | Classes annotated `@Entity`, `@Repository`; method calls on `EntityManager` |
| Java | REST endpoints | Methods annotated `@GetMapping`, `@PostMapping`, etc. |
| Java | Kafka | Calls to `KafkaTemplate.send()`, `@KafkaListener` methods |
| Python | SQLAlchemy | Calls to `session.query()`, `session.execute()`, model definitions |
| Python | pandas | Calls to `pd.read_csv()`, `df.to_sql()`, `pd.merge()` |
| Python | Django ORM | `Model.objects.filter()`, `Model.objects.create()` |
| Python | HTTP | `requests.get()`, `httpx.post()`, FastAPI endpoint decorators |
| Python | File I/O | `open()`, `pathlib` read/write, `json.load()` |
| Any | Raw SQL | String literals matching SQL patterns (`SELECT`, `INSERT`, `UPDATE`, `DELETE`) |

#### Step 2 — Feeding Snippets to the Agent

Each snippet is sent as a self-contained prompt:

```python
for snippet in manifest:
    prompt = f"""
    Analyze this code snippet for data lineage.

    File: {snippet.file_path}
    Function: {snippet.class_name}.{snippet.function_name}
    Call chain: {' → '.join(snippet.call_chain)}
    Detected patterns: {snippet.detected_patterns}
    Imports: {snippet.imports}

    ```{snippet.language}
    {snippet.snippet}
    ```

    Identify ALL data sources and targets. For each data flow,
    call emit_lineage_record() with the details.
    """
    await session.send(prompt)
    await idle.wait()
```

Because each snippet is small (typically 5-50 lines + context), the agent:
- Never hits context limits
- Can focus deeply on one data flow
- Produces precise evidence (exact line references)

#### Step 3 — Cross-File Lineage (Critical)

Since the AST pre-scan builds a **call graph**, we know the full chain:

```
OrderController.createOrder()  →  OrderService.saveOrder()  →  OrderDAO.insertOrder()  →  SQL INSERT
     ↑ REST endpoint                    ↑ business logic              ↑ DB access
```

After per-snippet analysis, a **stitching pass** connects records:

1. Our code matches records by `dataOperationName` to call graph edges
2. For ambiguous connections, a lightweight agent call resolves them:
   ```
   "Given these two lineage records and the call chain, are they part
    of the same data flow? If yes, what is source→target?"
   ```
3. Final output includes complete end-to-end lineage chains

#### Step 4 — Append-to-File Output (JSONL)

```python
@define_tool(name="emit_lineage_record", description="...", skip_permission=True)
async def emit_lineage_record(params: LineageRecordParams) -> str:
    record = validate_record(params)
    # Append to JSONL file (one JSON object per line)
    with open(output_path, "a") as f:
        f.write(record.model_dump_json() + "\n")
    return f"Record #{count} saved for {params.sourceEntityName} → {params.targetEntityName}"
```

Output format: **JSONL** (JSON Lines) — one record per line, trivially appendable. No memory accumulation.

#### Evidence Chain

Each lineage record must capture the **full transformation path** from source to target — not just the endpoints. The `dataOperationEvidence` field is a JSON array of evidence steps:

```json
{
  "sourceEntityName": "users (PostgreSQL table)",
  "targetEntityName": "user_report.csv",
  "dataOperationEvidence": [
    {
      "step": 1,
      "file": "src/dao/UserDAO.java",
      "line": 45,
      "code": "jdbcTemplate.query(\"SELECT id, name, email FROM users\", mapper)",
      "description": "Read from users table via JDBC"
    },
    {
      "step": 2,
      "file": "src/service/UserService.java",
      "line": 72,
      "code": "users.stream().map(u -> new UserDTO(u.getId(), u.getName().toUpperCase()))",
      "description": "Transform: uppercase name, map to DTO"
    },
    {
      "step": 3,
      "file": "src/service/UserService.java",
      "line": 78,
      "code": "filteredUsers = users.stream().filter(u -> u.isActive())",
      "description": "Filter: only active users"
    },
    {
      "step": 4,
      "file": "src/export/ReportExporter.java",
      "line": 31,
      "code": "csvWriter.write(userDTOs, \"user_report.csv\")",
      "description": "Write filtered, transformed data to CSV"
    }
  ]
}
```

This way, every `.map()`, `.filter()`, `.groupBy()`, type conversion, column rename, join, or aggregation between source and target is captured as a step in the evidence chain. The agent is instructed to trace the data through every transformation.

#### Final Output: CSV Conversion

Once progress reaches **100%** (all snippets processed + cross-file stitching done), the JSONL is converted to a CSV file:

```python
def convert_jsonl_to_csv(jsonl_path: str, csv_path: str, schema: LineageSchema):
    """Convert JSONL to CSV with configured columns."""
    records = []
    with open(jsonl_path) as f:
        for line in f:
            records.append(json.loads(line))

    # Flatten evidence chain to string for CSV
    for r in records:
        if isinstance(r.get("dataOperationEvidence"), list):
            r["dataOperationEvidence"] = " -> ".join(
                f"[{e['file']}:{e['line']}] {e['description']}" for e in r["dataOperationEvidence"]
            )

    df = pd.DataFrame(records)
    # Ensure all configured columns exist, fill missing with empty
    for field in schema.all_field_names():
        if field not in df.columns:
            df[field] = ""
    df = df[schema.all_field_names()]  # Reorder to match schema
    df.to_csv(csv_path, index=False)
```

CSV columns match exactly the fields from `lineage_schema.yaml`:
`sourceAppID, sourceDataSourceName, sourceTechnologyType, sourceSchemaType, sourceSchemaName, sourceEntityType, sourceEntityName, sourceColumnDataType, sourceColumnName, targetAppID, targetDataTargetName, targetTechnologyType, targetSchemaType, targetSchemaName, targetEntityType, targetEntityName, targetColumnDataType, targetColumnName, dataOperationDate, dataOperationType, dataOperationName, dataOperationDescription, dataOperationEvidence`

#### Step 5 — Session Strategy for Large Repos

| Batch | Session | Why |
|-------|---------|-----|
| All snippets from one file | Same session | Agent retains within-file context |
| Moving to next file | New session | Clean context, no carryover bloat |
| Cross-file stitching | Dedicated session | Focused on connecting records |

For files with 50+ methods, further split into groups of 10 methods per session turn.

#### Step 6 — Progress Tracking & Resume

```json
{
    "started_at": "2026-04-28T10:00:00Z",
    "manifest_total": 312,
    "snippets_processed": 187,
    "snippets_failed": 3,
    "records_emitted": 456,
    "current_file": "src/service/OrderService.java",
    "failed_snippets": [
        {"file": "src/LegacyService.java", "function": "complexQuery", "error": "timeout", "retries": 2}
    ],
    "phase": "snippet_analysis"
}
```

On restart, we skip already-processed snippets.

---

## Phase 4 — Skills & AST Tools

| # | Task | Details |
|---|------|---------|
| 4.1 | Python AST skill | `skills/python/ast_parser.py` — wraps stdlib `ast`. Extracts functions, classes, imports, call graph, and detects data-touching patterns (SQLAlchemy, pandas, Django ORM, file I/O, HTTP, raw SQL). |
| 4.2 | Java AST skill | `skills/java/ast_parser.py` — wraps `javalang`. Same interface: extracts methods, classes, imports, call graph, detects JDBC, JPA, Spring Data, Kafka, REST annotations. |
| 4.3 | AST tool for agent | `analyze_ast` tool lets the agent request additional AST info if a snippet is ambiguous (e.g., "show me what `processData()` calls"). This is a **backup** — most AST work is done in the pre-scan. |
| 4.4 | `emit_lineage_record` tool | Agent calls this per record. Validates via Pydantic, appends to JSONL file on disk. Returns confirmation (no data back into context). |
| 4.5 | Skill discovery | `tools/registry.py` scans `skills/` for `SKILL.md` + `ast_parser.py`, builds tool list. Framework-agnostic: skills are per-language, LLM handles framework specifics. |

### AST Tool Design

Two roles for AST:

**Role 1 — Pre-scan (our code, runs before agent):**
```python
class ASTPreScanner:
    """Runs locally, no LLM. Produces the work manifest."""
    def scan_repo(self, repo_path: str) -> list[DataFlowSnippet]: ...
    def build_call_graph(self, repo_path: str) -> CallGraph: ...
    def detect_data_patterns(self, file_path: str) -> list[DataFlowSnippet]: ...
```

**Role 2 — Agent backup tool (available during LLM analysis):**
```python
class AnalyzeAstParams(BaseModel):
    file_path: str = Field(description="Path to source file")
    query: str = Field(description="What to find: imports, function_calls, data_flows, callers, callees")
    scope: str | None = Field(default=None, description="Limit to a specific method/function/class")

@define_tool(name="analyze_ast", description="Query the AST for additional context when a snippet is ambiguous", skip_permission=True)
async def analyze_ast(params: AnalyzeAstParams) -> str:
    ...
```

The agent uses `analyze_ast` only when it needs more context (e.g., "what does `processData()` actually call?"). Most of the time, the pre-scan provides everything.

---

## Phase 5 — Configurable Lineage Schema

### `lineage_schema.yaml`

```yaml
fields:
  - name: sourceAppID
    type: string
    required: false
    description: "Application identifier for the source system"
  - name: sourceDataSourceName
    type: string
    required: false
    description: "Name of the source data source (DB name, API name, file path)"
  - name: sourceTechnologyType
    type: string
    required: true
    description: "Technology type: PostgreSQL, MySQL, REST API, Kafka, CSV, etc."
  - name: sourceSchemaType
    type: string
    required: false
    description: "Schema type: database, api, file, message_queue"
  - name: sourceSchemaName
    type: string
    required: false
    description: "Schema name (DB schema, API path prefix)"
  - name: sourceEntityType
    type: string
    required: false
    description: "Entity type: table, view, endpoint, topic, file"
  - name: sourceEntityName
    type: string
    required: true
    description: "Entity name: table name, endpoint path, topic name"
  - name: sourceColumnDataType
    type: string
    required: false
  - name: sourceColumnName
    type: string
    required: false
  - name: targetAppID
    type: string
    required: false
  - name: targetDataTargetName
    type: string
    required: false
  - name: targetTechnologyType
    type: string
    required: true
  - name: targetSchemaType
    type: string
    required: false
  - name: targetSchemaName
    type: string
    required: false
  - name: targetEntityType
    type: string
    required: false
  - name: targetEntityName
    type: string
    required: true
  - name: targetColumnDataType
    type: string
    required: false
  - name: targetColumnName
    type: string
    required: false
  - name: dataOperationDate
    type: string
    required: false
    description: "ISO date when lineage was extracted"
  - name: dataOperationType
    type: string
    required: false
    description: "READ, WRITE, TRANSFORM, COPY, JOIN, FILTER, AGGREGATE"
  - name: dataOperationName
    type: string
    required: false
    description: "Function/method name performing the operation"
  - name: dataOperationDescription
    type: string
    required: false
  - name: dataOperationEvidence
    type: string
    required: true
    description: "Code snippet or file:line reference as proof"

language_overrides:
  python:
    extra_fields:
      - name: sourcePandasOperation
        type: string
        required: false
        description: "Pandas method if applicable (read_csv, merge, groupby)"
      - name: sourceORMModel
        type: string
        required: false
        description: "SQLAlchemy/Django model class name"
  java:
    extra_fields:
      - name: sourceJDBCDriver
        type: string
        required: false
      - name: sourceSpringRepository
        type: string
        required: false
      - name: sourceJPAEntity
        type: string
        required: false
```

---

## Phase 6 — Orchestration (`main.py`)

```
1. Load config + lineage schema → build Pydantic model
2. Scan target repo → detect languages, build file list
3. Pre-process via AST (our code, not LLM):
   a. For each source file, extract "units" (functions, methods, classes)
   b. Build work manifest with chunks
4. Load relevant skills (Python, Java, or both)
5. Build system message from SKILL.md files + schema
6. Load or create progress.json (for resume support)
7. For each unprocessed chunk:
   a. Create/reuse session (hybrid strategy)
   b. Register tools: analyze_ast, emit_lineage_record
   c. Send chunk prompt to agent
   d. Wait for SessionIdleData
   e. Update progress.json
8. Post-process:
   a. Read JSONL output
   b. Deduplicate records
   c. Validate completeness against manifest
   d. Write final lineage_output.json
9. Print summary stats
```

---

## Phase 7 — Hardening & Extensibility

| # | Task | Details |
|---|------|---------|
| 7.1 | Permission scoping | Custom handler: allow `read` + `custom-tool` only; deny `shell` + `write`. |
| 7.2 | Large repo handling | Infinite sessions with compaction for within-file analysis. New sessions across files. |
| 7.3 | Error recovery | Retry failed chunks (configurable max retries). Log failures to `progress.json`. |
| 7.4 | Add a new language | Drop folder in `skills/` with `SKILL.md` + `ast_parser.py` implementing standard interface. No code changes elsewhere. |
| 7.5 | Testing | Unit tests for AST parsers with fixture files. Integration test with sample repo. |
| 7.6 | CI/CD | GitHub Actions: lint, test, run against sample repo. |

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `github-copilot-sdk>=0.3.0` | Copilot CLI JSON-RPC client |
| `pydantic>=2.0` | Schema validation + JSON schema generation |
| `pyyaml` | Configurable lineage schema |
| `javalang` | Java AST parsing |
| `pandas` | CSV output generation |
| Python stdlib `ast` | Python AST (no install) |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Context window overflow | Chunked processing, append-to-disk, never accumulate results in context |
| Agent misses lineage flows | AST pre-scan identifies all units; post-process checks coverage; retry missed units |
| Agent produces invalid records | Pydantic validation in `emit_lineage_record` tool; reject + ask agent to retry |
| Very large files (10k+ lines) | AST scope parameter limits analysis to one method at a time |
| Cross-file lineage (A calls B calls DB) | AST call graph traces full chain; stitching pass connects per-snippet records |
| Session crashes mid-file | `progress.json` enables resume from last successful chunk |
| Agent omits transformation steps | System prompt explicitly requires every intermediate step; evidence chain is validated for completeness (must have ≥1 step) |
| Variable/identifier used as data source name | Acceptable — use the name as-is from code since config files aren't in repo |

---

## Resolved Questions

| # | Question | Answer |
|---|----------|--------|
| Q1 | Azure model | gpt5.1, already provisioned |
| Q2 | Repo size | Large (500+ files) |
| Q3 | Frameworks | Framework-agnostic — LLM handles any framework, AST skills are per-language |
| Q4 | Cross-file lineage | Critical — full call chain tracing required |
| Q5 | Output | JSONL during processing → CSV as final deliverable |
| Q6 | Auth | BYOK via Azure Foundry only, no GitHub token |
| Q7 | Chunking | AST-first: find flows via AST, feed only relevant snippets to agent |
| Q8 | SQL / ORM / SP | Find lineage with whatever is present — ORM calls, stored proc calls, raw SQL, any data access pattern. No need for a separate SQL parser; the LLM interprets SQL strings found by AST. |
| Q9 | Config files | Configs won't be in source code. For connection names (DB name, API name), use the identifier/variable name as it appears in code (e.g., `userDatabase`, `orderService`). |
| Q10 | Sample repo | User will provide sample code for testing. |
| Q11 | Evidence | JSONL records must contain full evidence chain — every transformation step (map, filter, join, type conversion, aggregation) from source to target, not just endpoints. |
