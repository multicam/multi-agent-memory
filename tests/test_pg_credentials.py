"""Recovery must verify the deployed endpoint before replacing a stale secret."""

import io
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from psycopg.conninfo import conninfo_to_dict

from deploy import pg_credentials as recovery

REAL_PROBE_CHILD = recovery._probe_child


@pytest.fixture(autouse=True)
def successful_select(monkeypatch):
    probe = MagicMock(return_value=0)
    monkeypatch.setattr(recovery, '_probe_child', probe)
    return probe


def test_stale_consumer_recovery_preserves_config_and_secures_rollback(tmp_path):
    env = tmp_path / '.env'
    original = (
        '# deployment settings\n'
        'PG_URL="postgresql://memory_user:stale@192.0.2.215:5432/agent_memory?sslmode=require"\n'
        'ANTHROPIC_API_KEY=untouched\nNAS_PATH=/mnt/memory\n'
    )
    env.write_text(original)
    env.chmod(0o600)
    password = 'new:p@ss /?&#%$'
    result = recovery.recover(env, password, apply=True)
    connect = recovery._probe_child

    target = connect.call_args.args[0]
    parsed = conninfo_to_dict(target)
    assert parsed['host'] == '192.0.2.215'
    assert parsed['user'] == 'memory_user'
    assert parsed['dbname'] == 'agent_memory'
    assert parsed['sslmode'] == 'require'
    assert parsed['password'] == password
    assert recovery.read_pg_url(env.read_text()) == target
    assert env.read_text().splitlines()[2:] == original.splitlines()[2:]
    backup = tmp_path / result['backup']
    assert backup.read_text() == original
    assert os.stat(backup).st_mode & 0o777 == 0o600
    assert os.stat(env).st_mode & 0o777 == 0o600


def test_target_auth_failure_never_changes_or_backs_up_consumer(tmp_path):
    env = tmp_path / '.env'
    original = 'PG_URL=postgresql://memory_user:stale@192.0.2.215/db\n'
    env.write_text(original)
    with patch.object(recovery, '_probe_child', return_value=3):
        with pytest.raises(recovery.RecoveryError) as error:
            recovery.recover(env, 'candidate-secret', apply=True)
    assert 'secret' not in str(error.value)
    assert env.read_text() == original
    assert list(tmp_path.iterdir()) == [env]


def test_check_is_read_only_even_when_credential_is_stale(tmp_path):
    env = tmp_path / '.env'
    original = 'PG_URL=postgresql://memory_user:stale@192.0.2.215/db\n'
    env.write_text(original)
    result = recovery.recover(env, 'working', apply=False)
    assert result['credential_matches'] is False
    assert result['select_1'] is True
    assert env.read_text() == original
    assert list(tmp_path.iterdir()) == [env]


@pytest.mark.parametrize('text', ['', 'PG_URL=x\nPG_URL=y\n', 'PG_URL=$(command)\n'])
def test_invalid_env_is_rejected_without_executing_it(text):
    with pytest.raises(recovery.RecoveryError):
        recovery.read_pg_url(text)


def test_failed_atomic_replace_keeps_original_and_rollback(tmp_path):
    env = tmp_path / '.env'
    original = 'PG_URL=postgresql://memory_user:stale@192.0.2.215/db\n'
    env.write_text(original)
    with patch.object(recovery.os, 'replace', side_effect=OSError('disk failure')):
        with pytest.raises(OSError):
            recovery.recover(env, 'working', apply=True)
    assert env.read_text() == original
    backups = list(tmp_path.glob('.env.bak-*'))
    assert len(backups) == 1
    assert backups[0].read_text() == original


def test_failed_post_write_probe_restores_original(tmp_path, successful_select):
    env = tmp_path / '.env'
    original = 'PG_URL=postgresql://memory_user:stale@192.0.2.215/db\n'
    env.write_text(original)
    successful_select.side_effect = [0, 3]
    with pytest.raises(recovery.RecoveryError, match='original environment restored'):
        recovery.recover(env, 'working', apply=True)
    assert env.read_text() == original
    assert next(tmp_path.glob('.env.bak-*')).read_text() == original


def test_cli_defaults_to_check_and_transports_secret_only_on_stdin(tmp_path, monkeypatch, capsys):
    env = tmp_path / '.env'
    dsn = 'postgresql://memory_user:private-candidate@localhost/db'
    env.write_text('PG_URL=' + dsn + '\n')
    monkeypatch.setenv('LOCAL_ENV', str(env))
    monkeypatch.setenv('VM_SSH', 'operator@example.invalid')
    monkeypatch.setattr('sys.argv', ['pg_credentials.py'])
    receipt = '{"credential_matches": false, "select_1": true, "changed": false}'
    with patch.object(recovery.subprocess, 'run', return_value=MagicMock(returncode=0, stdout=receipt)) as run:
        recovery.main()
    assert 'private-candidate' not in str(run.call_args.args)
    assert dsn not in str(run.call_args.args)
    payload = json.loads(run.call_args.kwargs['input'])
    assert payload['password'] == 'private-candidate'
    assert payload['apply'] is False
    assert 'private-candidate' not in capsys.readouterr().out


