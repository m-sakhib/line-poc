# Base Lineage Skill

You are analyzing source code to extract data lineage information.

## What is Data Lineage?
Data lineage tracks how data flows from sources to targets through transformations.
A lineage record captures: where data comes from, what happens to it, and where it goes.

## Rules

1. **One record per flow**: Call `emit_lineage_record()` once for each distinct source→target path.
2. **Complete evidence chain**: Every intermediate step must be documented — maps, filters, joins, type conversions, aggregations, column renames.
3. **Use code identifiers**: For data source names (DB, API, file), use the variable/identifier name as it appears in code.
4. **Be specific**: Prefer "users table" over "database". Prefer "GET /api/users" over "REST API".
5. **Column-level when possible**: If you can identify specific columns/fields being moved, create per-column records.
6. **Evidence is mandatory**: Every record must have at least one evidence step with file, line, code, and description.

## Common Data Flow Patterns

| Pattern | Source | Target | Evidence |
|---------|--------|--------|----------|
| DB read → API response | Table/view | REST endpoint | SELECT query → transform → return |
| API call → DB write | External API | Table | HTTP request → parse → INSERT |
| File read → DB write | CSV/JSON file | Table | File.read → transform → INSERT |
| DB read → File write | Table | CSV/JSON file | SELECT → transform → File.write |
| Queue consume → DB write | Kafka topic | Table | Consumer.poll → process → INSERT |
| DB read → Queue produce | Table | Kafka topic | SELECT → transform → Producer.send |
| DB-to-DB | Source table | Target table | SELECT → transform → INSERT |
| Stored procedure call | Input params | Output/table | CALL proc → internal transforms |
