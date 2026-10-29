import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from app import codex_cli, llm


SPECS = [{'type': 'function', 'function': {'name': 'lookup', 'description': 'Lookup',
          'parameters': {'type': 'object', 'properties': {}}}}]


def fake_cli(monkeypatch, tmp_path, response=None, events=None, code=0):
    monkeypatch.setenv('AGENTOS_CODEX_HOME', str(tmp_path / 'auth'))
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret-do-not-forward')
    monkeypatch.setattr(codex_cli, 'executable', lambda: 'codex.exe')
    seen = []
    def run(command, **kw):
        seen.append((command, kw))
        assert kw['env']['CODEX_HOME'] == str(tmp_path / 'auth')
        assert 'OPENAI_API_KEY' not in kw['env']
        assert '--ignore-user-config' in command and '--ephemeral' in command
        assert command[command.index('--sandbox') + 1] == 'read-only'
        assert 'shell_tool' in command and 'multi_agent' in command
        assert kw.get('shell') is not True
        Path(command[command.index('--output-last-message') + 1]).write_text(
            json.dumps(response or {'content': '你好', 'tool_calls': []}), encoding='utf-8')
        return SimpleNamespace(returncode=code, stderr='sensitive-error', stdout='\n'.join(
            json.dumps(e) for e in (events or [{'type': 'turn.completed',
                'usage': {'input_tokens': 19, 'output_tokens': 7}}])))
    monkeypatch.setattr(codex_cli.subprocess, 'run', run)
    return seen


def test_text_usage_and_isolated_command(monkeypatch, tmp_path):
    seen = fake_cli(monkeypatch, tmp_path)
    result = codex_cli.chat([{'role': 'user', 'content': '你好'}], model='codex-cli/example')
    assert result['message']['content'] == '你好'
    assert result['usage'] == {'prompt_tokens': 19, 'completion_tokens': 7}
    assert seen[0][0][seen[0][0].index('--model') + 1] == 'example'
    assert '你好' in seen[0][1]['input']


def test_tool_roundtrip(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, {'content': '', 'tool_calls': [
        {'name': 'lookup', 'arguments': {'query': '测试'}}]})
    result = codex_cli.chat([], tools=SPECS)
    call = result['message']['tool_calls'][0]
    assert call['type'] == 'function' and call['id'].startswith('codex_')
    assert json.loads(call['function']['arguments']) == {'query': '测试'}


@pytest.mark.parametrize('call', [{'name': 'unauthorized', 'arguments': {}},
                                 {'name': 'lookup', 'arguments': []},
                                 {'name': 'lookup', 'arguments': '{}'}])
def test_reject_invalid_tools(monkeypatch, tmp_path, call):
    fake_cli(monkeypatch, tmp_path, {'content': '', 'tool_calls': [call]})
    with pytest.raises(RuntimeError, match='工具请求无效'):
        codex_cli.chat([], tools=SPECS)


def test_no_secret_error_forwarding(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, code=1)
    with pytest.raises(RuntimeError) as error:
        codex_cli.chat([])
    assert 'sensitive-error' not in str(error.value)


def test_structured_windows_arguments_omit_unused_optionals(monkeypatch, tmp_path):
    specs = [{'type': 'function', 'function': {'name': 'local_exec', 'parameters': {
        'type': 'object', 'properties': {'cmd': {'type': 'string'}, 'cwd': {'type': 'string'},
                                       'timeout_s': {'type': 'integer'}}, 'required': ['cmd']}}}]
    command = r'"C:\Program Files\Python\python.exe" -c "print(12345)"'
    fake_cli(monkeypatch, tmp_path, {'content': '', 'tool_calls': [{
        'name': 'local_exec', 'arguments': {'cmd': command, 'cwd': None, 'timeout_s': 10}}]})
    result = codex_cli.chat([], tools=specs)
    assert json.loads(result['message']['tool_calls'][0]['function']['arguments']) == {
        'cmd': command, 'timeout_s': 10}


