"""Fixed local stdio integration; never accepts commands from tool arguments."""
import json
import os
import re
import subprocess
from pathlib import Path

NAME = 'homelab-unified'
URL = 'stdio://homelab-unified'
CONFIG = Path(__file__).resolve().parents[1] / 'data' / 'homelab-agentos.json'
MAX_BYTES = 1024 * 1024


def config():
    value = json.loads(CONFIG.read_text(encoding='utf-8'))
    directory = Path(value['directory'])
    if not directory.is_absolute() or directory.is_symlink():
        raise ValueError('invalid_local_config')
    for name in ['homelab_unified.py', 'homelab_message_capture.py', 'credential_capture.py', 'vaultwarden_provider.py', 'homelab_memory_bridge.py', 'homelab_access_bridge.py', 'vaultwarden_ingest.py']:
        file = directory / name
        if not file.is_file() or file.is_symlink():
            raise ValueError('untrusted_local_module')
    return value


def invoke(command, payload, timeout=60):
    encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    if len(encoded) > MAX_BYTES:
        raise ValueError('local_input_limit')
    result = subprocess.run(command, input=encoded, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or len(result.stdout) > MAX_BYTES:
        raise ValueError('local_helper_failed')
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or value.get('ok') is not True:
        raise ValueError('local_helper_failed')
    return value


def capture(text, session_id):
    try:
        cfg = config()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session_id):
            raise ValueError('invalid_session')
        result = invoke([cfg['python'], '-I', str(Path(cfg['directory'])/'homelab_message_capture.py')],
                        {'action':'capture','session_id':session_id,'parts':[{'type':'text','text':text}]})
        if not result.get('captured'):
            return text
        parts = result['parts']
        if not parts or not isinstance(parts[0].get('text'), str):
            raise ValueError('invalid_capture_result')
        return parts[0]['text']
    except Exception:
        raise RuntimeError('homelab_capture_failed') from None


def rpc(method, params=None):
    try:
        cfg = config()
        directory = Path(cfg['directory'])
        command = [cfg['python'], '-I', str(directory/'homelab_unified.py'),
                   '--memory-dir', str(directory), '--memory-config', cfg['memory_config'],
                   '--access-dir', str(directory), '--access-config', cfg['access_config'], '--vault-dir', str(directory)]
        requests = [dict(jsonrpc='2.0',id=1,method='initialize',params={'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'agentos','version':'0.1.0'}}),
                    dict(jsonrpc='2.0',method='notifications/initialized'),
                    dict(jsonrpc='2.0',id=2,method=method,params=params or {})]
        data = ('\n'.join(json.dumps(r) for r in requests)+'\n').encode('utf-8')
        if len(data) > MAX_BYTES: raise ValueError('input_limit')
        done = subprocess.run(command,input=data,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=60,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if done.returncode or len(done.stdout)>MAX_BYTES: raise ValueError('stdio_failed')
        responses = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
        response = next(r for r in responses if r.get('id')==2)
        if 'error' in response: return {'ok':False,'error':'homelab_rpc_failed'}
        return {'ok':True,'result':response['result']}
    except Exception:
        return {'ok':False,'error':'homelab_stdio_failed'}
