import argparse
import hashlib
import json
import os
import importlib.metadata
import queue
import secrets
import shlex
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path


class Key:
    jsonrpc = "jsonrpc"; id = "id"; method = "method"; params = "params"
    result = "result"; error = "error"; name = "name"; arguments = "arguments"
    content = "content"; text = "text"; structured = "structuredContent"; is_error = "isError"
    protocol_version = "protocolVersion"; ip = "ip"; port = "port"; username = "username"
    password = "password"; local_path = "local_path"; remote_path = "remote_path"
    command = "command"; bytes = "bytes"; reason = "reason"


class Name:
    initialize = "initialize"; initialized = "notifications/initialized"
    tools_list = "tools/list"; tools_call = "tools/call"
    register = "ssh_register"; delete = "ssh_delete"; once = "ssh_temp_exec"
    upload = "sftp_upload"; download = "sftp_download"


class Env:
    service_start = "SSH_SFTP_MCP_SERVICE_START"
    service_host = "SSH_SFTP_MCP_SERVICE_HOST"
    service_port = "SSH_SFTP_MCP_SERVICE_PORT"
    python = "SSH_SFTP_MCP_PYTHON"


class Bench:
    mib = 1024 * 1024; default_size = 128; default_rounds = 3
    block = 1024 * 1024; tail = 137; small = 4 * mib
    timeout = 1800.0; read_timeout = 3.0; block_size = 131072; workers = 32
    service_default_host = "127.0.0.1"; service_default_port = "8765"
    remote_prefix = "/tmp/ssh_sftp_mcp_bench_"


class Field:
    phase = "phase"; server = "server"; label = "label"; python = "python"
    dependencies = "dependencies"; started = "started_at"; ended = "ended_at"
    size = "size_bytes"; rounds = "rounds"; times = "times"
    upload = "upload_seconds"; download = "download_seconds"; transfer = "transfer_seconds"
    verify = "verify_seconds"; total = "total_seconds"; sha256 = "sha256"
    verified = "verified"; verification = "verification"; options = "options"
    checks = "checks"; error = "error"


class Credential:
    @staticmethod
    def value(name: str) -> str:
        value = os.environ.get(name)
        if value:
            return value
        if sys.platform == "win32":
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                finally:
                    winreg.CloseKey(key)
                if value:
                    return str(value)
            except OSError:
                pass
        raise RuntimeError(f"missing environment variable: {name}")


class Server:
    def __init__(self, label: str):
        prefix = "NAS_SERVER_" if label == "nas" else "ABYSS_SERVER_"
        self.label = label
        self.host = Credential.value(prefix + "HOST")
        self.port = int(Credential.value(prefix + "PORT"))
        self.user = Credential.value(prefix + "USER")
        self.password = Credential.value(prefix + "PASSWORD")


class Protocol:
    def __init__(self, process: subprocess.Popen, stderr_path: Path):
        self.process = process; self.responses = queue.Queue(); self.next_id = 1
        self.stderr_file = stderr_path.open("w", encoding="utf-8")
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                response = json.loads(line)
                if Key.id in response: self.responses.put(response)
            except json.JSONDecodeError: continue

    def _read_stderr(self):
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr_file.write(line); self.stderr_file.flush()

    def call(self, method: str, params: dict | None = None) -> dict:
        if self.process.poll() is not None: raise RuntimeError("MCP client process exited unexpectedly")
        req_id = self.next_id; self.next_id += 1
        request = {Key.jsonrpc: "2.0", Key.id: req_id, Key.method: method}
        if params is not None: request[Key.params] = params
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n"); self.process.stdin.flush()
        deadline = time.monotonic() + Bench.timeout
        while time.monotonic() < deadline:
            try: response = self.responses.get(timeout=Bench.read_timeout)
            except queue.Empty:
                if self.process.poll() is not None: raise RuntimeError("MCP client process exited unexpectedly")
                continue
            if response.get(Key.id) == req_id:
                if Key.error in response: raise RuntimeError(str(response[Key.error]))
                return response.get(Key.result, {})
        raise TimeoutError(f"MCP request timed out: {method}")

    def tool(self, name: str, arguments: dict) -> dict:
        result = self.call(Name.tools_call, {Key.name: name, Key.arguments: arguments})
        if result.get(Key.is_error):
            content = result.get(Key.content, [{}])
            raise RuntimeError(content[0].get(Key.text, "MCP tool failed"))
        return result.get(Key.structured, {})

    def close(self):
        try:
            if self.process.stdin: self.process.stdin.close()
        except OSError: pass
        try: self.process.wait(timeout=10)
        except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=10)
        self.stderr_file.close()


