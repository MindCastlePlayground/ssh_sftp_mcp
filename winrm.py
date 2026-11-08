import hashlib
import os
import re
import time
import uuid
from pathlib import Path

from .constants import (
    Auth,
    CommandStatus,
    Field,
    Msg,
    Origin,
    SessionStatus,
    Text,
    WinrmDefault,
    WinrmShell,
)
from .log import LogFactory
from .models import CommandInfo, OutputChunk, ServerInfo, SessionView
from .transfer_constants import TransferError, TransferMode, TransferResult


class Flags:
    @staticmethod
    def on(value: object, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)


class Ps:
    @staticmethod
    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"


class HashText:
    pattern = re.compile(r"[0-9a-fA-F]{64}")

    @classmethod
    def read(cls, text: str) -> str:
        match = cls.pattern.search(text or "")
        if not match:
            raise RuntimeError(Msg.winrm_failed)
        return match.group(0).lower()

    @staticmethod
    def file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                block = handle.read(1048576)
                if not block:
                    break
                digest.update(block)
        return digest.hexdigest()


class PypsrpLink:
    def __init__(self, info: ServerInfo):
        self.info = info
        self.client = None
        self.pool = None
        self.dead = False
        self.log = LogFactory.get()

    def alive(self) -> bool:
        return self.client is not None and not self.dead

    def open(self) -> None:
        from pypsrp.client import Client

        encryption = "auto"
        if self.info.auth == Auth.basic and not self.info.ssl:
            encryption = "never"
        self.client = Client(
            self.info.ip,
            port=self.info.port,
            username=self.info.username,
            password=self.info.password,
            ssl=self.info.ssl,
            cert_validation=False,
            auth=self.info.auth,
            encryption=encryption,
            connection_timeout=WinrmDefault.connect_timeout,
            read_timeout=WinrmDefault.read_timeout,
            operation_timeout=WinrmDefault.operation_timeout,
            max_envelope_size=WinrmDefault.envelope,
        )
        self.dead = False
        self.log.info("winrm open ip=%s port=%s user=%s ssl=%s", self.info.ip, self.info.port, self.info.username, self.info.ssl)

    def close(self) -> None:
        if self.pool is not None:
            try:
                self.pool.close()
            except Exception:
                self.log.exception("winrm pool close failed ip=%s", self.info.ip)
            self.pool = None
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                self.log.exception("winrm client close failed ip=%s", self.info.ip)
            self.client = None

    def ensure(self) -> None:
        if not self.alive():
            raise RuntimeError(Msg.disconnected)
        if self.pool is None:
            from pypsrp.powershell import RunspacePool

            self.pool = RunspacePool(self.client.wsman)
            self.pool.open()

    def run_ps(self, script: str) -> tuple[str, str, int]:
        from pypsrp.powershell import PowerShell

        self.ensure()
        shell = PowerShell(self.pool)
        shell.add_script(script, use_local_scope=False)
        shell.add_cmdlet("Out-String").add_parameter("Stream")
        try:
            shell.invoke()
        except Exception:
            self.dead = True
            raise
        text = "\n".join(str(item) for item in shell.output)
        err = "\n".join(str(item) for item in shell.streams.error)
        if shell.had_errors:
            return text, err, 1
        return text, err, self.exit_code()

    def exit_code(self) -> int:
        from pypsrp.powershell import PowerShell

        shell = PowerShell(self.pool)
        shell.add_script("if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }", use_local_scope=False)
        try:
            values = shell.invoke()
        except Exception:
            self.dead = True
            raise
        if not values:
            return 0
        try:
            return int(str(values[-1]).strip())
        except ValueError:
            return 0

    def run_cmd(self, command: str) -> tuple[str, str, int]:
        if not self.alive():
            raise RuntimeError(Msg.disconnected)
        try:
            out, err, code = self.client.execute_cmd(command)
        except Exception:
            self.dead = True
            raise
        return out, err, int(code)

    def copy(self, local_path: str, remote_path: str) -> str:
        if not self.alive():
            raise RuntimeError(Msg.disconnected)
        try:
            return self.client.copy(local_path, remote_path)
        except Exception:
            self.dead = True
            raise

    def fetch(self, remote_path: str, local_path: str) -> None:
        if not self.alive():
            raise RuntimeError(Msg.disconnected)
        try:
            self.client.fetch(remote_path, local_path)
        except Exception:
            self.dead = True
            raise