def test_repair_preserves_crlf_and_is_idempotent(tmp_path):
    env = tmp_path / '.env'
    original = b'PG_URL=postgresql://memory_user:old@192.0.2.215/db\r\nNAS_PATH=/mnt/memory\r\n'
    env.write_bytes(original)
    result = recovery.recover(env, 'working', apply=True)
    assert env.read_bytes() == original.replace(b':old@', b':working@')
    assert (tmp_path / result['backup']).read_bytes() == original
    result = recovery.recover(env, 'working', apply=True)
    assert result['changed'] is False
    assert len(list(tmp_path.glob('.env.bak-*'))) == 1


def test_symlink_consumer_is_rejected(tmp_path):
    target = tmp_path / 'target'
    target.write_text('PG_URL=postgresql://memory_user:old@192.0.2.215/db\n')
    env = tmp_path / '.env'
    env.symlink_to(target)
    with pytest.raises(recovery.RecoveryError, match='regular file'):
        recovery.recover(env, 'working', apply=True)
    recovery._probe_child.assert_not_called()


def test_wrong_select_result_never_changes_consumer(tmp_path, successful_select):
    env = tmp_path / '.env'
    original = 'PG_URL=postgresql://memory_user:old@192.0.2.215/db\n'
    env.write_text(original)
    successful_select.return_value = 4
    with pytest.raises(recovery.RecoveryError, match='unexpected result'):
        recovery.recover(env, 'working', apply=True)
    assert env.read_text() == original
    assert list(tmp_path.iterdir()) == [env]


@pytest.mark.parametrize('value', [
    'postgresql://u:pa"ss"@host/db',
    "postgresql://u:pa'ss'@host/db",
    'postgresql://u:pa\\ss@host/db',
    'postgresql://u:pa\\\nss@host/db',
    '"postgresql://u:pa\\$ss@host/db"',
    '"postgresql://u:pass@host/db"suffix',
    "'postgresql://u:pass@host/db'\nPG_URL =postgresql://u:other@host/db",
])
def test_unsupported_systemd_syntax_is_rejected_before_probe_or_write(tmp_path, value):
    env = tmp_path / '.env'
    original = 'PG_URL=' + value + '\n'
    env.write_text(original)
    with pytest.raises(recovery.RecoveryError):
        recovery.recover(env, 'replacement', apply=True)
    recovery._probe_child.assert_not_called()
    assert env.read_text() == original
    assert list(tmp_path.iterdir()) == [env]


@pytest.mark.parametrize('wrapper', ['', "'", '"'])
def test_systemd_subset_preserves_literal_dollar_and_hash(wrapper):
    uri = 'postgresql://u:pa$HOME#ss@host/db'
    text = '# comment\n; comment\nPG_URL=' + wrapper + uri + wrapper + '\n'
    assert recovery.read_pg_url(text) == uri
    assert conninfo_to_dict(recovery.read_pg_url(text))['password'] == 'pa$HOME#ss'


def test_continuation_before_pg_url_is_rejected_before_probe(tmp_path):
    env = tmp_path / '.env'
    env.write_text('OTHER=value\\\nPG_URL=postgresql://u:pass@host/db\n')
    with pytest.raises(recovery.RecoveryError):
        recovery.recover(env, 'replacement', apply=True)
    recovery._probe_child.assert_not_called()


@pytest.mark.parametrize('wrapper', ['', "'", '"'])
def test_recovery_preserves_systemd_value_semantics(tmp_path, wrapper):
    env = tmp_path / '.env'
    uri = 'postgresql://u:pa$HOME#ss@host/db?sslmode=require'
    original = '  PG_URL= ' + wrapper + uri + wrapper + ' \nOTHER=$literal#value\n'
    env.write_text(original)
    assert recovery.recover(env, 'pa$HOME#ss', apply=True)['changed'] is False
    assert env.read_text() == original
    result = recovery.recover(env, 'new$#pass', apply=True)
    assert result['changed'] is True
    parsed = conninfo_to_dict(recovery.read_pg_url(env.read_text()))
    assert parsed['password'] == 'new$#pass'
    assert parsed['host'] == 'host'
    assert parsed['dbname'] == 'db'
    assert parsed['sslmode'] == 'require'
    assert env.read_text().splitlines()[1] == 'OTHER=$literal#value'