class Direct:
    def __init__(self, server):
        import paramiko
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(server.host, port=server.port, username=server.user, password=server.password, timeout=30, banner_timeout=30, auth_timeout=30)

    def exec(self, command):
        stdin, stdout, stderr = self.client.exec_command(command, timeout=Bench.timeout)
        out, err = stdout.read().decode("utf-8", "replace"), stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        if code:
            raise RuntimeError(err or f"remote command failed: {code}")
        return out

    def clean(self, remote):
        sftp = self.client.open_sftp()
        try:
            for name in sftp.listdir(remote):
                if name.startswith(("payload", "readback", "empty", "overwrite")) or ".part" in name:
                    sftp.remove(remote.rstrip("/") + "/" + name)
            sftp.rmdir(remote)
            return True
        except (OSError, IOError):
            return False
        finally:
            sftp.close()

    def close(self):
        self.client.close()


class FileData:
    def __init__(self, path: Path, size: int):
        self.path = path; self.size = size; self.digest = ""

    def make(self) -> str:
        digest = hashlib.sha256(); self.path.parent.mkdir(parents=True, exist_ok=True)
        remaining = self.size
        with self.path.open("wb") as stream:
            while remaining >= Bench.block:
                data = secrets.token_bytes(Bench.block); stream.write(data); digest.update(data); remaining -= Bench.block
            if remaining:
                data = secrets.token_bytes(remaining); stream.write(data); digest.update(data)
        self.digest = digest.hexdigest(); return self.digest

    @staticmethod
    def hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for data in iter(lambda: stream.read(Bench.block), b""): digest.update(data)
        return digest.hexdigest()


