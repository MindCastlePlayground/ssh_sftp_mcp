from __future__ import annotations

import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ssh_sftp_mcp.constants import CommandStatus, ExecMode, Field, JsonKey, Tool
from ssh_sftp_mcp.session import SessionManager
from ssh_sftp_mcp.store import ServerStore
from ssh_sftp_mcp.tools import ToolService


class EnvTest:
    user = "u"
    password = "p"
    key = "CODEX_ENV_KEEP"
    value = "env_kept_20260626"
    timeout = 5
    poll = 0.05


class TestSshServer(paramiko.ServerInterface):
    def __init__(self):
        self.ready = threading.Event()

    def check_auth_password(self, username, password):
        if username == EnvTest.user and password == EnvTest.password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        return True

    def check_channel_shell_request(self, channel):
        self.ready.set()
        return True


class LocalSshServer:
    def __init__(self):
        self.env = {}
        self.key = paramiko.RSAKey.generate(1024)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
        except OSError:
            pass
        self.sock.close()
        self.thread.join(timeout=2)

    def run(self):
        while not self.stop.is_set():
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            if self.stop.is_set():
                client.close()
                return
            threading.Thread(target=self.handle, args=(client,), daemon=True).start()

    def handle(self, client):
        transport = paramiko.Transport(client)
        transport.add_server_key(self.key)
        server = TestSshServer()
        try:
            transport.start_server(server=server)
            chan = transport.accept(5)
            if chan is None or not server.ready.wait(5):
                return
            self.loop(chan)
        finally:
            transport.close()

    def loop(self, chan):
        buf = ""
        while not self.stop.is_set() and not chan.closed:
            data = chan.recv(4096)
            if not data:
                break
            buf += data.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                self.reply(chan, line.strip("\r"))

    def reply(self, chan, line):
        if not line or line.startswith("stty "):
            return
        start = re.search(r"(__MCP_CMD_[0-9a-f]+_START__)", line)
        end = re.search(r"(__MCP_CMD_[0-9a-f]+_END__)", line)
        body = re.search(r"; \( (.*?) \); __mcp_code=", line)
        out = ""
        code = 0
        if body:
            out, code = self.exec(body.group(1).strip())
        if start:
            chan.send("\n" + start.group(1) + "\n")
        if out:
            chan.send(out)
        if end:
            chan.send("\n" + end.group(1) + f":{code}\n")

    def exec(self, cmd):
        if cmd.startswith("export "):
            key, value = cmd[len("export "):].split("=", 1)
            self.env[key.strip()] = value.strip().strip("\"'")
            return "", 0
        if cmd.startswith("printf "):
            arg = cmd[len("printf "):].strip().strip("\"'")
            if arg.startswith("$"):
                return self.env.get(arg[1:], ""), 0
            return arg, 0
        if cmd.startswith("echo "):
            arg = cmd[len("echo "):].strip()
            if arg.startswith("$"):
                return self.env.get(arg[1:], "") + "\n", 0
            return arg + "\n", 0
        return "", 127


class McpClient:
    def __init__(self, port):
        self.port = port
        self.tmp = tempfile.TemporaryDirectory()
        self.service = ToolService(SessionManager(ServerStore(Path(self.tmp.name))))

    def close(self):
        self.tmp.cleanup()

    def call(self, name, args):
        res = self.service.call(name, args)
        if res[JsonKey.is_error]:
            raise RuntimeError(res[JsonKey.content][0][JsonKey.text])
        return res[JsonKey.structured]

    def open(self):
        self.call(Tool.register, {
            Field.ip: "127.0.0.1",
            Field.port: self.port,
            Field.username: EnvTest.user,
            Field.password: EnvTest.password,
        })
        return self.call(Tool.connect, {Field.ip: "127.0.0.1"})[Field.session_id]

    def send(self, sid, text):
        data = self.call(Tool.send, {
            Field.session_id: sid,
            Field.input: text,
            Field.mode: ExecMode.host.value,
        })
        return self.wait(sid, data[Field.command_id])

    def wait(self, sid, cid):
        cursor = 0
        parts = []
        deadline = time.time() + EnvTest.timeout
        while time.time() < deadline:
            data = self.call(Tool.output, {
                Field.session_id: sid,
                Field.command_id: cid,
                Field.cursor: cursor,
            })
            cursor = data[Field.next_cursor]
            if data.get(Field.output):
                parts.append(data[Field.output])
            status = self.call(Tool.status, {
                Field.session_id: sid,
                Field.command_id: cid,
            })
            cmd = status[Field.command]
            if cmd[Field.status] == CommandStatus.done.value:
                data = self.call(Tool.output, {
                    Field.session_id: sid,
                    Field.command_id: cid,
                    Field.cursor: cursor,
                })
                if data.get(Field.output):
                    parts.append(data[Field.output])
                return int(cmd[Field.exit_code]), "".join(parts)
            time.sleep(EnvTest.poll)
        raise TimeoutError(cid)

    def close_session(self, sid):
        self.call(Tool.close, {Field.ip: "127.0.0.1", Field.session_id: sid})


class Main:
    @staticmethod
    def run():
        server = LocalSshServer()
        server.start()
        client = McpClient(server.port)
        sid = ""
        try:
            sid = client.open()
            export_code, _ = client.send(sid, f"export {EnvTest.key}={EnvTest.value}")
            read_code, output = client.send(sid, f'printf "${EnvTest.key}"')
            if sid:
                client.close_session(sid)
            kept = export_code == 0 and read_code == 0 and output == EnvTest.value
            print({
                Field.session_id: sid,
                "export_exit": export_code,
                "read_exit": read_code,
                Field.output: output,
                "kept": kept,
            })
            raise SystemExit(0 if kept else 1)
        finally:
            client.close()
            server.close()


Main.run()
