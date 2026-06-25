from __future__ import annotations

import re
import shlex
import threading
import time
import uuid
import posixpath
from pathlib import Path

import paramiko

from .constants import CommandStatus, Default, ExecMode, Field, Limits, Mark, Msg, Origin, Regex, SessionStatus, Shell, ShellCmd, Text, TransferMethod
from .log import LogFactory
from .models import CommandInfo, OutputChunk, ServerInfo, SessionView


class LocalPath:
    @classmethod
    def roots(cls) -> list[Path]:
        package = Path(__file__).resolve().parent
        paths = [Path.cwd().resolve(), package, package.parent]
        roots = []
        for path in paths:
            if path not in roots:
                roots.append(path)
        return roots

    @classmethod
    def src(cls, local_path: str) -> str:
        path = Path(local_path).expanduser()
        if path.is_absolute():
            found = path
        else:
            found = cls.find(path)
        if not found.is_file():
            raise FileNotFoundError(f"{Msg.local_missing}: {found}")
        return str(found)

    @classmethod
    def dst(cls, local_path: str) -> str:
        path = Path(local_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return str(path.resolve())

    @classmethod
    def find(cls, path: Path) -> Path:
        for root in cls.roots():
            item = (root / path).resolve()
            if item.exists():
                return item
        return (Path.cwd() / path).resolve()


class SshSession:
    def __init__(self, info: ServerInfo, origin: Origin):
        self.id = uuid.uuid4().hex
        self.info = info
        self.origin = origin
        self.status = SessionStatus.connecting
        self.reason = ""
        self.started_at = time.time()
        self.last_seen_at = self.started_at
        self.lock = threading.RLock()
        self.client: paramiko.SSHClient | None = None
        self.channel: paramiko.Channel | None = None
        self.reader: threading.Thread | None = None
        self.stop = threading.Event()
        self.seq = 0
        self.tail = ""
        self.chunks: list[OutputChunk] = []
        self.commands: dict[str, CommandInfo] = {}
        self.log = LogFactory.get()

    def conn(self) -> dict:
        try:
            self.log.info("ssh connect start ip=%s port=%s user=%s session=%s", self.info.ip, self.info.port, self.info.username, self.id)
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                self.info.ip,
                port=self.info.port,
                username=self.info.username,
                password=self.info.password,
                timeout=Limits.connect_timeout,
                banner_timeout=Limits.connect_timeout,
                auth_timeout=Limits.connect_timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            transport = client.get_transport()
            if transport:
                transport.set_keepalive(Limits.keepalive)
            channel = client.invoke_shell(width=200, height=40)
            channel.settimeout(0.0)
            self.client = client
            self.channel = channel
            self.prep()
            self.status = SessionStatus.connected
            self.reader = threading.Thread(target=self.watch, daemon=True)
            self.reader.start()
            self.log.info("ssh connect ok ip=%s session=%s", self.info.ip, self.id)
            return self.view().data()
        except Exception as exc:
            self.status = SessionStatus.failed
            self.reason = str(exc)
            self.log.exception("ssh connect failed ip=%s session=%s", self.info.ip, self.id)
            raise

    def prep(self) -> None:
        if not self.channel:
            return
        self.channel.send(Shell.echo_off)
        time.sleep(Limits.prep_wait)
        self.drain()

    def drain(self) -> None:
        if not self.channel:
            return
        while self.channel.recv_ready():
            self.channel.recv(Limits.recv)

    def view(self) -> SessionView:
        self.probe()
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
        )

    def probe(self) -> bool:
        with self.lock:
            if self.status in (SessionStatus.closed, SessionStatus.failed, SessionStatus.disconnected):
                return False
            transport = self.client.get_transport() if self.client else None
            if not transport or not transport.is_active():
                self.mark(SessionStatus.disconnected, Msg.disconnected)
                return False
            if self.channel and self.channel.closed:
                self.mark(SessionStatus.disconnected, Msg.disconnected)
                return False
            return True

    def mark(self, status: SessionStatus, reason: str) -> None:
        if self.status != status or self.reason != reason:
            self.log.warning("session mark session=%s ip=%s status=%s reason=%s", self.id, self.info.ip, status.value, reason)
        self.status = status
        self.reason = reason
        for cmd in self.commands.values():
            if cmd.status in (CommandStatus.queued, CommandStatus.running):
                cmd.status = CommandStatus.failed
                cmd.reason = reason
                cmd.ended_at = time.time()

    def close(self) -> dict:
        with self.lock:
            self.log.info("ssh close session=%s ip=%s", self.id, self.info.ip)
            self.stop.set()
            self.mark(SessionStatus.closed, "")
            try:
                if self.channel:
                    self.channel.close()
                if self.client:
                    self.client.close()
            except Exception:
                self.log.exception("ssh close failed session=%s ip=%s", self.id, self.info.ip)
            return self.view().data()

    def watch(self) -> None:
        while not self.stop.is_set():
            try:
                if not self.probe():
                    break
                if self.channel and self.channel.recv_ready():
                    data = self.channel.recv(Limits.recv)
                    if data:
                        text = data.decode(Text.encoding, errors="replace")
                        self.add(text)
                        self.scan(text)
                        self.last_seen_at = time.time()
                        continue
                time.sleep(Limits.poll)
            except Exception as exc:
                with self.lock:
                    self.mark(SessionStatus.disconnected, str(exc))
                    self.log.exception("ssh watch failed session=%s ip=%s", self.id, self.info.ip)
                break

    def add(self, text: str) -> int:
        with self.lock:
            self.seq += 1
            self.chunks.append(OutputChunk(seq=self.seq, text=text))
            if len(self.chunks) > Limits.chunks:
                self.chunks = self.chunks[-Limits.chunks:]
            return self.seq

    def scan(self, text: str) -> None:
        with self.lock:
            self.tail = (self.tail + text)[-Limits.tail:]
            for cmd in self.commands.values():
                start = f"{Mark.prefix}{cmd.command_id}{Mark.start}"
                end = f"{Mark.prefix}{cmd.command_id}{Mark.end}"
                if start in self.tail and cmd.status == CommandStatus.queued:
                    cmd.status = CommandStatus.running
                match = re.search(re.escape(end) + r":(\d+)", self.tail)
                if match and cmd.status in (CommandStatus.queued, CommandStatus.running):
                    cmd.status = CommandStatus.done
                    cmd.ended_at = time.time()
                    cmd.end_seq = self.seq
                    cmd.exit_code = int(match.group(1))

    def send(self, command: str, mode: ExecMode, opts: dict) -> dict:
        if not self.probe():
            raise RuntimeError(Msg.disconnected)
        cmd = CommandInfo(command_id=uuid.uuid4().hex, command=command, start_seq=self.seq)
        payload = self.wrap(cmd.command_id, self.target(command, mode, opts), mode)
        with self.lock:
            self.commands[cmd.command_id] = cmd
            if not self.channel:
                raise RuntimeError(Msg.disconnected)
            self.log.info("ssh exec session=%s command=%s mode=%s", self.id, cmd.command_id, mode.value)
            self.channel.send(payload)
        return cmd.data() | {Field.session_id: self.id}

    def target(self, command: str, mode: ExecMode, opts: dict) -> str:
        if mode == ExecMode.docker:
            container = str(opts.get(Field.container, ""))
            if not container:
                raise ValueError(f"{Msg.missing}: {Field.container}")
            shell = str(opts.get(Field.shell, Default.shell))
            return f"docker exec -i {shlex.quote(container)} {shlex.quote(shell)} -lc {shlex.quote(command)}"
        if mode == ExecMode.k8s:
            ns = str(opts.get(Field.namespace, Default.namespace))
            pod = str(opts.get(Field.pod, ""))
            if not pod:
                raise ValueError(f"{Msg.missing}: {Field.pod}")
            container = str(opts.get(Field.container, ""))
            shell = str(opts.get(Field.shell, Default.shell))
            part = f"kubectl exec -n {shlex.quote(ns)} {shlex.quote(pod)}"
            if container:
                part = f"{part} -c {shlex.quote(container)}"
            return f"{part} -- {shlex.quote(shell)} -lc {shlex.quote(command)}"
        return command

    def wrap(self, command_id: str, command: str, mode: ExecMode) -> str:
        if mode == ExecMode.raw:
            return f"{command}\n"
        start = f"{Mark.prefix}{command_id}{Mark.start}"
        end = f"{Mark.prefix}{command_id}{Mark.end}"
        return (
            f"printf '\\n{start}\\n'; "
            f"( {command} ); "
            f"__mcp_code=$?; "
            f"printf '\\n{end}:%s\\n' \"$__mcp_code\"\n"
        )

    def output(self, command_id: str | None, cursor: int, limit: int) -> dict:
        with self.lock:
            start = cursor
            cmd = None
            if command_id:
                if command_id not in self.commands:
                    raise KeyError(Msg.unknown_command)
                cmd = self.commands[command_id]
                start = max(start, cmd.start_seq)
            selected = [chunk for chunk in self.chunks if chunk.seq > start]
            text = ""
            next_cursor = cursor
            for chunk in selected:
                if len(text) >= limit:
                    break
                text += chunk.text
                next_cursor = chunk.seq
            return {
                Field.session_id: self.id,
                Field.command_id: command_id,
                Field.output: self.clean(text, cmd),
                Field.cursor: cursor,
                Field.next_cursor: next_cursor,
                Field.status: self.status.value,
            }

    def clean(self, text: str, cmd: CommandInfo | None = None) -> str:
        if cmd:
            start = f"{Mark.prefix}{cmd.command_id}{Mark.start}"
            end = f"{Mark.prefix}{cmd.command_id}{Mark.end}"
            if start in text:
                text = text.split(start, 1)[1]
            match = re.search(re.escape(end) + r":\d+", text)
            if match:
                text = text[:match.start()]
        text = re.sub(Regex.start, "", text)
        text = re.sub(Regex.end, "", text)
        text = re.sub(Regex.ansi, "", text)
        return text.strip("\r\n")

    def cmd(self, command_id: str) -> dict:
        with self.lock:
            if command_id not in self.commands:
                raise KeyError(Msg.unknown_command)
            return self.commands[command_id].data() | {Field.session_id: self.id}

    def sftp(self) -> paramiko.SFTPClient:
        if not self.probe() or not self.client:
            raise RuntimeError(Msg.disconnected)
        sftp = self.client.open_sftp()
        self.no_timeout(sftp.get_channel())
        return sftp

    def no_timeout(self, *items) -> None:
        for item in items:
            if hasattr(item, "settimeout"):
                item.settimeout(Limits.transfer_timeout)

    def sftp_download(self, sftp: paramiko.SFTPClient, remote_path: str, local_path: str) -> int:
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        sftp.get(
            remote_path,
            str(path),
            prefetch=True,
            max_concurrent_prefetch_requests=Limits.sftp_prefetch,
        )
        return path.stat().st_size

    def run(self, command: str, data: bytes | None = None) -> bytes:
        if not self.probe() or not self.client:
            raise RuntimeError(Msg.disconnected)
        self.log.info("ssh shell transfer command session=%s ip=%s command=%s", self.id, self.info.ip, command)
        stdin, stdout, stderr = self.client.exec_command(command, timeout=Limits.transfer_timeout)
        self.no_timeout(getattr(stdin, "channel", None), getattr(stdout, "channel", None), getattr(stderr, "channel", None))
        if data is not None:
            stdin.write(data)
            stdin.channel.shutdown_write()
        err = stderr.read().decode(Text.encoding, errors="replace")
        out = stdout.read()
        code = stdout.channel.recv_exit_status()
        if code:
            raise RuntimeError(err.strip() or f"{Msg.remote_failed}: {code}")
        return out

    def once(self, command: str, mode: ExecMode, opts: dict) -> dict:
        if not self.probe() or not self.client:
            raise RuntimeError(Msg.disconnected)
        target = self.target(command, mode, opts)
        self.log.info("ssh once command session=%s ip=%s mode=%s", self.id, self.info.ip, mode.value)
        stdin, stdout, stderr = self.client.exec_command(target, timeout=Limits.transfer_timeout)
        self.no_timeout(getattr(stdin, "channel", None), getattr(stdout, "channel", None), getattr(stderr, "channel", None))
        out = stdout.read().decode(Text.encoding, errors="replace")
        err = stderr.read().decode(Text.encoding, errors="replace")
        code = stdout.channel.recv_exit_status()
        return {
            Field.ip: self.info.ip,
            Field.port: self.info.port,
            Field.username: self.info.username,
            Field.command: command,
            Field.mode: mode.value,
            Field.output: out,
            Field.stderr: err,
            Field.exit_code: code,
            Field.status: CommandStatus.done.value,
            Field.ok: code == 0,
        }

    def run_file(self, command: str, local_path: str) -> int:
        if not self.probe() or not self.client:
            raise RuntimeError(Msg.disconnected)
        self.log.info("ssh shell transfer file session=%s ip=%s command=%s local=%s", self.id, self.info.ip, command, local_path)
        stdin, stdout, stderr = self.client.exec_command(command, timeout=Limits.transfer_timeout)
        self.no_timeout(getattr(stdin, "channel", None), getattr(stdout, "channel", None), getattr(stderr, "channel", None))
        size = 0
        with Path(local_path).open("rb") as file:
            while True:
                chunk = file.read(Limits.file_chunk)
                if not chunk:
                    break
                stdin.write(chunk)
                size += len(chunk)
        stdin.channel.shutdown_write()
        err = stderr.read().decode(Text.encoding, errors="replace")
        stdout.read()
        code = stdout.channel.recv_exit_status()
        if code:
            raise RuntimeError(err.strip() or f"{Msg.remote_failed}: {code}")
        return size

    def shell_upload(self, local_path: str, remote_path: str) -> int:
        parent = posixpath.dirname(remote_path)
        parts = []
        if parent:
            parts.append(f"{ShellCmd.mkdir} {shlex.quote(parent)}")
        parts.append(f"{ShellCmd.cat} > {shlex.quote(remote_path)}")
        return self.run_file(ShellCmd.and_op.join(parts), local_path)

    def shell_download(self, remote_path: str, local_path: str) -> int:
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        command = f"{ShellCmd.cat} {shlex.quote(remote_path)}"
        if not self.probe() or not self.client:
            raise RuntimeError(Msg.disconnected)
        self.log.info("ssh shell download stream session=%s ip=%s command=%s local=%s", self.id, self.info.ip, command, local_path)
        stdin, stdout, stderr = self.client.exec_command(command, timeout=Limits.transfer_timeout)
        self.no_timeout(getattr(stdin, "channel", None), getattr(stdout, "channel", None), getattr(stderr, "channel", None))
        stdin.close()
        size = 0
        with path.open("wb") as file:
            while True:
                chunk = stdout.read(Limits.file_chunk)
                if not chunk:
                    break
                file.write(chunk)
                size += len(chunk)
        err = stderr.read().decode(Text.encoding, errors="replace")
        code = stdout.channel.recv_exit_status()
        if code:
            raise RuntimeError(err.strip() or f"{Msg.remote_failed}: {code}")
        return size