class WinrmBuffer:
    def __init__(self) -> None:
        self.seq = 0
        self.chunks: list[OutputChunk] = []

    def add(self, text: str) -> None:
        if not text:
            return
        start = self.seq
        self.seq += len(text)
        self.chunks.append(OutputChunk(seq=self.seq, text=text, start=start))

    def slice(self, cursor: int, limit: int, start_seq: int) -> tuple[str, int]:
        start = max(cursor, start_seq)
        text = ""
        nxt = start
        for chunk in self.chunks:
            if chunk.seq <= start:
                continue
            if len(text) >= limit:
                break
            pos = max(start, chunk.start)
            part = chunk.text[pos - chunk.start:pos - chunk.start + limit - len(text)]
            text += part
            nxt = pos + len(part)
        return text, nxt


class WinrmSession:
    def __init__(self, info: ServerInfo, origin: Origin, link: PypsrpLink | None = None):
        self.id = uuid.uuid4().hex
        self.info = info
        self.origin = origin
        self.link = link or PypsrpLink(info)
        self.status = SessionStatus.connecting
        self.reason = ""
        self.started_at = time.time()
        self.last_seen_at = self.started_at
        self.commands: dict[str, CommandInfo] = {}
        self.errors: dict[str, str] = {}
        self.buf = WinrmBuffer()
        self.log = LogFactory.get()

    def conn(self) -> dict:
        try:
            self.log.info("winrm connect start ip=%s session=%s", self.info.ip, self.id)
            self.link.open()
            self.status = SessionStatus.connected
            self.last_seen_at = time.time()
            self.log.info("winrm connect ok ip=%s session=%s", self.info.ip, self.id)
            return self.view().data()
        except Exception as exc:
            self.status = SessionStatus.failed
            self.reason = str(exc)
            self.log.exception("winrm connect failed ip=%s session=%s", self.info.ip, self.id)
            raise

    def view(self) -> SessionView:
        return SessionView(
            session_id=self.id,
            ip=self.info.ip,
            port=self.info.port,
            username=self.info.username,
            origin=self.origin,
            status=self.status,
            reason=self.reason,
            started_at=self.started_at,
            last_seen_at=self.last_seen_at,
            protocol=self.info.protocol,
        )

    def probe(self) -> bool:
        if self.status in (SessionStatus.closed, SessionStatus.failed, SessionStatus.disconnected):
            return False
        if not self.link.alive():
            self.status = SessionStatus.disconnected
            self.reason = Msg.disconnected
            return False
        return True

    def close(self) -> dict:
        self.log.info("winrm close session=%s ip=%s", self.id, self.info.ip)
        self.status = SessionStatus.closed
        try:
            self.link.close()
        except Exception:
            self.log.exception("winrm close failed session=%s", self.id)
        return self.view().data()

    def send(self, command: str, mode, opts: dict) -> dict:
        del mode
        if not self.probe():
            raise RuntimeError(Msg.disconnected)
        shell = str(opts.get(Field.shell, WinrmDefault.shell))
        if shell not in WinrmShell.values:
            raise ValueError(Msg.bad_shell)
        cmd = CommandInfo(
            command_id=uuid.uuid4().hex,
            command=command,
            sent=command,
            status=CommandStatus.running,
            start_seq=self.buf.seq,
        )
        self.commands[cmd.command_id] = cmd
        self.log.info("winrm exec session=%s command=%s shell=%s", self.id, cmd.command_id, shell)
        try:
            if shell == WinrmShell.cmd:
                out, err, code = self.link.run_cmd(command)
            else:
                out, err, code = self.link.run_ps(command)
        except Exception as exc:
            cmd.status = CommandStatus.failed
            cmd.ended_at = time.time()
            cmd.reason = str(exc)
            self.status = SessionStatus.disconnected
            self.reason = str(exc)
            raise
        self.errors[cmd.command_id] = err
        self.buf.add(out)
        cmd.exit_code = code
        cmd.status = CommandStatus.done
        cmd.ended_at = time.time()
        cmd.end_seq = self.buf.seq
        self.last_seen_at = time.time()
        data = cmd.data()
        data[Field.session_id] = self.id
        data[Field.output] = out
        data[Field.stderr] = err
        data[Field.ok] = code == 0
        data[Field.shell] = shell
        return data

    def output(self, command_id: str | None, cursor: int, limit: int) -> dict:
        start_seq = 0
        cmd = None
        if command_id:
            if command_id not in self.commands:
                raise KeyError(Msg.unknown_command)
            cmd = self.commands[command_id]
            start_seq = cmd.start_seq
        text, nxt = self.buf.slice(cursor, limit, start_seq)
        data = {
            Field.session_id: self.id,
            Field.command_id: command_id,
            Field.output: text,
            Field.cursor: cursor,
            Field.next_cursor: nxt,
            Field.status: self.status.value,
            Field.stderr: self.errors.get(command_id or "", ""),
        }
        if cmd:
            data[Field.exit_code] = cmd.exit_code
            data[Field.done] = cmd.status in (CommandStatus.done, CommandStatus.failed)
        return data

    def cmd(self, command_id: str) -> dict:
        if command_id not in self.commands:
            raise KeyError(Msg.unknown_command)
        data = self.commands[command_id].data()
        data[Field.session_id] = self.id
        data[Field.stderr] = self.errors.get(command_id, "")
        return data


