import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from ssh_sftp_mcp.constants import CommandStatus, ExecMode, Field, JsonKey, Tool
from ssh_sftp_mcp.tools import ToolFactory


class EnvName:
    host = "E2E_HOST"
    port = "E2E_PORT"
    user = "E2E_USER"
    password = "E2E_PASSWORD"
    size_mib = "E2E_SIZE_MIB"
    remote_base = "E2E_REMOTE_BASE"


class E2EConst:
    default_size_mib = 64
    block = 1024 * 1024
    timeout = 180
    poll = 0.2
    read_limit = 65536
    local_root = Path("C:/tmp/ssh_sftp_mcp_e2e")
    remote_base = "/tmp"
    source = "large.bin"
    download = "large.download.bin"


class ResultField:
    host = "host"
    user = "user"
    size_bytes = "size_bytes"
    upload_bytes = "upload_bytes"
    upload_method = "upload_method"
    upload_elapsed_sec = "upload_elapsed_sec"
    upload_mib_s = "upload_mib_s"
    download_bytes = "download_bytes"
    download_method = "download_method"
    download_elapsed_sec = "download_elapsed_sec"
    download_mib_s = "download_mib_s"
    remote_path = "remote_path"
    local_sha256 = "local_sha256"
    remote_sha256 = "remote_sha256"
    download_sha256 = "download_sha256"
    remote_size = "remote_size"
    downloaded_size = "downloaded_size"
    hash_match = "hash_match"
    size_match = "size_match"
    cleanup_exit_code = "cleanup_exit_code"
    session_id = "session_id"


class McpTool:
    def __init__(self):
        self.service = ToolFactory.make()

    def call(self, name: str, args: dict) -> dict:
        res = self.service.call(name, args)
        if res.get(JsonKey.is_error):
            text = res[JsonKey.content][0][JsonKey.text]
            raise RuntimeError(text)
        return res[JsonKey.structured]


