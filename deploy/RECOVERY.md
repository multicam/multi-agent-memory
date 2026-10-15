# PostgreSQL credential recovery

Both the serving process and curator call `Config.from_env()`. Python does not
load `.env` itself. Both system units inject `/opt/multi-agent-memory/.env`;
the server keeps the environment loaded when its process started. Changing
the file does not update an already-running process.

The workstation repo `.env` is the authority used by this repo's sync helper.
Deployment tooling may generate the consumer file from a separate credential
source; check the sources agree before recovery. This helper does not modify
deployment tooling or rotate the PostgreSQL role password.

From the workstation, run:

```bash
deploy/sync-pg-password.sh          # Verify authority and exact consumer endpoint; no changes
deploy/sync-pg-password.sh --apply  # Explicit password-only repair, with protected backup
```

`VM_SSH` (`user@host`) is required; `LOCAL_ENV` and `REMOTE_ENV` override the
defaults. The local and remote probes each run in a child process with a
10-second overall deadline. A failed post-write probe restores the original;
if that restore itself fails, the helper exits with a distinct message pointing
to the protected backup. Run as the
consumer environment file's owner. Parsing supports a strict subset of systemd
`EnvironmentFile`: blank lines, whole-line `#`/`;` comments, canonical `NAME=`
assignments, unquoted values without whitespace or quotes, and whole single- or
double-quoted values without embedded quotes. Surrounding horizontal whitespace
is allowed. `$` and `#` inside values are literal. Escapes, continuations,
multiline values, embedded quotes and noncanonical assignments are rejected
throughout the file before any probe or write. Percent-encode special URI
characters instead of using shell escapes. Only PostgreSQL URI assignments are
supported for `PG_URL`; missing or duplicate assignments fail closed. URI passwords are decoded
and re-encoded correctly, including special characters. Credentials travel on
SSH stdin; neither credentials nor DSNs appear on argv or in receipts.

Before writing, the helper authenticates to the consumer's existing endpoint
with the candidate password and executes `SELECT 1` in a read-only transaction.
It preserves the endpoint, URI options and other environment settings, creates
an exclusive mode-600 `.env.bak-<timestamp>` beside the file, then atomically
replaces `.env` with a mode-600 file. A failed post-write probe restores the
original file. A failed replacement leaves the original file and backup intact.
The helper never starts curation, restarts services or deploys source.

For rollback, restore the receipt's backup as the same account, without printing
its contents. For example, on the consumer host, from `/opt/multi-agent-memory`:

```bash
install -m 600 .env.bak-<timestamp> .env
```

Restoration affects future environment loads. An existing serving process needs
a separately authorized restart to change its loaded configuration. A process
already crash-looping may load a changed file through its existing systemd
retry policy without an explicit restart command.

After a repair, prove `SELECT 1` using the curator's actual environment and
`Config.from_env()`. Then call the live MCP `memory_status` and a read-only
operation such as `recall`; an active systemd unit or successful MCP initialize
response alone does not establish database reads. Never run `curate.py`, even
with `--dry-run`, as a credential probe: it can call the LLM and is not a health
check. The deployment script's `--local-only` check also executes `SELECT 1`
without passing its DSN on argv.

## Recovery notes

- The helper verifies the consumer endpoint before any write, never restarts
  services, and treats MCP initialize alone as insufficient health evidence.
- Both serving and curation read the same environment file, so a stale password
  fails both consumers identically; compare the workstation authority and any
  separately generated credential source before repairing.
- Keep incident-specific evidence (hosts, accounts, backup names) in private
  operator notes, not in this repository.

Validation commands:

```bash
uv run pytest -q --cov=src --cov-report=term-missing --junitxml=.test-current.xml
uv run python scripts/backtest.py --check --no-run
```