def test_structured_parameter_type_error_is_redacted(monkeypatch, tmp_path):
    specs = [{'type': 'function', 'function': {'name': 'test', 'parameters': {
        'type': 'object', 'properties': {'timeout_s': {'type': 'integer'}},
        'required': ['timeout_s']}}}]
    fake_cli(monkeypatch, tmp_path, {'content': '', 'tool_calls': [{
        'name': 'test', 'arguments': {'timeout_s': 'private-value-do-not-expose'}}]})
    with pytest.raises(RuntimeError, match='工具请求无效') as error:
        codex_cli.chat([], tools=specs)
    assert 'private-value' not in str(error.value)


def test_cli_timeout(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path)
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired('codex', 1)
    monkeypatch.setattr(codex_cli.subprocess, 'run', timeout)
    with pytest.raises(RuntimeError, match='超时'):
        codex_cli.chat([])


def test_native_tool_result_denied(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, events=[{'type': 'item.completed',
             'item': {'type': 'command_execution'}}])
    with pytest.raises(RuntimeError, match='应用外工具'):
        codex_cli.chat([])


def test_missing_completion_denied(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, events=[{'type': 'thread.started'}])
    with pytest.raises(RuntimeError, match='完成事件'):
        codex_cli.chat([])


def test_model_catalog_only_emits_supported_combinations(monkeypatch):
    monkeypatch.setattr(codex_cli, 'catalog', lambda: [
        {'model': 'test-6.1', 'label': 'Test 6.1', 'efforts': ['low', 'high']}])
    items = codex_cli.model_options()
    assert [i['id'] for i in items] == [
        'codex-cli/test-6.1', 'codex-cli/test-6.1#low', 'codex-cli/test-6.1#high']


def test_explicit_model_and_reasoning_reach_cli(monkeypatch, tmp_path):
    seen = fake_cli(monkeypatch, tmp_path)
    monkeypatch.setattr(codex_cli, 'catalog', lambda: [
        {'model': 'test-6.1', 'efforts': ['low', 'high']}])
    codex_cli.chat([], model='codex-cli/test-6.1#high')
    args = seen[0][0]
    assert args[args.index('--model') + 1] == 'test-6.1'
    assert 'model_reasoning_effort="high"' in args


def test_unsupported_reasoning_rejected_before_spawn(monkeypatch):
    monkeypatch.setattr(codex_cli, 'catalog', lambda: [{'model': 'test', 'efforts': ['low']}])
    with pytest.raises(RuntimeError, match='不支持'):
        codex_cli.selection('codex-cli/test#ultra')


def test_offline_gate(monkeypatch):
    monkeypatch.setenv('M0_LLM_MODE', 'off')
    monkeypatch.setattr(codex_cli, 'chat', lambda *a, **kw: pytest.fail('offline spawned CLI'))
    assert llm.chat([], model='codex-cli') is None
    with pytest.raises(RuntimeError, match='llm_offline'):
        list(llm.chat_stream([], model='codex-cli'))


def test_agent_failure_does_not_retry_cli(monkeypatch):
    from app.agent.loop import _chat_streamed
    calls = []
    def fail(*a, **kw):
        calls.append(1)
        raise RuntimeError('login required')
    monkeypatch.setattr(llm, 'chat', fail)
    with pytest.raises(RuntimeError, match='login required'):
        _chat_streamed(None, [], bot_id='b', session_id='s', turn_id='t', model='codex-cli')
    assert len(calls) == 1


def test_cli_wait_emits_progress_without_retry(monkeypatch):
    from app.agent import loop
    import concurrent.futures
    class Pending:
        reads = 0
        def result(self, timeout):
            self.reads += 1
            if self.reads == 1:
                raise concurrent.futures.TimeoutError()
            return {'message': {'content': 'done'}}
        def done(self):
            return False
    pending = Pending()
    class Pool:
        def __init__(self, **kw): pass
        def submit(self, *args, **kw): return pending
        def shutdown(self, **kw): pass
    monkeypatch.setattr(concurrent.futures, 'ThreadPoolExecutor', Pool)
    events = []
    monkeypatch.setattr(loop, '_emit', lambda c, **kw: events.append(kw))
    out = loop._chat_streamed(None, [], bot_id='b', session_id='s', turn_id='t', model='codex-cli')
    assert out['message']['content'] == 'done'
    assert len(events) == 1 and events[0]['kind'] == 'thought.delta'