def test_probe_child_has_overall_deadline_and_is_killed(monkeypatch):
    monkeypatch.setattr(recovery, '_probe_child', REAL_PROBE_CHILD)
    monkeypatch.setattr(recovery, '_CHILD_SOURCE', 'import time\ntime.sleep(60)\n')
    monkeypatch.setattr(recovery, '_PROBE_TIMEOUT', 0.5)
    with pytest.raises(recovery.RecoveryError, match='timed out; no credential changes') as error:
        recovery.verify('postgresql://u:private-pass@host/db')
    assert 'private-pass' not in str(error.value)


def test_probe_child_gets_dsn_on_stdin_only_with_read_only_bounds(monkeypatch):
    monkeypatch.setattr(recovery, '_probe_child', REAL_PROBE_CHILD)
    dsn = 'postgresql://u:private-pass@host/db'
    with patch.object(recovery.subprocess, 'run', return_value=MagicMock(returncode=0)) as run:
        recovery.verify(dsn)
    assert run.call_args.kwargs['input'] == dsn
    assert 'private-pass' not in str(run.call_args.args)
    assert run.call_args.kwargs['timeout'] == recovery._PROBE_TIMEOUT
    assert run.call_args.kwargs['stdout'] == recovery.subprocess.DEVNULL
    assert run.call_args.kwargs['stderr'] == recovery.subprocess.DEVNULL
    assert 'connect_timeout=5' in recovery._CHILD_SOURCE
    assert 'default_transaction_read_only=on' in recovery._CHILD_SOURCE


def test_real_child_failure_is_fixed_redacted_message(monkeypatch):
    monkeypatch.setattr(recovery, '_probe_child', REAL_PROBE_CHILD)
    with pytest.raises(recovery.RecoveryError) as error:
        recovery.verify('postgresql://u:private-pass@127.0.0.1:1/db?connect_timeout=1')
    assert str(error.value) == 'PostgreSQL read probe failed; no credential changes made'


def _fail_second_write(real):
    calls = []

    def write(path, content):
        calls.append(path)
        if len(calls) == 2:
            raise OSError('/secret/path denied')
        real(path, content)
    return write


def test_rollback_restore_failure_is_distinct_and_points_to_backup(tmp_path, successful_select, monkeypatch):
    env = tmp_path / '.env'
    original = 'PG_URL=postgresql://memory_user:stale@192.0.2.215/db\n'
    env.write_text(original)
    successful_select.side_effect = [0, 3]
    monkeypatch.setattr(recovery, '_atomic_write', _fail_second_write(recovery._atomic_write))
    with pytest.raises(recovery.RestoreFailed) as error:
        recovery.recover(env, 'new-secret', apply=True)
    message = str(error.value)
    assert 'restore failed' in message and 'backup' in message
    assert 'new-secret' not in message and 'stale' not in message and 'secret/path' not in message
    assert next(tmp_path.glob('.env.bak-*')).read_text() == original
    assert 'new-secret' in env.read_text()


def test_remote_main_reports_restore_failure_with_distinct_exit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(recovery, 'recover', MagicMock(side_effect=recovery.RestoreFailed('x')))
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'path': 'p', 'password': 'pw-secret', 'apply': True})))
    with pytest.raises(SystemExit) as exit_info:
        recovery.remote_main()
    assert exit_info.value.code == recovery.RESTORE_FAILED_EXIT
    err = capsys.readouterr().err
    assert 'protected backup' in err and 'pw-secret' not in err


@pytest.mark.parametrize('stdin', ['', '[]', '{}', '{"path": "p"}', 'null'])
def test_remote_main_malformed_payload_fails_fixed(monkeypatch, capsys, stdin):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(stdin))
    with pytest.raises(SystemExit) as exit_info:
        recovery.remote_main()
    assert exit_info.value.code == 1
    assert capsys.readouterr().err.startswith('Credential recovery failed')


def _run_main(monkeypatch, tmp_path, **result):
    env = tmp_path / '.env'
    env.write_text('PG_URL=postgresql://memory_user:private-candidate@localhost/db\n')
    monkeypatch.setenv('LOCAL_ENV', str(env))
    monkeypatch.setenv('VM_SSH', 'operator@example.invalid')
    monkeypatch.setattr('sys.argv', ['pg_credentials.py'])
    with patch.object(recovery.subprocess, 'run', return_value=MagicMock(**result)):
        with pytest.raises(SystemExit) as exit_info:
            recovery.main()
    return exit_info.value.code


