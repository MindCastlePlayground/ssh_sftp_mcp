from __future__ import annotations

import ipaddress
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ssh_sftp_mcp.constants import CommandStatus, ExecMode, Field, JsonKey, LiveConfigKey, LiveEnv, LiveMarker, LiveName, LiveRole, SessionStatus, Text, Tool
from ssh_sftp_mcp.models import ServerInfo
from ssh_sftp_mcp.session import SessionManager
from ssh_sftp_mcp.store import ServerStore
from ssh_sftp_mcp.tools import ToolService


class LiveDefault:
    config = r"H:\Documents\ssh-server.json"
    enabled = "1"
    timeout = 90.0
    long_timeout = 900.0
    poll = 0.2


@dataclass
class LiveServer:
    info: ServerInfo
    role: str

    @classmethod
    def make(cls, row: dict) -> "LiveServer":
        ip = str(row[LiveConfigKey.host])
        info = ServerInfo(
            ip=ip,
            port=int(row[LiveConfigKey.port]),
            username=str(row[LiveConfigKey.user]),
            password=str(row[Field.password]),
        )
        role = LiveRole.private if ipaddress.ip_address(ip).is_private else LiveRole.public
        return cls(info, role)


class PsCred:
    @staticmethod
    def exe() -> str:
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if not exe:
            raise unittest.SkipTest("PowerShell is required to decrypt credentialXml")
        return exe

    @classmethod
    def load(cls, path: Path) -> list[LiveServer]:
        path_text = json.dumps(str(path))
        script = """
$ErrorActionPreference = 'Stop'
$path = __CONFIG_PATH__
$data = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
$out = @()
foreach ($item in $data) {
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        Set-Content -LiteralPath $tmp -Value $item.credentialXml -Encoding UTF8
        $cred = Import-Clixml -LiteralPath $tmp
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($cred.Password)
        try {
            $pass = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
        $out += [pscustomobject]@{
            host = $item.host
            user = $item.user
            port = [int]$item.port
            password = $pass
        }
    } finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}
$out | ConvertTo-Json -Depth 4 -Compress
""".replace("__CONFIG_PATH__", path_text)
        proc = subprocess.run(
            [cls.exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or "credentialXml decrypt failed")
        data = json.loads(proc.stdout)
        rows = data if isinstance(data, list) else [data]
        return [LiveServer.make(row) for row in rows]


class RemoteCmd:
    @staticmethod
    def shell(script: str) -> str:
        return f"sh -lc {shlex.quote(script)}"

    @staticmethod
    def install_docker() -> str:
        return RemoteCmd.shell("""
set -eu
if ! command -v docker >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y docker.io
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y docker
    elif command -v yum >/dev/null 2>&1; then
        yum install -y docker
    else
        echo no-supported-package-manager >&2
        exit 1
    fi
fi
(systemctl enable --now docker || systemctl start docker || service docker start || true) >/dev/null 2>&1
docker version >/dev/null
""")

    @staticmethod
    def install_psql() -> str:
        return RemoteCmd.shell("""
set -eu
if ! command -v psql >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y postgresql-client
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y postgresql
    elif command -v yum >/dev/null 2>&1; then
        yum install -y postgresql
    else
        echo no-supported-package-manager >&2
        exit 1
    fi
fi
psql --version >/dev/null
""")

    @staticmethod
    def start_pg() -> str:
        return RemoteCmd.shell(f"""
set -eu
docker rm -f {shlex.quote(LiveName.pg_container)} >/dev/null 2>&1 || true
docker run -d --name {shlex.quote(LiveName.pg_container)} \\
    -e POSTGRES_USER={shlex.quote(LiveName.pg_user)} \\
    -e POSTGRES_PASSWORD={shlex.quote(LiveName.pg_password)} \\
    -e POSTGRES_DB={shlex.quote(LiveName.pg_db)} \\
    -p 127.0.0.1:{LiveName.pg_port}:5432 \\
    {shlex.quote(LiveName.pg_image)} >/dev/null
for i in $(seq 1 90); do
    if docker exec {shlex.quote(LiveName.pg_container)} pg_isready -U {shlex.quote(LiveName.pg_user)} -d {shlex.quote(LiveName.pg_db)} >/dev/null 2>&1; then
        exit 0
    fi
    sleep 1
done
docker logs {shlex.quote(LiveName.pg_container)} >&2 || true
exit 1
""")

    @staticmethod
    def stop_pg() -> str:
        return RemoteCmd.shell(f"docker rm -f {shlex.quote(LiveName.pg_container)} >/dev/null 2>&1 || true")

    @staticmethod
    def psql(sql: str, port: bool = True) -> str:
        port_arg = f"-p {LiveName.pg_port}" if port else ""
        return (
            f"PGPASSWORD={shlex.quote(LiveName.pg_password)} "
            f"psql -h 127.0.0.1 {port_arg} -U {shlex.quote(LiveName.pg_user)} "
            f"-d {shlex.quote(LiveName.pg_db)} -tAc {shlex.quote(sql)}"
        )

    @staticmethod
    def psql_repl() -> str:
        inner = (
            f"PGPASSWORD={shlex.quote(LiveName.pg_password)} "
            f"psql -h 127.0.0.1 -U {shlex.quote(LiveName.pg_user)} "
            f"-d {shlex.quote(LiveName.pg_db)}"
        )
        return f"docker exec -i {shlex.quote(LiveName.pg_container)} sh -lc {shlex.quote(inner)}"


class LiveClient:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = ToolService(SessionManager(ServerStore(Path(self.tmp.name))))

    def close(self) -> None:
        self.tmp.cleanup()

    def call(self, name: str, args: dict) -> dict:
        res = self.service.call(name, args)
        if res[JsonKey.is_error]:
            reason = res[JsonKey.structured].get(Field.reason, "tool failed")
            raise RuntimeError(reason)
        return res[JsonKey.structured]

    def reg(self, server: LiveServer) -> None:
        self.call(Tool.register, server.info.data())

    def once(self, server: LiveServer, command: str, timeout: float = LiveDefault.timeout) -> dict:
        old = LiveDefault.timeout
        LiveDefault.timeout = timeout
        try:
            data = self.call(Tool.once, {
                Field.ip: server.info.ip,
                Field.port: server.info.port,
                Field.username: server.info.username,
                Field.password: server.info.password,
                Field.command: command,
            })
            if not data[Field.ok]:
                raise RuntimeError(data.get(Field.stderr) or data.get(Field.output) or data[Field.command])
            return data
        finally:
            LiveDefault.timeout = old

    def open(self, server: LiveServer) -> str:
        data = self.call(Tool.connect, {Field.ip: server.info.ip})
        self.assert_connected(data)
        return data[Field.session_id]

    def close_session(self, server: LiveServer, sid: str) -> None:
        self.call(Tool.close, {Field.ip: server.info.ip, Field.session_id: sid})

    def send(self, sid: str, text: str, mode: ExecMode = ExecMode.host, opts: dict | None = None) -> dict:
        args = {Field.session_id: sid, Field.input: text, Field.mode: mode.value}
        if opts:
            args.update(opts)
        return self.call(Tool.send, args)

    def wait(self, sid: str, cid: str, timeout: float = LiveDefault.timeout) -> str:
        cursor = 0
        parts = []
        end = time.time() + timeout
        while time.time() < end:
            data = self.call(Tool.output, {Field.session_id: sid, Field.command_id: cid, Field.cursor: cursor})
            cursor = data[Field.next_cursor]
            if data[Field.output]:
                parts.append(data[Field.output])
            status = self.call(Tool.status, {Field.session_id: sid, Field.command_id: cid})
            command = status[Field.command]
            if command[Field.status] in (CommandStatus.done.value, CommandStatus.failed.value):
                data = self.call(Tool.output, {Field.session_id: sid, Field.command_id: cid, Field.cursor: cursor})
                if data[Field.output]:
                    parts.append(data[Field.output])
                if command[Field.status] == CommandStatus.failed.value:
                    raise RuntimeError(command[Field.reason])
                return "".join(parts)
            time.sleep(LiveDefault.poll)
        raise TimeoutError(cid)

    def until(self, sid: str, cid: str, text: str, timeout: float = LiveDefault.timeout) -> str:
        cursor = 0
        parts = []
        end = time.time() + timeout
        while time.time() < end:
            data = self.call(Tool.output, {Field.session_id: sid, Field.command_id: cid, Field.cursor: cursor})
            cursor = data[Field.next_cursor]
            if data[Field.output]:
                parts.append(data[Field.output])
            output = "".join(parts)
            if text in output:
                return output
            time.sleep(LiveDefault.poll)
        raise TimeoutError(text)

    @staticmethod
    def assert_connected(data: dict) -> None:
        if data[Field.status] != SessionStatus.connected.value:
            raise RuntimeError(data.get(Field.reason, data[Field.status]))


class LiveRealServersTest(unittest.TestCase):
    client: LiveClient
    servers: list[LiveServer]
    public_ready = False

    @classmethod
    def setUpClass(cls):
        if os.environ.get(LiveEnv.enabled) != LiveDefault.enabled:
            raise unittest.SkipTest(f"set {LiveEnv.enabled}=1 to run real SSH e2e tests")
        cfg = Path(os.environ.get(LiveEnv.config, LiveDefault.config))
        cls.servers = PsCred.load(cfg)
        cls.client = LiveClient()
        for server in cls.servers:
            cls.client.reg(server)

    @classmethod
    def tearDownClass(cls):
        client = getattr(cls, "client", None)
        if client:
            try:
                if cls.public_ready:
                    client.once(cls.public(), RemoteCmd.stop_pg(), LiveDefault.timeout)
            finally:
                client.close()

    @classmethod
    def public(cls) -> LiveServer:
        for server in cls.servers:
            if server.role == LiveRole.public:
                return server
        raise unittest.SkipTest("no public server in config")

    @classmethod
    def private(cls) -> LiveServer:
        for server in cls.servers:
            if server.role == LiveRole.private:
                return server
        raise unittest.SkipTest("no private server in config")

    @classmethod
    def ready_public(cls) -> LiveServer:
        server = cls.public()
        if not cls.public_ready:
            cls.client.once(server, RemoteCmd.install_docker(), LiveDefault.long_timeout)
            cls.client.once(server, RemoteCmd.start_pg(), LiveDefault.long_timeout)
            cls.public_ready = True
        return server

    def run_cmd(self, server: LiveServer, command: str, mode: ExecMode = ExecMode.host, opts: dict | None = None, timeout: float = LiveDefault.timeout) -> str:
        sid = self.client.open(server)
        try:
            data = self.client.send(sid, command, mode, opts)
            return self.client.wait(sid, data[Field.command_id], timeout)
        finally:
            self.client.close_session(server, sid)

    def test_private_basic_interaction(self):
        out = self.run_cmd(self.private(), f"echo {shlex.quote(LiveMarker.private_basic)}")
        self.assertIn(LiveMarker.private_basic, out)

    def test_public_basic_and_escape_command(self):
        public = self.public()
        basic = self.run_cmd(public, f"echo {shlex.quote(LiveMarker.public_basic)}")
        escaped = self.run_cmd(public, f"printf '%s\\n' {shlex.quote(LiveMarker.escape)}")
        self.assertIn(LiveMarker.public_basic, basic)
        self.assertIn(LiveMarker.escape, escaped)

    def test_public_slow_output_weak_network_shape(self):
        script = f"for i in 1 2 3; do echo weak-$i; sleep 1; done; echo {shlex.quote(LiveMarker.weak)}"
        out = self.run_cmd(self.public(), RemoteCmd.shell(script), timeout=LiveDefault.timeout)
        self.assertIn("weak-1", out)
        self.assertIn("weak-2", out)
        self.assertIn("weak-3", out)
        self.assertIn(LiveMarker.weak, out)

    def test_public_interactive_password_and_choice(self):
        public = self.public()
        sid = self.client.open(public)
        script = (
            'printf "Password: "; '
            'stty -echo 2>/dev/null || true; '
            'read pw; '
            'stty echo 2>/dev/null || true; '
            'printf "\\nPW:%s\\n" "$pw"; '
            'printf "Continue? [y/n] "; '
            'read yn; '
            'echo "YN:$yn"'
        )
        try:
            data = self.client.send(sid, RemoteCmd.shell(script))
            cid = data[Field.command_id]
            self.client.until(sid, cid, LiveMarker.prompt_password)
            self.client.send(sid, "codex-secret", ExecMode.raw)
            self.client.until(sid, cid, LiveMarker.prompt_choice)
            self.client.send(sid, "y", ExecMode.raw)
            out = self.client.wait(sid, cid)
            self.assertIn(LiveMarker.interactive_pw, out)
            self.assertIn(LiveMarker.interactive_choice, out)
        finally:
            self.client.close_session(public, sid)

    def test_public_container_mode(self):
        public = self.ready_public()
        out = self.run_cmd(
            public,
            f"echo {shlex.quote(LiveMarker.container)}",
            ExecMode.docker,
            {Field.container: LiveName.pg_container, Field.shell: "sh"},
        )
        self.assertIn(LiveMarker.container, out)

    def test_public_database_raw_session(self):
        public = self.ready_public()
        sid = self.client.open(public)
        try:
            data = self.client.send(sid, RemoteCmd.psql_repl())
            cid = data[Field.command_id]
            self.client.send(sid, f"select '{LiveMarker.database}' as marker;", ExecMode.raw)
            self.client.until(sid, cid, LiveMarker.database)
            self.client.send(sid, "\\q", ExecMode.raw)
            out = self.client.wait(sid, cid)
            self.assertIn(LiveMarker.database, out)
        finally:
            self.client.close_session(public, sid)

    def test_public_container_database_mode(self):
        public = self.ready_public()
        out = self.run_cmd(
            public,
            RemoteCmd.psql(f"select '{LiveMarker.container_database}'", port=False),
            ExecMode.docker,
            {Field.container: LiveName.pg_container, Field.shell: "sh"},
        )
        self.assertIn(LiveMarker.container_database, out)


if __name__ == "__main__":
    unittest.main()
