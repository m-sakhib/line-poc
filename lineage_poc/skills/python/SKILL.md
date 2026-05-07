# Python Lineage Skill

## Python-Specific Data Access Patterns

### SQLAlchemy / ORM
- `session.query(Model)` — READ from the model's table
- `session.add(obj)` — WRITE to the model's table
- `session.execute(text("SQL"))` — Raw SQL, parse the string
- `engine.connect()` — Note the connection/engine name as data source

### Django ORM
- `Model.objects.filter(...)` — READ with conditions
- `Model.objects.create(...)` — WRITE
- `Model.objects.raw("SQL")` — Raw SQL
- `Model.save()` — WRITE

### pandas
- `pd.read_csv("file")` — READ from CSV file (source = filename)
- `pd.read_sql(query, conn)` — READ from DB (source = query's table)
- `df.to_csv("file")` — WRITE to CSV
- `df.to_sql("table", conn)` — WRITE to DB table
- `df.merge(other, on="col")` — JOIN two dataframes (document both sources)
- `df.groupby("col").agg(...)` — AGGREGATE (document the transformation)
- `df.rename(columns={...})` — Column rename (document in evidence)
- `df.drop(columns=[...])` — Column drop (document in evidence)

### File I/O
- `open("file", "r")` → READ; `open("file", "w")` → WRITE
- `json.load(f)` — READ from JSON file
- `json.dump(obj, f)` — WRITE to JSON file

### HTTP / REST
- `requests.get(url)` — READ from external API
- `requests.post(url, data)` — WRITE to external API
- `@app.get("/path")` (FastAPI) — Endpoint that serves data (trace where the data comes from)
- `@app.post("/path")` — Endpoint that receives data (trace where it goes)

### Evidence Chain Example (Python)
```
Step 1: [etl.py:10] df = pd.read_csv("users.csv")  → "Read user data from CSV"
Step 2: [etl.py:12] df = df[df.active == True]       → "Filter: only active users"
Step 3: [etl.py:13] df["name"] = df["name"].str.upper() → "Transform: uppercase name"
Step 4: [etl.py:15] df.to_sql("active_users", engine) → "Write to active_users table"
```
