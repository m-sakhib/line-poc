# Lineage POC — Quickstart

## Prerequisites

- Python 3.11+
- Azure OpenAI resource with a model deployed (e.g., gpt5.1)
- GitHub Copilot CLI installed (`npm install -g @github/copilot-cli` or bundled with the SDK)

## 1. Install dependencies

```powershell
cd c:\ms\projects\lineage_poc
pip install -r requirements.txt
pip install github-copilot-sdk
```

## 2. Set environment variables

```powershell
$env:AZURE_OPENAI_ENDPOINT = "https://your-resource.openai.azure.com"
$env:AZURE_OPENAI_KEY = "your-api-key"

# Optional (defaults shown):
$env:AZURE_API_VERSION = "2024-10-21"
$env:MODEL_NAME = "gpt5.1"
$env:MAX_RETRIES = "2"
$env:SNIPPETS_PER_TURN = "10"
```

## 3. Run against a target repo

```powershell
# Analyze a repository
python -m lineage_poc.main C:\path\to\your\repo --verbose

# Or use the test fixtures:
python -m lineage_poc.main C:\ms\projects\lineage_poc\test_fixtures\python_sample --verbose
python -m lineage_poc.main C:\ms\projects\lineage_poc\test_fixtures\java_sample --verbose
```

### CLI options

| Flag | Description |
|------|-------------|
| `repo_path` | (required) Path to the repository to analyze |
| `--output / -o` | Output directory (default: `./output`) |
| `--schema` | Path to custom `lineage_schema.yaml` (default: `./config/lineage_schema.yaml`) |
| `--verbose / -v` | Enable debug logging |

## 4. Check output

After the run completes, you'll find these files in the output directory:

```
output/
├── lineage_output.jsonl    # Raw records (one JSON object per line, appended incrementally)
├── lineage_output.csv      # Final CSV with all configured columns (created at 100%)
└── progress.json           # Progress tracker (shows processed/failed/total)
```

### CSV columns

The CSV contains these columns (configurable via `config/lineage_schema.yaml`):

| Column | Required | Description |
|--------|----------|-------------|
| sourceAppID | no | Source application identifier |
| sourceDataSourceName | no | Data source name as referenced in code |
| sourceTechnologyType | **yes** | PostgreSQL, MySQL, REST API, Kafka, CSV, etc. |
| sourceSchemaType | no | database, api, file, message_queue |
| sourceSchemaName | no | Schema name |
| sourceEntityType | no | table, view, endpoint, topic, file |
| sourceEntityName | **yes** | Table name, endpoint path, topic name |
| sourceColumnDataType | no | Column data type |
| sourceColumnName | no | Column/field name |
| targetAppID | no | Target application identifier |
| targetDataTargetName | no | Target destination name |
| targetTechnologyType | **yes** | Target technology type |
| targetSchemaType | no | Target schema type |
| targetSchemaName | no | Target schema name |
| targetEntityType | no | Target entity type |
| targetEntityName | **yes** | Target entity name |
| targetColumnDataType | no | Target column data type |
| targetColumnName | no | Target column/field name |
| dataOperationDate | no | ISO date of extraction |
| dataOperationType | no | READ, WRITE, TRANSFORM, COPY, JOIN, FILTER, AGGREGATE |
| dataOperationName | no | Function/method name |
| dataOperationDescription | no | Human-readable description |
| dataOperationEvidence | **yes** | Full transformation chain from source to target |

## 5. Resume a failed/interrupted run

Just re-run the same command. The tool reads `progress.json` and skips already-processed snippets:

```powershell
# Same command — picks up where it left off
python -m lineage_poc.main C:\path\to\your\repo --verbose
```

## 6. Customize the schema

Edit `config/lineage_schema.yaml` to add/remove/modify fields. Changes take effect on the next run.

To add a field:
```yaml
fields:
  # ... existing fields ...
  - name: myCustomField
    type: string
    required: false
    description: "My custom lineage field"
```

To add language-specific fields:
```yaml
language_overrides:
  python:
    extra_fields:
      - name: myPythonField
        type: string
        required: false
```

## 7. Add a new language

Create a folder under `lineage_poc/skills/` with:

```
lineage_poc/skills/newlang/
├── __init__.py
├── ast_parser.py    # Must implement get_data_touching_methods(file_path)
└── SKILL.md         # LLM guidance for this language
```

Register the file extension in `lineage_poc/skills/call_graph.py`:
```python
LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".java": "java",
    ".cs": "newlang",  # <-- add here
}
```

## How it works

```
┌─────────────────────────────────────────────────────────────┐
│  Phase A: AST Pre-Scan (local, no LLM calls)               │
│  • Parse all source files → build call graph                │
│  • Detect data-touching patterns (DB, API, file I/O)        │
│  • Extract small code snippets for each data flow           │
│  • Build work manifest                                      │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase B: LLM Analysis (one snippet at a time)              │
│  • Send each snippet to the agent via Copilot SDK           │
│  • Agent calls emit_lineage_record() per data flow          │
│  • Each record appended to JSONL immediately                │
│  • Progress tracked in progress.json                        │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase C: Finalize                                          │
│  • Convert JSONL → CSV with configured columns              │
│  • Print summary stats                                      │
└─────────────────────────────────────────────────────────────┘
```
