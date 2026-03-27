# Feature: Configuration

## Context
All configuration is read from environment variables. PG_URL is required; everything else has sensible defaults. The server must fail fast on missing required config.

## Scenarios

### Scenario: missing PG_URL raises ValueError
- **Given** no PG_URL environment variable is set
- **When** Config.from_env() is called
- **Then** ValueError is raised with a message about PG_URL
- **Priority:** critical

### Scenario: all defaults are sensible
- **Given** only PG_URL is set in the environment
- **When** Config.from_env() is called
- **Then** nas_path="/mnt/memory", server_port=8888, server_host="0.0.0.0"
- **And** anthropic_api_key=None, ollama_base_url=None
- **Priority:** critical

### Scenario: all env vars are read correctly
- **Given** PG_URL, NAS_PATH, SERVER_PORT, SERVER_HOST, ANTHROPIC_API_KEY, OLLAMA_BASE_URL are all set
- **When** Config.from_env() is called
- **Then** each field reflects its corresponding env var
- **Priority:** important

### Scenario: SERVER_PORT is parsed as integer
- **Given** SERVER_PORT="9999" in the environment
- **When** Config.from_env() is called
- **Then** config.server_port is int 9999, not string
- **Priority:** important

## Out of Scope
- Secret management (Ansible secrets.yml)
- Environment variable injection at deploy time

## Acceptance Criteria
- [ ] All critical scenarios pass
- [ ] Missing PG_URL fails immediately, not at first query