class Benchmark:
    def __init__(self, args: argparse.Namespace):
        self.args = args; self.server = Server(args.server)
        self.root = Path(args.work_dir).resolve() if args.work_dir else Path.cwd() / "transfer-bench-work"
        self.root.mkdir(parents=True, exist_ok=True); self.run_id = uuid.uuid4().hex
        self.local = self.root / self.run_id; self.remote = Bench.remote_prefix + self.run_id
        self.remote_file = self.remote + "/payload.bin"
        self.out = Path(args.out).resolve() if args.out else self.root / f"{self.run_id}.json"
        self.client = None; self.direct = None; self.registered = False
        self.result = {Field.phase: args.phase, Field.server: args.server, Field.label: f"{args.phase}-{args.server}",
            Field.python: sys.version, Field.dependencies: Benchmark.dependencies(),
            Field.started: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            Field.size: args.size_mib * Bench.mib + Bench.tail, Field.rounds: args.rounds, Field.times: []}

    @staticmethod
    def version(name):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return "unavailable"

    @staticmethod
    def dependencies() -> dict:
        return {name: Benchmark.version(name) for name in ("asyncssh", "netmiko", "paramiko")}

    def start(self):
        self.local.mkdir(parents=True, exist_ok=True); environment = os.environ.copy()
        environment[Env.service_start] = "0"; environment.setdefault(Env.service_host, Bench.service_default_host)
        environment.setdefault(Env.service_port, Bench.service_default_port)
        executable = environment.get(Env.python, sys.executable)
        process = subprocess.Popen([executable, "-m", "ssh_sftp_mcp"], cwd=str(Path(__file__).resolve().parents[1]), env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.client = Protocol(process, self.local / "mcp-client.stderr.log")
        self.client.call(Name.initialize, {Key.protocol_version: "2025-06-18", "capabilities": {}, "clientInfo": {"name": "transfer-bench", "version": "1"}})
        self.notify(); self.client.call(Name.tools_list, {})
        self.client.tool(Name.register, {Key.ip: self.server.host, Key.port: self.server.port, Key.username: self.server.user, Key.password: self.server.password})
        self.registered = True
        self.direct = Direct(self.server)
        self.direct.exec(f"set -eu; free=$(df -Pk /tmp | awk 'NR==2{{printf \"%.0f\", $4 * 1024}}'); test \"$free\" -gt {self.result[Field.size] * 3}; mkdir -p {shlex.quote(self.remote)}")

    def notify(self):
        assert self.client is not None and self.client.process.stdin is not None
        self.client.process.stdin.write(json.dumps({Key.jsonrpc: "2.0", Key.method: Name.initialized}, separators=(",", ":")) + "\n")
        self.client.process.stdin.flush()

    def remote_exec(self, command: str) -> str:
        if self.direct is None:
            raise RuntimeError("direct SSH connection is not ready")
        return self.direct.exec(command)

    def transfer(self, source: Path, target: str, upload: bool, options: dict):
        assert self.client is not None
        args = {Key.ip: self.server.host, Key.local_path: str(source), Key.remote_path: target}; args.update(options)
        started = time.perf_counter(); return self.client.tool(Name.upload if upload else Name.download, args), time.perf_counter() - started

    def round(self, source: Path, expected: str, label: str, options: dict) -> dict:
        downloaded = self.local / f"{label}.download.bin"; started = time.perf_counter()
        if label.startswith("overwrite"):
            downloaded.write_bytes(b"OLD CONTENT")
            self.direct.exec(f"printf OLD_CONTENT > {shlex.quote(self.remote_file)}")
        upload, upload_seconds = self.transfer(source, self.remote_file, True, options)
        remote_started = time.perf_counter()
        remote = self.remote_exec(f"set -eu; stat -c %s {shlex.quote(self.remote_file)}; sha256sum {shlex.quote(self.remote_file)} | awk '{{print $1}}'")
        remote_seconds = time.perf_counter() - remote_started
        lines = [line.strip() for line in remote.splitlines() if line.strip()]
        remote_size, remote_hash = int(lines[-2]), lines[-1]
        download, download_seconds = self.transfer(downloaded, self.remote_file, False, options)
        local_started = time.perf_counter()
        local_hash = FileData.hash(downloaded); local_size = downloaded.stat().st_size
        independent_seconds = remote_seconds + (time.perf_counter() - local_started)
        size_match = remote_size == local_size == source.stat().st_size == upload.get(Key.bytes) == download.get(Key.bytes)
        hash_match = remote_hash == expected == local_hash
        if not hash_match or not size_match:
            raise RuntimeError(f"verification failed for {label}: hash={hash_match} size={size_match}")
        if options.get("verify") == "readback" and (upload.get("verification") != "readback" or download.get("verification") != "readback"):
            raise RuntimeError(f"readback verification was not reported for {label}")
        return {Field.label: label, Field.upload: round(upload_seconds, 6), Field.download: round(download_seconds, 6),
            Field.transfer: round(upload_seconds + download_seconds, 6), Field.verify: round(independent_seconds, 6),
            Field.total: round(time.perf_counter() - started, 6), Field.sha256: {"source": expected, "remote": remote_hash, "download": local_hash},
            "upload_result": upload, "download_result": download,
            Field.verified: remote_hash == expected == local_hash, Field.verification: {"remote_size": remote_size, "download_size": local_size,
            "expected_size": source.stat().st_size, "upload_bytes": upload.get(Key.bytes), "download_bytes": download.get(Key.bytes),
            "size_match": size_match}}

    def extras(self, options: dict) -> list[dict]:
        if self.args.phase != "async": return []
        checks = []; options = dict(options); options["verify"] = "readback"; small = FileData(self.local / "readback-4MiB.bin", Bench.small)
        checks.append(self.round(small.path, small.make(), "readback-4MiB", options))
        empty = FileData(self.local / "empty.bin", 0); checks.append(self.round(empty.path, empty.make(), "empty", options))
        first = FileData(self.local / "overwrite-a.bin", Bench.mib); second = FileData(self.local / "overwrite-b.bin", Bench.mib)
        checks.append(self.round(first.path, first.make(), "overwrite-a", options)); checks.append(self.round(second.path, second.make(), "overwrite-b", options))
        return checks

    def run(self) -> dict:
        source = FileData(self.local / "payload.bin", self.args.size_mib * Bench.mib + Bench.tail); source.make()
        options = {} if self.args.phase == "baseline" else {"workers": Bench.workers, "block_size": Bench.block_size, "verify": self.args.verify}
        self.result[Field.options] = options
        try:
            self.start();
            for i in range(self.args.rounds):
                self.result[Field.times].append(self.round(source.path, source.digest, f"round-{i + 1}", options))
                self.flush()
            self.result[Field.checks] = self.extras(options); self.result[Field.ended] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); return self.result
        except Exception as exc: self.result[Field.error] = str(exc); raise
        finally:
            cleanup = False
            if self.direct:
                try: cleanup = self.direct.clean(self.remote)
                except Exception as exc: self.result.setdefault(Field.error, f"cleanup: {exc}")
            if self.client and self.registered:
                try: self.client.tool(Name.delete, {Key.ip: self.server.host})
                except Exception as exc: self.result.setdefault(Field.error, f"credential cleanup: {exc}")
            if self.direct: self.direct.close()
            self.result["cleanup"] = cleanup
            if self.client: self.client.close()
            self.flush()

    def flush(self):
        self.out.parent.mkdir(parents=True, exist_ok=True); self.out.write_text(json.dumps(self.result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Cli:
    @staticmethod
    def parse(argv):
        parser = argparse.ArgumentParser(description="Benchmark the MCP SFTP transfer path.")
        parser.add_argument("--server", choices=("nas", "abyss"), required=True); parser.add_argument("--size-mib", type=int, default=Bench.default_size)
        parser.add_argument("--rounds", type=int, default=Bench.default_rounds); parser.add_argument("--phase", choices=("baseline", "async"), default="baseline")
        parser.add_argument("--verify", choices=("auto", "readback"), default="auto"); parser.add_argument("--out", default=""); parser.add_argument("--work-dir", default="")
        args = parser.parse_args(argv)
        if args.size_mib < 1 or args.rounds < 1: parser.error("--size-mib and --rounds must be positive")
        return args

    @staticmethod
    def run(argv):
        args = Cli.parse(argv); bench = Benchmark(args)
        try:
            result = bench.run()
            print(json.dumps({"out": str(bench.out), "phase": result[Field.phase], "server": result[Field.server], "rounds": len(result[Field.times]), "verified": all(item[Field.verified] for item in result[Field.times])}, ensure_ascii=False))
            return 0
        except Exception as exc: bench.flush(); print(f"benchmark failed: {exc}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(Cli.run(sys.argv[1:]))
