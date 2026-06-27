from __future__ import annotations

import re
import shlex
import threading
import time
import uuid
import posixpath
from pathlib import Path

import paramiko
from netmiko import ConnectHandler
from netmiko.base_connection import BaseConnection

from .constants import CommandStatus, Default, ExecMode, Field, Limits, Msg, NetmikoKey, NetmikoValue, Origin, Prompt, Regex, SessionStatus, Shell, ShellCmd, Text, TransferMethod
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


class NetmikoTransport:
    def __init__(self, info: ServerInfo):
        self.info = info
        self.conn: BaseConnection | None = None

    def params(self) -> dict:
        return {
            NetmikoKey.device_type: NetmikoValue.terminal,
            NetmikoKey.host: self.info.ip,
            NetmikoKey.port: self.info.port,
            NetmikoKey.username: self.info.username,
            NetmikoKey.password: self.info.password,
            NetmikoKey.conn_timeout: Limits.connect_timeout,
            NetmikoKey.auth_timeout: Limits.connect_timeout,
            NetmikoKey.banner_timeout: Limits.connect_timeout,
            NetmikoKey.blocking_timeout: Limits.blocking_timeout,
            NetmikoKey.timeout: Limits.session_timeout,
            NetmikoKey.session_timeout: Limits.session_timeout,
            NetmikoKey.keepalive: Limits.keepalive,
            NetmikoKey.allow_agent: False,
            NetmikoKey.use_keys: False,
            NetmikoKey.fast_cli: False,
            NetmikoKey.encoding: Text.encoding,
            NetmikoKey.default_enter: Shell.enter,
        }

    def conn_open(self) -> None:
        self.conn = ConnectHandler(**self.params())

    def alive(self) -> bool:
        if not self.conn:
            return False
        remote = getattr(self.conn, "remote_conn", None)
        transport = getattr(remote, "transport", None)
        if not remote or getattr(remote, "closed", False):
            return False
        return bool(transport and transport.is_active())

    def close(self) -> None:
        if self.conn:
            self.conn.disconnect()
            self.conn = None

    def write(self, text: str) -> None:
        if not self.conn:
            raise RuntimeError(Msg.disconnected)
        self.conn.write_channel(text)

    def read(self) -> str:
        if not self.conn:
            return Text.empty
        return self.conn.read_channel()

    def ssh(self) -> paramiko.SSHClient:
        if not self.conn:
            raise RuntimeError(Msg.disconnected)
        client = getattr(self.conn, "remote_conn_pre", None)
        if not client:
            raise RuntimeError(Msg.disconnected)
        return client

    def sftp(self) -> paramiko.SFTPClient:
        return self.ssh().open_sftp()

    def exec(self, command: str):
        return self.ssh().exec_command(command, timeout=Limits.transfer_timeout)


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
        self.net: NetmikoTransport | None = None
        self.reader: threading.Thread | None = None
        self.stop = threading.Event()
        self.seq = 0
        self.tail = ""
        self.prompt = ""
        self.chunks: list[OutputChunk] = []
        self.commands: dict[str, CommandInfo] = {}
        self.log = LogFactory.get()

    def conn(self) -> dict:
        try:
            self.log.info("ssh connect start ip=%s port=%s user=%s session=%s", self.info.ip, self.info.port, self.info.username, self.id)
            self.net = NetmikoTransport(self.info)
            self.net.conn_open()
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
        if not self.net:
            return
        self.net.write(Shell.echo_off)
        time.sleep(Limits.prep_wait)
        self.drain()
        self.net.write(Shell.enter)
        time.sleep(Limits.prep_wait)
        self.prompt = self.find_prompt(self.drain())

    def drain(self) -> str:
        if not self.net:
            return Text.empty
        return self.net.read()

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
            if not self.net or not self.net.alive():
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
                if self.net:
                    self.net.close()
            except Exception:
                self.log.exception("ssh close failed session=%s ip=%s", self.id, self.info.ip)
            return self.view().data()

    def watch(self) -> None:
        while not self.stop.is_set():
            try:
                if not self.probe():
                    break
                if self.net:
                    text = self.net.read()
                    if text:
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
            start = self.seq
            self.seq += len(text)
            self.chunks.append(OutputChunk(seq=self.seq, text=text, start=start))
            if len(self.chunks) > Limits.chunks:
                self.chunks = self.chunks[-Limits.chunks:]
            return self.seq

    def scan(self, text: str) -> None:
        with self.lock:
            self.tail = (self.tail + text)[-Limits.tail:]
            cmd = self.active()
            if cmd and self.prompted(self.tail):
                cmd.status = CommandStatus.done
                cmd.ended_at = time.time()
                cmd.end_seq = self.seq

    def send(self, command: str, mode: ExecMode, opts: dict) -> dict:
        if not self.probe():
            raise RuntimeError(Msg.disconnected)
        target = self.target(command, mode, opts)
        cmd = CommandInfo(command_id=uuid.uuid4().hex, command=command, sent=target, start_seq=self.seq)
        payload = self.line(target, mode)
        with self.lock:
            if mode != ExecMode.raw and self.active():
                raise RuntimeError(Msg.command_running)
            self.commands[cmd.command_id] = cmd
            if not self.net:
                raise RuntimeError(Msg.disconnected)
            self.log.info("ssh exec session=%s command=%s mode=%s", self.id, cmd.command_id, mode.value)
            net = self.net
        try:
            self.write(net, payload)
        except Exception as exc:
            with self.lock:
                cmd.status = CommandStatus.failed
                cmd.ended_at = time.time()
                cmd.reason = str(exc)
            raise
        if mode == ExecMode.raw:
            with self.lock:
                cmd.status = CommandStatus.done
                cmd.ended_at = time.time()
                cmd.reason = Msg.raw_sent
        else:
            with self.lock:
                cmd.status = CommandStatus.running
        return cmd.data() | {Field.session_id: self.id}

    def active(self) -> CommandInfo | None:
        for cmd in sorted(self.commands.values(), key=lambda item: item.started_at):
            if cmd.status in (CommandStatus.queued, CommandStatus.running):
                return cmd
        return None

    def write(self, net: NetmikoTransport, payload: str) -> None:
        net.write(payload)

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

    def line(self, command: str, mode: ExecMode) -> str:
        if mode == ExecMode.raw:
            return f"{command}{Shell.enter}"
        return f"{command.rstrip()}{Shell.enter}"

    def prompted(self, text: str) -> bool:
        clean = re.sub(Regex.ansi, Text.empty, text)
        clean = clean.replace("\r\n", Shell.enter).replace("\r", Shell.enter)
        if self.prompt and clean.rstrip().endswith(self.prompt.rstrip()):
            return True
        return bool(re.search(Prompt.pattern, clean))

    def find_prompt(self, text: str) -> str:
        clean = re.sub(Regex.ansi, Text.empty, text)
        clean = clean.replace("\r\n", Shell.enter).replace("\r", Shell.enter)
        lines = [line for line in clean.split(Shell.enter) if line.strip()]
        if lines and re.search(Prompt.line, lines[-1]):
            return lines[-1]
        return Text.empty

    def output(self, command_id: str | None, cursor: int, limit: int) -> dict:
        with self.lock:
            start = cursor
            cmd = None
            if command_id:
                if command_id not in self.commands:
                    raise KeyError(Msg.unknown_command)
                cmd = self.commands[command_id]
                start = max(start, cmd.start_seq)
            text = ""
            next_cursor = start
            for chunk in self.chunks:
                if chunk.seq <= start:
                    continue
                if len(text) >= limit:
                    break
                pos = max(start, chunk.start)
                off = pos - chunk.start
                room = limit - len(text)
                part = chunk.text[off:off + room]
                text += part
                next_cursor = pos + len(part)
            return {
                Field.session_id: self.id,
                Field.command_id: command_id,
                Field.output: self.clean(text, cmd),
                Field.cursor: cursor,
                Field.next_cursor: next_cursor,
                Field.status: self.status.value,
            }

    def clean(self, text: str, cmd: CommandInfo | None = None) -> str:
        text = re.sub(Regex.ansi, "", text)
        if cmd:
            text = self.strip_echo(text, cmd.sent or cmd.command)
            text = self.strip_prompt(text)
        return text.strip("\r\n")

    def strip_echo(self, text: str, command: str) -> str:
        lines = text.splitlines()
        if lines and lines[0].strip() == command.strip():
            return Shell.enter.join(lines[1:])
        return text

    def strip_prompt(self, text: str) -> str:
        if self.prompt:
            prompt = self.prompt.rstrip()
            if text.rstrip().endswith(prompt):
                return text[:text.rfind(prompt)].rstrip("\r\n")
        lines = text.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        if lines and re.search(Prompt.line, lines[-1]):
            lines.pop()
        return Shell.enter.join(lines)

    def cmd(self, command_id: str) -> dict:
        with self.lock:
            if command_id not in self.commands:
                raise KeyError(Msg.unknown_command)
            return self.commands[command_id].data() | {Field.session_id: self.id}

    def sftp(self) -> paramiko.SFTPClient:
        if not self.probe() or not self.net:
            raise RuntimeError(Msg.disconnected)
        sftp = self.net.sftp()
        channel = sftp.get_channel()
        channel.settimeout(Limits.transfer_timeout)
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
        if not self.probe() or not self.net:
            raise RuntimeError(Msg.disconnected)
        self.log.info("ssh shell transfer command session=%s ip=%s command=%s", self.id, self.info.ip, command)
        stdin, stdout, stderr = self.net.exec(command)
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
        if not self.probe() or not self.net:
            raise RuntimeError(Msg.disconnected)
        target = self.target(command, mode, opts)
        self.log.info("ssh once command session=%s ip=%s mode=%s", self.id, self.info.ip, mode.value)
        stdin, stdout, stderr = self.net.exec(target)
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
        if not self.probe() or not self.net:
            raise RuntimeError(Msg.disconnected)
        self.log.info("ssh shell transfer file session=%s ip=%s command=%s local=%s", self.id, self.info.ip, command, local_path)
        stdin, stdout, stderr = self.net.exec(command)
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
        if not self.probe() or not self.net:
            raise RuntimeError(Msg.disconnected)
        self.log.info("ssh shell download stream session=%s ip=%s command=%s local=%s", self.id, self.info.ip, command, local_path)
        stdin, stdout, stderr = self.net.exec(command)
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
