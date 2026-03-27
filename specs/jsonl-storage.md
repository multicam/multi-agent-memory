# Feature: JSONL Storage (NAS Write-Ahead)

## Context
JSONL on NAS is the source of truth (Diderot pattern). PG is a rebuildable index. Every memory is appended to a per-session JSONL file before PG insert. The rebuild script can reconstruct PG from JSONL alone.

## Scenarios

### Scenario: append creates file at correct path
- **Given** a mounted NAS at the configured path
- **When** append(record, agent_id="ag-1", session_id="sess-1") is called
- **Then** the file is created at {nas_path}/agents/ag-1/episodic/sess-1.jsonl
- **Priority:** critical

### Scenario: second append to same session appends to same file
- **Given** a file already exists for session "sess-1"
- **When** append() is called again for "sess-1"
- **Then** the new record is appended (not overwritten)
- **And** the file contains two lines
- **Priority:** critical

### Scenario: different session creates different file
- **Given** an append for session "sess-1" exists
- **When** append() is called for session "sess-2"
- **Then** a new file {nas_path}/agents/ag-1/episodic/sess-2.jsonl is created
- **Priority:** important

### Scenario: append_shared writes to shared directory
- **Given** a promoted record
- **When** append_shared(record, session_id="sess-1") is called
- **Then** the file is created at {nas_path}/shared/episodic/sess-1.jsonl
- **Priority:** critical

### Scenario: append raises on unmounted NAS
- **Given** the NAS is not mounted
- **When** append() is called
- **Then** OSError is raised
- **Priority:** critical

### Scenario: each line is valid JSON
- **Given** a JSONL file with multiple appended records
- **When** each line is parsed with json.loads()
- **Then** all lines parse successfully
- **Priority:** critical

### Scenario: read_all returns records sorted by timestamp
- **Given** JSONL files across multiple agents and sessions
- **When** read_all() is called
- **Then** all records are returned sorted by timestamp ascending
- **Priority:** important

### Scenario: read_all on empty NAS returns empty list
- **Given** no agent directories exist under the NAS path
- **When** read_all() is called
- **Then** an empty list is returned
- **Priority:** important

### Scenario: is_mounted returns correct status
- **Given** the NAS path
- **When** is_mounted() is called
- **Then** it returns True if the path is a mount point, False otherwise
- **Priority:** important

## Out of Scope
- CIFS mount configuration (Ansible role)
- NAS performance benchmarking
- Concurrent write safety (single-writer model assumed)

## Acceptance Criteria
- [ ] All critical scenarios pass
- [ ] Every appended line is valid, parseable JSON
- [ ] File paths follow the agent/session directory convention
- [ ] OSError raised cleanly when NAS is unavailable
