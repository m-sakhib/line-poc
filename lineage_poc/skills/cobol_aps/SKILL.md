# COBOL/APS Lineage Skill

## About APS

APS (Micro Focus Advanced Programming Software for z/OS) is a full-function
application generator that produces COBOL for mainframe environments (CICS, IMS,
ISPF, MVS batch). It uses high-level proprietary macros instead of manual COBOL.

APS source files (.aps) do NOT follow standard COBOL division structure.
Data access is through APS-specific keywords and CALL to subprograms.

## Data Flow Patterns in APS

### VSAM File I/O
- `SELECT file-name ASSIGN TO physical-name` — declares a logical file
- `IO SELECT file-name ...` — alternative I/O selection syntax
- `FD file-name` — defines the record layout for a file
- `READ file-name` — read a record (source)
- `WRITE record-name` — write a record (target)
- `REWRITE record-name` — update a record in place (target)
- `DELETE file-name` — delete a record (target)

### Subprogram Calls
- `CALL 'PROGNAME' USING param1 param2` — data passed to/from another program
- The USING parameters are the data flow — they carry records between programs
- Both the caller and the callee touch the passed data

### COPY / Copybooks
- `COPY COPYBOOK-NAME` — imports external data structure definitions
- Copybooks define the record layouts (fields, PIC clauses)
- These are equivalent to "schemas" — they tell you what columns exist

### Data Movement
- `MOVE source-field TO target-field` — direct data transformation
- Chains of MOVEs show how data flows within a program

### Record Definitions
- `01 RECORD-NAME.` or `REC RECORD-NAME.` — defines a record structure
- `05 FIELD-NAME PIC X(30).` — defines a field within a record
- Fields map to columns in lineage output

## Evidence Chain for APS

Every lineage record must trace the full path:

```
Step 1: [program.aps:10] SELECT CUSTOMER-FILE ASSIGN TO CUSTVSAM
        → "VSAM file declaration for customer data"
Step 2: [program.aps:50] READ CUSTOMER-FILE INTO WS-CUSTOMER-REC
        → "Read customer record from VSAM file"
Step 3: [program.aps:55] MOVE CUST-NAME TO OUT-NAME
        → "Transform: copy customer name to output record"
Step 4: [program.aps:60] CALL 'AUDITPGM' USING WS-AUDIT-REC
        → "Pass audit record to audit subprogram"
```

## What the LLM Agent Should Do

1. **Identify file sources** — Which VSAM files are READ from? These are sources.
2. **Identify file targets** — Which files are WRITE/REWRITE'd to? These are targets.
3. **Trace MOVE chains** — How does data move from read fields to write fields?
4. **Identify CALL data passing** — What records are passed to subprograms via USING?
5. **Map fields to columns** — Use record/field definitions to identify column-level lineage.
6. **Note the evidence** — Every step must reference the exact line in the .aps file.

## Technology Mapping

| APS Construct | Lineage Field Value |
|---------------|-------------------|
| VSAM file via SELECT | sourceTechnologyType = "VSAM" |
| READ file-name | sourceEntityName = file-name |
| WRITE record TO file | targetEntityName = file-name |
| CALL 'PROG' USING | sourceEntityName or targetEntityName = "PROG (subprogram)" |
| Field in record | sourceColumnName / targetColumnName |
| PIC clause | sourceColumnDataType / targetColumnDataType |
| COPY copybook | Reference for record layout / schema |

## Important Notes

- APS files do NOT have EXEC SQL or EXEC CICS — data access is via APS macros
- The parser provides structural matches; the LLM interprets the business meaning
- If a CALL passes data, the called program may be the actual source/target
- MOVE chains can be long — trace the full path, don't skip intermediate steps
- Data source names should use the logical file name as it appears in code