class SessionManager:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.sessions: dict[str, SshSession] = {}

    def reg(self, ip: str, port: int, username: str, password: str) -> dict:
        return self.store.reg(ip, port, username, password)

    def upd(self, old_ip: str, ip: str, port: int, username: str, password: str) -> dict:
        return self.store.upd(old_ip, ip, port, username, password)

    def delete(self, ip: str) -> dict:
        closed = self.close(ip)
        data = self.store.delete(ip)
        data[Field.sessions] = closed
        return data

    def conn(self, ip: str) -> dict:
        return self.open(self.store.get(ip), Origin.registered)

    def temp(self, ip: str, port: int, username: str, password: str) -> dict:
        return self.open(ServerInfo(ip=ip, port=port, username=username, password=password), Origin.temporary)

    def once(self, ip: str, port: int, username: str, password: str, command: str, mode: ExecMode, opts: dict) -> dict:
        session = SshSession(ServerInfo(ip=ip, port=port, username=username, password=password), Origin.temporary)
        data = session.conn()
        try:
            if session.status != SessionStatus.connected:
                raise RuntimeError(data.get(Field.reason, Msg.disconnected))
            return session.once(command, mode, opts)
        finally:
            session.close()

    def open(self, info: ServerInfo, origin: Origin) -> dict:
        session = SshSession(info, origin)
        data = session.conn()
        with self.lock:
            self.sessions[session.id] = session
        return data

    def list(self, ip: str) -> list[dict]:
        with self.lock:
            return [item.view().data() for item in self.sessions.values() if item.info.ip == ip]

    def close(self, ip: str, session_id: str | None = None) -> list[dict]:
        with self.lock:
            items = [
                item for sid, item in self.sessions.items()
                if item.info.ip == ip and (session_id is None or sid == session_id)
            ]
        return [item.close() for item in items]

    def get(self, session_id: str) -> SshSession:
        with self.lock:
            if session_id not in self.sessions:
                raise KeyError(Msg.unknown_session)
            return self.sessions[session_id]

    def send(self, session_id: str, command: str, mode: ExecMode, opts: dict) -> dict:
        return self.get(session_id).send(command, mode, opts)

    def output(self, session_id: str, command_id: str | None, cursor: int, limit: int) -> dict:
        return self.get(session_id).output(command_id, cursor, limit)

    def status(self, session_id: str, command_id: str | None) -> dict:
        session = self.get(session_id)
        data = session.view().data()
        if command_id:
            data[Field.command] = session.cmd(command_id)
        return data

    def pick(self, ip: str, session_id: str | None) -> SshSession | None:
        if session_id:
            return self.get(session_id)
        with self.lock:
            for session in self.sessions.values():
                if session.info.ip == ip and session.probe():
                    return session
        return None

    def transfer(self, ip: str, local_path: str, remote_path: str, upload: bool, session_id: str | None) -> dict:
        session = self.pick(ip, session_id)
        if session:
            return self.transfer_session(session, local_path, remote_path, upload)
        if not self.store.has(ip):
            raise KeyError(Msg.no_credential)
        temp = SshSession(self.store.get(ip), Origin.registered)
        temp.conn()
        try:
            return self.transfer_session(temp, local_path, remote_path, upload)
        finally:
            temp.close()

    def transfer_session(self, session: SshSession, local_path: str, remote_path: str, upload: bool) -> dict:
        if upload:
            local_path = LocalPath.src(local_path)
        else:
            local_path = LocalPath.dst(local_path)
        method = TransferMethod.sftp
        try:
            session.log.info("sftp transfer start session=%s ip=%s upload=%s local=%s remote=%s", session.id, session.info.ip, upload, local_path, remote_path)
            with session.sftp() as sftp:
                if upload:
                    sftp.put(local_path, remote_path)
                    size = Path(local_path).stat().st_size
                else:
                    size = session.sftp_download(sftp, remote_path, local_path)
            session.log.info("sftp transfer ok session=%s ip=%s bytes=%s", session.id, session.info.ip, size)
        except Exception as exc:
            session.log.exception("sftp transfer failed; trying shell fallback session=%s ip=%s", session.id, session.info.ip)
            if not session.probe():
                session.log.exception("sftp transfer disconnected; shell fallback skipped session=%s ip=%s", session.id, session.info.ip)
                raise
            method = TransferMethod.shell
            try:
                if upload:
                    size = session.shell_upload(local_path, remote_path)
                else:
                    size = session.shell_download(remote_path, local_path)
            except Exception:
                session.log.exception("shell transfer fallback failed session=%s ip=%s", session.id, session.info.ip)
                if not session.probe():
                    session.mark(SessionStatus.disconnected, Msg.disconnected)
                raise
            session.log.info("shell transfer fallback ok session=%s ip=%s bytes=%s original=%s", session.id, session.info.ip, size, exc)
        return {
            Field.ip: session.info.ip,
            Field.session_id: session.id,
            Field.local_path: local_path,
            Field.remote_path: remote_path,
            Field.bytes: size,
            Field.method: method,
        }