class LargeFile:
    def __init__(self, path: Path, size: int):
        self.path = path
        self.size = size

    def make(self) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with self.path.open("wb") as file:
            for index in range(self.size // E2EConst.block):
                seed = f"ssh_sftp_mcp_e2e_block_{index:06d}\n".encode()
                data = (seed * ((E2EConst.block // len(seed)) + 1))[:E2EConst.block]
                file.write(data)
                digest.update(data)
        return digest.hexdigest()

    @staticmethod
    def hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            while True:
                chunk = file.read(E2EConst.block)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def delete(self) -> None:
        if self.path.exists():
            self.path.unlink()


class LargeTransferE2E:
    def __init__(self):
        self.host = os.environ[EnvName.host]
        self.port = int(os.environ[EnvName.port])
        self.user = os.environ[EnvName.user]
        self.password = os.environ[EnvName.password]
        self.size_mib = int(os.environ.get(EnvName.size_mib, E2EConst.default_size_mib))
        self.size = self.size_mib * E2EConst.block
        self.remote_base = os.environ.get(EnvName.remote_base, E2EConst.remote_base).rstrip("/")
        self.remote_dir = f"{self.remote_base}/ssh_sftp_mcp_e2e_{int(time.time())}"
        self.remote_file = f"{self.remote_dir}/{E2EConst.source}"
        self.local_dir = E2EConst.local_root / str(int(time.time()))
        self.local_file = self.local_dir / E2EConst.source
        self.download_file = self.local_dir / E2EConst.download
        self.tool = McpTool()
        self.session_id = ""

    def run(self) -> dict:
        large = LargeFile(self.local_file, self.size)
        local_hash = large.make()
        try:
            self.connect()
            self.remote_mkdir()
            upload, upload_elapsed = self.upload()
            remote_size, remote_hash = self.verify()
            download, download_elapsed = self.download()
            download_hash = LargeFile.hash(self.download_file)
            cleanup_code = self.cleanup_remote()
            return self.result(upload, upload_elapsed, download, download_elapsed, local_hash, remote_size, remote_hash, download_hash, cleanup_code)
        finally:
            self.close()
            large.delete()
            if self.local_dir.exists():
                shutil.rmtree(self.local_dir, ignore_errors=True)

    def connect(self) -> None:
        data = self.tool.call(Tool.temp, {
            Field.ip: self.host,
            Field.port: self.port,
            Field.username: self.user,
            Field.password: self.password,
        })
        self.session_id = data[Field.session_id]

    def remote_mkdir(self) -> None:
        code, output = self.remote(f"mkdir -p {self.remote_dir}")
        if code:
            raise RuntimeError(f"mkdir failed code={code} output={output}")

    def upload(self) -> tuple[dict, float]:
        start = time.time()
        data = self.tool.call(Tool.upload, {
            Field.ip: self.host,
            Field.local_path: str(self.local_file),
            Field.remote_path: self.remote_file,
            Field.session_id: self.session_id,
        })
        return data, time.time() - start

    def download(self) -> tuple[dict, float]:
        start = time.time()
        data = self.tool.call(Tool.download, {
            Field.ip: self.host,
            Field.remote_path: self.remote_file,
            Field.local_path: str(self.download_file),
            Field.session_id: self.session_id,
        })
        return data, time.time() - start

    def verify(self) -> tuple[int, str]:
        command = f"set -e; stat -c %s {self.remote_file}; sha256sum {self.remote_file} | awk '{{print $1}}'"
        code, output = self.remote(command)
        if code:
            raise RuntimeError(f"verify failed code={code} output={output}")
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return int(lines[-2]), lines[-1]

    def cleanup_remote(self) -> int:
        command = f"rm -f {self.remote_file}; rmdir {self.remote_dir} 2>/dev/null || true"
        code, _ = self.remote(command)
        return code

    def remote(self, command: str) -> tuple[int, str]:
        data = self.tool.call(Tool.exec, {
            Field.session_id: self.session_id,
            Field.command: command,
            Field.mode: ExecMode.host.value,
        })
        return self.wait(data[Field.command_id])

    def wait(self, command_id: str) -> tuple[int, str]:
        deadline = time.time() + E2EConst.timeout
        cursor = 0
        output = []
        while time.time() < deadline:
            read = self.tool.call(Tool.read, {
                Field.session_id: self.session_id,
                Field.command_id: command_id,
                Field.cursor: cursor,
                Field.limit: E2EConst.read_limit,
            })
            cursor = read[Field.next_cursor]
            if read.get(Field.output):
                output.append(read[Field.output])
            status = self.tool.call(Tool.status, {
                Field.session_id: self.session_id,
                Field.command_id: command_id,
            })
            command = status[Field.command]
            if command[Field.status] == CommandStatus.done.value:
                read = self.tool.call(Tool.read, {
                    Field.session_id: self.session_id,
                    Field.command_id: command_id,
                    Field.cursor: cursor,
                    Field.limit: E2EConst.read_limit,
                })
                if read.get(Field.output):
                    output.append(read[Field.output])
                return int(command[Field.exit_code]), "\n".join(output)
            time.sleep(E2EConst.poll)
        raise TimeoutError(f"command did not finish: {command_id}")

    def close(self) -> None:
        if self.session_id:
            self.tool.call(Tool.close, {
                Field.ip: self.host,
                Field.session_id: self.session_id,
            })

    def result(self, upload: dict, upload_elapsed: float, download: dict, download_elapsed: float, local_hash: str, remote_size: int, remote_hash: str, download_hash: str, cleanup_code: int) -> dict:
        downloaded_size = self.download_file.stat().st_size
        return {
            ResultField.host: self.host,
            ResultField.user: self.user,
            ResultField.size_bytes: self.size,
            ResultField.upload_bytes: upload[Field.bytes],
            ResultField.upload_method: upload[Field.method],
            ResultField.upload_elapsed_sec: round(upload_elapsed, 3),
            ResultField.upload_mib_s: round(self.size_mib / upload_elapsed, 3),
            ResultField.download_bytes: download[Field.bytes],
            ResultField.download_method: download[Field.method],
            ResultField.download_elapsed_sec: round(download_elapsed, 3),
            ResultField.download_mib_s: round(self.size_mib / download_elapsed, 3),
            ResultField.remote_path: self.remote_file,
            ResultField.local_sha256: local_hash,
            ResultField.remote_sha256: remote_hash,
            ResultField.download_sha256: download_hash,
            ResultField.remote_size: remote_size,
            ResultField.downloaded_size: downloaded_size,
            ResultField.hash_match: remote_hash == local_hash == download_hash,
            ResultField.size_match: remote_size == downloaded_size == self.size == upload[Field.bytes] == download[Field.bytes],
            ResultField.cleanup_exit_code: cleanup_code,
            ResultField.session_id: self.session_id,
        }


class Main:
    @staticmethod
    def run() -> None:
        result = LargeTransferE2E().run()
        print(json.dumps(result, ensure_ascii=False, indent=2))


Main.run()