@pytest.mark.parametrize('stdout', [
    'null', '[]', '"text"', '{}', '{"changed": "yes"}', 'not json', '',
    '{"credential_matches": true, "select_1": true}',
])
def test_malformed_remote_receipt_fails_fixed_without_traceback(tmp_path, monkeypatch, capsys, stdout):
    assert _run_main(monkeypatch, tmp_path, returncode=0, stdout=stdout) == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == 'Remote credential receipt was malformed; no restart attempted'
    assert captured.out == ''


def test_remote_restore_failure_tells_operator_to_use_protected_backup(tmp_path, monkeypatch, capsys):
    code = _run_main(monkeypatch, tmp_path, returncode=recovery.RESTORE_FAILED_EXIT, stdout='')
    assert code == 1
    err = capsys.readouterr().err
    assert 'restore failed' in err and 'protected backup' in err
    assert 'private-candidate' not in err


def test_receipt_prints_only_known_keys(tmp_path, monkeypatch, capsys):
    receipt = {'credential_matches': False, 'select_1': True, 'changed': False, 'extra': 'leak'}
    monkeypatch.setattr('sys.argv', ['pg_credentials.py'])
    env = tmp_path / '.env'
    env.write_text('PG_URL=postgresql://memory_user:private-candidate@localhost/db\n')
    monkeypatch.setenv('LOCAL_ENV', str(env))
    monkeypatch.setenv('VM_SSH', 'operator@example.invalid')
    with patch.object(recovery.subprocess, 'run',
                      return_value=MagicMock(returncode=0, stdout=json.dumps(receipt))):
        recovery.main()
    out = capsys.readouterr().out
    assert 'leak' not in out and 'select_1' in out


def test_missing_vm_ssh_fails_fixed_before_any_ssh(tmp_path, monkeypatch, capsys):
    env = tmp_path / '.env'
    env.write_text('PG_URL=postgresql://memory_user:private-candidate@localhost/db\n')
    monkeypatch.setenv('LOCAL_ENV', str(env))
    monkeypatch.delenv('VM_SSH', raising=False)
    monkeypatch.setattr('sys.argv', ['pg_credentials.py'])
    with patch.object(recovery.subprocess, 'run') as run:
        with pytest.raises(SystemExit):
            recovery.main()
    run.assert_not_called()
    assert 'VM_SSH' in capsys.readouterr().err


def test_local_only_probe_failure_is_bounded_and_fixed(tmp_path, monkeypatch, capsys, successful_select):
    env = tmp_path / '.env'
    env.write_text('PG_URL=postgresql://memory_user:private-candidate@localhost/db\n')
    monkeypatch.setenv('LOCAL_ENV', str(env))
    monkeypatch.setattr('sys.argv', ['pg_credentials.py', '--local-only'])
    successful_select.return_value = recovery._TIMEOUT_CODE
    with pytest.raises(SystemExit):
        recovery.main()
    err = capsys.readouterr().err
    assert 'timed out' in err and 'private-candidate' not in err


def test_ssh_target_is_separated_from_options(tmp_path, monkeypatch):
    """A VM_SSH value starting with '-' must not be parsed as an ssh option."""
    env = tmp_path / '.env'
    env.write_text('PG_URL=postgresql://memory_user:pw@localhost/db\n')
    monkeypatch.setenv('LOCAL_ENV', str(env))
    monkeypatch.setenv('VM_SSH', '-oProxyCommand=evil')
    monkeypatch.setattr('sys.argv', ['pg_credentials.py'])
    receipt = '{"credential_matches": true, "select_1": true, "changed": false}'
    with patch.object(recovery.subprocess, 'run', return_value=MagicMock(returncode=0, stdout=receipt)) as run:
        recovery.main()
    argv = run.call_args.args[0]
    assert argv[argv.index('-oProxyCommand=evil') - 1] == '--'


@pytest.mark.parametrize('kind', ['oserror', 'decode'])
def test_post_write_read_failure_restores_original(tmp_path, monkeypatch, kind):
    env = tmp_path / '.env'
    original = 'PG_URL=postgresql://memory_user:stale@192.0.2.215/db\n'
    env.write_text(original)
    if kind == 'oserror':
        def broken(self, *a, **k):
            raise OSError('/secret/path unreadable')
        monkeypatch.setattr(recovery.Path, 'read_text', broken)
    else:
        real = recovery._atomic_write
        calls = []

        def write(path, content):
            calls.append(path)
            real(path, content)
            if len(calls) == 1:
                path.write_bytes(b'\xff\xfe')
        monkeypatch.setattr(recovery, '_atomic_write', write)
    with pytest.raises(recovery.RecoveryError, match='original environment restored') as error:
        recovery.recover(env, 'working', apply=True)
    assert 'secret/path' not in str(error.value)
    assert env.read_bytes() == original.encode()