class WinrmOnce:
    @staticmethod
    def run(info: ServerInfo, command: str, opts: dict) -> dict:
        shell = str(opts.get(Field.shell, WinrmDefault.shell))
        if shell not in WinrmShell.values:
            raise ValueError(Msg.bad_shell)
        link = PypsrpLink(info)
        try:
            link.open()
            if shell == WinrmShell.cmd:
                out, err, code = link.run_cmd(command)
            else:
                out, err, code = link.run_ps(command)
        finally:
            link.close()
        return {
            Field.ip: info.ip,
            Field.port: info.port,
            Field.username: info.username,
            Field.protocol: info.protocol,
            Field.command: command,
            Field.shell: shell,
            Field.output: out,
            Field.stderr: err,
            Field.exit_code: code,
            Field.status: CommandStatus.done.value,
            Field.ok: code == 0,
        }


class WinrmTransfer:
    def __init__(self, info: ServerInfo, link: PypsrpLink | None = None):
        self.info = info
        self.link = link
        self.owned = link is None

    def run(self, local_path: str, remote_path: str, upload: bool) -> dict:
        if self.link is None:
            self.link = PypsrpLink(self.info)
            self.link.open()
        started = time.perf_counter()
        try:
            if upload:
                data = self.upload(local_path, remote_path)
            else:
                data = self.download(local_path, remote_path)
        finally:
            if self.owned and self.link is not None:
                self.link.close()
        data[TransferResult.total_seconds] = time.perf_counter() - started
        return data

    def upload(self, local_path: str, remote_path: str) -> dict:
        local = Path(local_path)
        digest = HashText.file(local)
        target = self.resolve(remote_path, True)
        started = time.perf_counter()
        self.link.copy(str(local), target)
        transfer_seconds = time.perf_counter() - started
        started = time.perf_counter()
        remote = self.remote_hash(target)
        verify_seconds = time.perf_counter() - started
        if remote != digest:
            raise IOError(TransferError.verify)
        return self.result(local.stat().st_size, digest, remote, transfer_seconds, verify_seconds)

    def download(self, local_path: str, remote_path: str) -> dict:
        target = self.resolve(remote_path, False)
        started = time.perf_counter()
        remote = self.remote_hash(target)
        verify_head = time.perf_counter() - started
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        part = Path(str(local) + ".part-" + uuid.uuid4().hex)
        started = time.perf_counter()
        try:
            self.link.fetch(target, str(part))
            transfer_seconds = time.perf_counter() - started
            digest = HashText.file(part)
            if digest != remote:
                raise IOError(TransferError.verify)
            os.replace(part, local)
        finally:
            if part.exists():
                part.unlink()
        return self.result(local.stat().st_size, digest, remote, transfer_seconds, verify_head)

    def resolve(self, path: str, mkdir: bool) -> str:
        quoted = Ps.quote(path)
        lines = [
            f"$dest = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath({quoted})",
        ]
        if mkdir:
            lines.extend([
                "$parent = Split-Path -Parent $dest",
                "if ($parent -and -not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }",
            ])
        lines.append("$dest")
        text, err, code = self.link.run_ps("\n".join(lines))
        if code != 0:
            raise RuntimeError(err.strip() or Msg.winrm_failed)
        rows = [row.strip() for row in text.splitlines() if row.strip()]
        if not rows:
            raise RuntimeError(err.strip() or Msg.winrm_failed)
        return rows[-1]

    def remote_hash(self, path: str) -> str:
        script = f"(Get-FileHash -Algorithm SHA256 -LiteralPath {Ps.quote(path)}).Hash"
        text, err, code = self.link.run_ps(script)
        if code != 0:
            raise RuntimeError(err.strip() or Msg.winrm_failed)
        return HashText.read(text)

    @staticmethod
    def result(size: int, digest: str, remote: str, transfer_seconds: float, verify_seconds: float) -> dict:
        return {
            TransferResult.bytes: size,
            TransferResult.method: TransferMode.winrm,
            TransferResult.sha256: digest,
            TransferResult.remote_sha256: remote,
            TransferResult.verified: True,
            TransferResult.verification: "sha256",
            TransferResult.workers: 1,
            TransferResult.block_size: 0,
            TransferResult.transfer_seconds: transfer_seconds,
            TransferResult.verify_seconds: verify_seconds,
        }
