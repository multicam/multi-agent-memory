"""Verify and optionally repair a consumer password, without restarting services.

Passwords travel to SSH on stdin. Only the password component is replaced;
the deployed endpoint and all other settings remain authoritative locally.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

import psycopg
from psycopg.conninfo import conninfo_to_dict


class RecoveryError(Exception):
    """A safe error message that contains no connection values."""


class RestoreFailed(RecoveryError):
    pass


RESTORE_FAILED_EXIT = 3
_TIMEOUT_CODE = -1
_UNEXPECTED_RESULT_CODE = 4
# Parent-enforced overall deadline: covers startup, DNS, socket stalls, cleanup.
_PROBE_TIMEOUT = 10
# DSN arrives on stdin only. Exit: 0 ok, 4 wrong result, other = failure.
_CHILD_SOURCE = """
import sys
import psycopg
with psycopg.connect(
    sys.stdin.read(), connect_timeout=5,
    options='-c default_transaction_read_only=on -c statement_timeout=5000',
) as conn:
    sys.exit(0 if conn.execute('SELECT 1').fetchone() == (1,) else 4)
"""


def _probe_child(pg_url):
    """Run the read probe in a killable child; return its exit code or _TIMEOUT_CODE."""
    try:
        return subprocess.run(
            [sys.executable, '-I', '-c', _CHILD_SOURCE], input=pg_url, text=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=_PROBE_TIMEOUT,
        ).returncode
    except subprocess.TimeoutExpired:
        return _TIMEOUT_CODE
    except OSError:
        return 1


def read_pg_url(text):
    # Narrow EnvironmentFile subset: no escapes, continuations, embedded quotes
    # or multiline values. $ and # within values are always literal. Validate
    # every line so preceding continuations cannot hide assignments.
    assignments = []
    for line in text.replace('\r\n', '\n').split('\n'):
        line = line.strip(' \t')
        if not line or line.startswith(('#', ';')):
            continue
        key, separator, value = line.partition('=')
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise RecoveryError('Unsupported EnvironmentFile assignment')
        value = value.strip(' \t')
        if '\\' in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise RecoveryError('EnvironmentFile escapes and continuations are unsupported')
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise RecoveryError('Unsupported EnvironmentFile quoting')
            value = value[1:-1]
        elif any(c.isspace() for c in value):
            raise RecoveryError('Unsupported unquoted EnvironmentFile whitespace')
        if "'" in value or '"' in value:
            raise RecoveryError('Embedded EnvironmentFile quotes are unsupported')
        if key == 'PG_URL':
            assignments.append(value)
    if len(assignments) != 1:
        raise RecoveryError('Expected exactly one PG_URL assignment')
    try:
        value = assignments[0]
        if not value.startswith(('postgresql://', 'postgres://')):
            raise ValueError
        conninfo_to_dict(value)
        return value
    except (ValueError, psycopg.Error):
        raise RecoveryError('Invalid PG_URL; expected a PostgreSQL URI') from None


def verify(pg_url):
    code = _probe_child(pg_url)
    if code == 0:
        return
    if code == _UNEXPECTED_RESULT_CODE:
        raise RecoveryError('SELECT 1 returned an unexpected result')
    if code == _TIMEOUT_CODE:
        raise RecoveryError('PostgreSQL read probe timed out; no credential changes made')
    raise RecoveryError('PostgreSQL read probe failed; no credential changes made')


def _atomic_write(path, content):
    fd, temporary = tempfile.mkstemp(prefix='.pg-env-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def recover(path, password, *, apply=False):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.geteuid():
        raise RecoveryError('Consumer environment must be a regular file owned by the recovery account')
    original = path.read_bytes().decode('utf-8')
    current = read_pg_url(original)
    # libpq accepts literal # in URI passwords; urllib's generic URL parser
    # treats it as a fragment boundary, even with allow_fragments=False.
    scheme, _, remainder = current.partition('://')
    authority, slash, suffix = remainder.partition('/')
    userinfo, separator, host = authority.rpartition('@')
    if not separator or ':' not in userinfo or not password:
        raise RecoveryError('Expected an explicit user and password in PG_URL')
    candidate = (scheme + '://' + userinfo.partition(':')[0] + ':'
                 + quote(password, safe='') + '@' + host + slash + suffix)
    current_info = conninfo_to_dict(current)
    try:
        candidate_info = conninfo_to_dict(candidate)
    except psycopg.Error:
        raise RecoveryError('Unsupported URI structure; cannot safely replace password') from None
    if candidate_info != dict(current_info, password=password):
        raise RecoveryError('Unsupported URI structure; cannot safely replace password')
    # Verify at the consumer endpoint, never just at the workstation's localhost.
    verify(candidate)
    matches = current_info.get('password') == password
    result = {'credential_matches': matches, 'select_1': True, 'changed': False}
    if not apply or matches:
        return result

    updated = ''.join(
        line.replace(current, candidate, 1) if line.lstrip(' \t').startswith('PG_URL=') else line
        for line in original.splitlines(keepends=True)
    )
    if read_pg_url(updated) != candidate:
        raise RecoveryError('Cannot safely replace the PG_URL value')
    if path.read_bytes().decode('utf-8') != original:
        raise RecoveryError('Consumer environment changed during verification')
    backup = path.with_name(path.name + '.bak-' + str(time.time_ns()))
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    _atomic_write(path, updated)
    try:
        verify(read_pg_url(path.read_text()))
    except (RecoveryError, OSError, UnicodeDecodeError):
        try:
            _atomic_write(path, original)
        except OSError:
            raise RestoreFailed(
                'Post-write probe failed and restore failed; the environment file may hold '
                'a bad credential. Use the protected backup (.env.bak-*) beside it'
            ) from None
        raise RecoveryError('Post-write read probe failed; original environment restored') from None
    result.update(changed=True, backup=backup.name)
    return result


def remote_main():
    try:
        payload = json.load(sys.stdin)
        result = recover(payload['path'], payload['password'], apply=payload['apply'])
        print(json.dumps(result))
    except RestoreFailed:
        print('Post-write probe failed and restore failed; use the protected backup '
              '(.env.bak-*) beside the environment file', file=sys.stderr)
        raise SystemExit(RESTORE_FAILED_EXIT) from None
    except (RecoveryError, OSError, ValueError, KeyError, TypeError, AttributeError):
        # Never relay exception text from a driver, parser, SSH or filesystem.
        print('Credential recovery failed; inspect configuration and protected backup paths', file=sys.stderr)
        raise SystemExit(1) from None


def _read_receipt(text):
    malformed = RecoveryError('Remote credential receipt was malformed; no restart attempted')
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise malformed from None
    flags = ('credential_matches', 'select_1', 'changed')
    if not isinstance(data, dict) or any(not isinstance(data.get(k), bool) for k in flags):
        raise malformed
    receipt = {k: data[k] for k in flags}
    if isinstance(data.get('backup'), str):
        receipt['backup'] = data['backup']
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Replace stale password after target verification')
    parser.add_argument('--local-only', action='store_true', help='Only probe the current local environment file')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        local = Path(os.environ.get('LOCAL_ENV', root / '.env'))
        pg_url = read_pg_url(local.read_text())
        verify(pg_url)
        if args.local_only:
            print('PostgreSQL SELECT 1 succeeded using the local environment file')
            return
        password = conninfo_to_dict(pg_url).get('password')
        if not password:
            raise RecoveryError('Authority PG_URL must contain an explicit password')
        # Static program on argv; the credential is supplied only on SSH stdin.
        source = Path(__file__).read_text().rsplit('\nif __name__', 1)[0]
        source += '\nremote_main()\n'
        remote_root = '/opt/multi-agent-memory'
        target = os.environ.get('VM_SSH')
        if not target:
            raise RecoveryError('Set VM_SSH to the consumer host as user@host')
        command = f'cd {remote_root} && .venv/bin/python -c ' + shlex.quote(source)
        result = subprocess.run(
            ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
             '--', target, command],
            input=json.dumps({'password': password, 'apply': args.apply,
                              'path': os.environ.get('REMOTE_ENV', remote_root + '/.env')}),
            text=True, capture_output=True, timeout=45,
        )
        if result.returncode == RESTORE_FAILED_EXIT:
            raise RecoveryError('Remote post-write probe and restore failed; use the '
                                'protected backup (.env.bak-*) beside the remote .env')
        if result.returncode:
            raise RecoveryError('Remote credential verification/recovery failed; no restart attempted')
        receipt = _read_receipt(result.stdout)
        print(json.dumps(receipt))
        if receipt['changed']:
            print('Protected rollback backup is beside .env. Curator uses the next environment load.')
            print('Serving process needs a separately authorized restart to load the repaired credential.')
    except (RecoveryError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, RecoveryError) else 'Credential recovery failed', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
