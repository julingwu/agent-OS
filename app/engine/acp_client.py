"""ACP (Agent Client Protocol) client over stdio.

Measured on DSH 0.2.1-rc.2 (`dsh --profile acp`), newline-delimited JSON-RPC:

    -> initialize {protocolVersion, clientCapabilities}      <- agentInfo, capabilities
    -> session/new {cwd, mcpServers}                          <- {sessionId, configOptions}
    -> session/resume {sessionId, cwd}                        <- {configOptions}   (optional)
    -> session/prompt {sessionId, prompt:[{type:text,text}]}  <- {stopReason}
       <- notifications session/update:
            agent_thought_chunk | agent_message_chunk | tool_call | tool_call_update | usage_update

Two facts that matter:
  * stdout also carries plain-text diagnostics (e.g. `[llm-deepseek-account] ...`);
    a client that json.loads every line dies on the first one.
  * `usage_update.used` arrives **during** the turn, after each model call. That is
    the only pre-next-call signal the protocol exposes, so the budget gate is
    "cancel as soon as used crosses the cap" — it stops every subsequent call in
    the turn, but cannot un-send the call already in flight.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time


class AcpError(RuntimeError):
    pass


class AcpClient:
    def __init__(self, cmd: list[str], cwd: str, env: dict | None = None,
                 default_timeout: float = 600.0):
        self.cmd = cmd
        self.cwd = cwd
        self.env = dict(env or os.environ)
        self.default_timeout = default_timeout
        self._proc: subprocess.Popen | None = None
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._id = 0
        self._lock = threading.Lock()
        self.log: list[dict] = []
        self.stderr: list[str] = []
        self._pending: list[dict] = []      # notifications seen while awaiting a response
        self.on_started = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> dict:
        self._proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            cwd=self.cwd, env=self.env, shell=False,
        )
        if self.on_started is not None:
            self.on_started(self._proc)
        threading.Thread(target=self._read_stdout, name="acp-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="acp-err", daemon=True).start()
        return self.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
        }, timeout=90)

    def close(self, session_id: str | None = None, timeout: float = 5.0) -> None:
        """Release the session handle before the process goes away.

        `session/close` is advertised in sessionCapabilities; doing it keeps the
        next process able to `session/resume` the same id. Closing stdin is the
        graceful stop; a tree kill is the fallback, because Popen wraps a .cmd
        launcher whose electron child would otherwise survive and keep the lock.
        """
        if self._proc is None:
            return
        if session_id and self._proc.poll() is None:
            try:
                self.request("session/close", {"sessionId": session_id}, timeout=timeout)
            except (AcpError, OSError, ValueError):
                pass
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self._proc.pid)],
                               capture_output=True, check=False)
            else:
                self._proc.kill()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    # ------------------------------------------------------------ transport
    def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self.log.append({"type": "raw", "text": line[:400]})
                continue
            self.log.append(msg)
            if msg.get("method") and msg.get("id") is not None:
                # Server -> client request (none observed in DSH, refuse politely).
                self._write({"jsonrpc": "2.0", "id": msg["id"],
                             "error": {"code": -32601, "message": "client method not supported"}})
                continue
            self._q.put(msg)

    def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self.stderr.append(line.rstrip()[:400])
            if len(self.stderr) > 400:
                self.stderr.pop(0)

    def _write(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _next_id(self) -> int:
        with self._lock:
            self._id += 1
            return self._id

    def request(self, method: str, params: dict | None = None,
                timeout: float | None = None) -> dict:
        rid = self._next_id()
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        end = time.time() + (timeout if timeout is not None else self.default_timeout)
        while time.time() < end:
            try:
                msg = self._q.get(timeout=0.2)
            except queue.Empty:
                if self._proc and self._proc.poll() is not None:
                    raise AcpError(f"agent exited before answering {method}: {self.stderr[-3:]}")
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise AcpError(f"{method} failed: {msg['error']}")
                return msg.get("result") or {}
            # A notification arrived while awaiting a response: stash it instead of
            # re-queueing, otherwise the same message spins until the timeout.
            self._pending.append(msg)
        raise AcpError(f"timeout waiting for {method}")

    # ------------------------------------------------------------ protocol
    def new_session(self, cwd: str | None = None) -> dict:
        return self.request("session/new", {"cwd": cwd or self.cwd, "mcpServers": []}, timeout=90)

    def resume(self, session_id: str, cwd: str | None = None) -> dict:
        return self.request("session/resume", {"sessionId": session_id, "cwd": cwd or self.cwd},
                            timeout=90)

    def cancel(self, session_id: str) -> None:
        self._write({"jsonrpc": "2.0", "method": "session/cancel",
                     "params": {"sessionId": session_id}})
        self.log.append({"type": "client", "method": "session/cancel"})

    def prompt(self, session_id: str, text: str, on_update=None,
               timeout: float | None = None) -> dict:
        rid = self._next_id()
        self._write({"jsonrpc": "2.0", "id": rid, "method": "session/prompt",
                     "params": {"sessionId": session_id,
                                "prompt": [{"type": "text", "text": text}]}})
        end = time.time() + (timeout if timeout is not None else self.default_timeout)
        updates = 0
        while time.time() < end:
            try:
                msg = self._q.get(timeout=0.2)
            except queue.Empty:
                if self._proc and self._proc.poll() is not None:
                    raise AcpError(f"agent exited mid-turn: {self.stderr[-3:]}")
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise AcpError(f"session/prompt failed: {msg['error']}")
                return {"result": msg.get("result") or {}, "updates": updates}
            if msg.get("method") == "session/update":
                updates += 1
                update = (msg.get("params") or {}).get("update") or {}
                if on_update is not None:
                    on_update(update)
        raise AcpError("prompt timed out")


def client_for(cli_path: str, cwd: str, env: dict | None = None) -> AcpClient:
    return AcpClient([cli_path, "--profile", "acp"], cwd=cwd, env=env)
