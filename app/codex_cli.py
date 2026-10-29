"""Codex CLI inference adapter; application-owned tools stay in agent/tools.py."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
import queue
import threading
import time
import copy
from jsonschema import validate, ValidationError


def _strict_parameters(parameters):
    """Optional tool parameters use null on the wire, then are omitted at dispatch."""
    result = copy.deepcopy(parameters)
    if result.get('type') == 'object':
        properties = result.setdefault('properties', {})
        required = result.get('required', [])
        for key, value in list(properties.items()):
            value = _strict_parameters(value)
            properties[key] = value if key in required else {'anyOf': [value, {'type': 'null'}]}
        result['required'] = list(properties)
        result['additionalProperties'] = False
    elif result.get('type') == 'array':
        result['items'] = _strict_parameters(result['items'])
    return result


def _output_schema(specs):
    variants = [{'type': 'object', 'additionalProperties': False,
                 'properties': {'name': {'type': 'string', 'enum': [name]},
                                'arguments': _strict_parameters(spec['parameters'])},
                 'required': ['name', 'arguments']} for name, spec in specs.items()]
    calls = {'type': 'array', 'items': {'anyOf': variants}} if variants else {
        'type': 'array', 'items': {'type': 'string'}, 'maxItems': 0}
    return {'type': 'object', 'additionalProperties': False,
            'properties': {'content': {'type': 'string'}, 'tool_calls': calls},
            'required': ['content', 'tool_calls']}

_catalog_lock = threading.Lock()
_catalog_cache = None
_catalog_time = 0.0


def catalog() -> list[dict]:
    """Read CLI metadata, never its auth file. Catalog is not an entitlement check."""
    global _catalog_cache, _catalog_time
    if os.environ.get('M0_MOCK_LLM') == '1':
        raise RuntimeError('离线测试不查询真实 Codex 目录。')
    with _catalog_lock:
        if _catalog_cache is not None and time.monotonic() - _catalog_time < 60:
            return _catalog_cache
        process = subprocess.Popen(
            [executable(), 'app-server', '--listen', 'stdio://'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', env=environment(),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        lines = queue.Queue()
        def read():
            for line in process.stdout:
                lines.put(line)
            lines.put(None)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        def rpc(number, method, params):
            process.stdin.write(json.dumps({'id': number, 'method': method, 'params': params}) + '\n')
            process.stdin.flush()
            deadline = time.monotonic() + 15
            while True:
                line = lines.get(timeout=max(0.01, deadline-time.monotonic()))
                if line is None:
                    raise RuntimeError('Codex 模型目录连接关闭。')
                data = json.loads(line)
                if data.get('id') == number:
                    if 'error' in data:
                        raise RuntimeError('Codex 模型目录查询失败。')
                    return data['result']
        try:
            rpc(1, 'initialize', {'clientInfo': {'name': 'agent_os', 'version': '0.1'}})
            process.stdin.write('{"method":"initialized"}\n')
            process.stdin.flush()
            models = []
            cursor = None
            for number in range(2, 12):
                page = rpc(number, 'model/list', {'limit': 100, 'includeHidden': False, 'cursor': cursor})
                models.extend(page['data'])
                cursor = page.get('nextCursor')
                if not cursor:
                    break
            _catalog_cache = [{'model': m['model'], 'label': m.get('displayName') or m['model'],
                               'default_effort': m.get('defaultReasoningEffort'),
                               'efforts': [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])]}
                              for m in models]
            _catalog_time = time.monotonic()
            return _catalog_cache
        finally:
            process.kill()
            process.wait(timeout=5)
            reader.join(timeout=1)
            process.stdin.close()
            process.stdout.close()


def model_options() -> list[dict]:
    levels = {'low': '低', 'medium': '中', 'high': '高', 'xhigh': '超高',
              'max': '最大', 'ultra': 'Ultra', 'none': '无', 'minimal': '最小'}
    items = []
    for m in catalog():
        base = 'codex-cli/' + m['model']
        items.append({'id': base, 'label': f"Codex · {m['label']} · 默认推理",
                      'model': m['model'], 'reasoning_effort': None})
        for effort in m['efforts']:
            items.append({'id': base + '#' + effort,
                          'label': f"Codex · {m['label']} · {levels.get(effort, effort)} ({effort})",
                          'model': m['model'], 'reasoning_effort': effort})
    return items


def selection(model):
    if not model or model == 'codex-cli':
        return None, None
    name, sep, effort = model.partition('/')[2].partition('#')
    if not name or (sep and effort not in {
            'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}):
        raise RuntimeError('Codex 模型或推理级别无效。')
    if sep:
        entry = next((m for m in catalog() if m['model'] == name), None)
        if entry is None or effort not in entry['efforts']:
            raise RuntimeError('该 Codex 模型不支持所选推理级别。')
    return name, effort if sep else None


def home() -> Path:
    path = Path(os.environ.get('AGENTOS_CODEX_HOME') or
                Path(__file__).resolve().parents[1] / 'data' / 'codex-cli').resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def environment() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('CODEX_', 'OPENAI_', 'DEEPSEEK_', 'LLM_'))
           and not any(s in k.upper() for s in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY'))}
    env['CODEX_HOME'] = str(home())
    return env


def executable() -> str:
    binary = os.environ.get('CODEX_CLI_BIN') or shutil.which('codex')
    if not binary:
        raise RuntimeError('Codex CLI 未安装或不在服务进程 PATH 中。')
    return binary


def selected(model: str | None) -> bool:
    return bool(model == 'codex-cli' or (model or '').startswith('codex-cli/'))


def chat(messages: list[dict], *, tools=None, model=None, timeout=300) -> dict:
    model_name, effort = selection(model)
    specs = {s['function']['name']: s['function'] for s in (tools or [])}
    schema = _output_schema(specs)
    prompt = ('You are the inference backend of Agent OS. Read the supplied conversation in order. '
              'Return a final answer or request application tools using the output schema. '
              'Do not run any Codex built-in tool, command, file operation or web search. '
              'Only names in application_tools are valid. arguments must be a JSON object, '
              'not a JSON-encoded string. Use null for unused optional parameters. '
              'Do not claim tools succeeded until their results appear in history.\n' +
              json.dumps({'conversation': messages, 'application_tools': list(specs.values())}, ensure_ascii=False))
    with tempfile.TemporaryDirectory(prefix='agentos-codex-') as tmp:
        root = Path(tmp)
        (root / 'schema.json').write_text(json.dumps(schema), encoding='utf-8')
        command = [executable(), 'exec', '--ignore-user-config', '--ignore-rules',
                   '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only',
                   '--disable', 'shell_tool', '--disable', 'multi_agent', '--disable', 'plugins',
                   '-c', 'web_search="disabled"', '-c', 'tools.view_image=false',
                   '--json', '--color', 'never', '--output-schema', str(root / 'schema.json'),
                   '--output-last-message', str(root / 'answer.json'), '-C', tmp]
        if model_name:
            command += ['--model', model_name]
        if effort:
            command += ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
        command += ['-']
        try:
            result = subprocess.run(command, input=prompt, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=timeout,
                                    env=environment(), cwd=tmp,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('Codex CLI 请求超时；本次未自动重试。') from exc
        if result.returncode:
            # Never forward CLI stderr: it can contain account or credential details.
            raise RuntimeError('Codex CLI 调用失败：请检查专用登录状态、账号额度或模型权限。')
        usage = {}
        completed = False
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') in ('turn.failed', 'error'):
                raise RuntimeError('Codex CLI 返回执行失败；请检查登录状态或账号额度。')
            if event.get('type') == 'item.completed' and event.get('item', {}).get('type') in (
                    'command_execution', 'file_change', 'mcp_tool_call', 'web_search'):
                raise RuntimeError('Codex CLI 使用了应用外工具，本轮结果已拒绝。')
            if event.get('type') == 'turn.completed':
                completed = True
                usage = event.get('usage') or {}
        if not completed:
            raise RuntimeError('Codex CLI 未返回完成事件，不能认定本轮成功。')
        try:
            data = json.loads((root / 'answer.json').read_text(encoding='utf-8'))
            if not isinstance(data['content'], str) or not isinstance(data['tool_calls'], list):
                raise ValueError()
            calls = []
            for call in data['tool_calls']:
                arguments = call['arguments']
                if call['name'] not in specs or not isinstance(arguments, dict):
                    raise ValueError()
                parameters = specs[call['name']]['parameters']
                required = parameters.get('required', [])
                arguments = {k: v for k, v in arguments.items() if v is not None or k in required}
                # Enforce the same schema locally before returning any tool for dispatch.
                validate(arguments, parameters)
                calls.append({'id': 'codex_' + uuid.uuid4().hex, 'type': 'function',
                              'function': {'name': call['name'], 'arguments': json.dumps(arguments, ensure_ascii=False)}})
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            raise RuntimeError('Codex CLI 返回格式或工具请求无效；未执行任何应用工具。') from exc
        return {'message': {'role': 'assistant', 'content': data['content'], 'tool_calls': calls},
                'usage': {'prompt_tokens': int(usage.get('input_tokens') or 0),
                          'completion_tokens': int(usage.get('output_tokens') or 0)}}
