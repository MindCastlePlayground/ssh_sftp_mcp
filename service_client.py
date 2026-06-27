from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from .constants import Arg, Env, EnvValue, Field, JsonKey, ModuleName, Msg, ServiceCmd, ServiceDefault, Text
from .log import LogFactory


class ServiceConfig:
    def __init__(self, host: str | None = None, port: int | None = None, start: bool | None = None) -> None:
        self.host = host or os.environ.get(Env.service_host, ServiceDefault.host)
        self.port = port if port is not None else int(os.environ.get(Env.service_port, ServiceDefault.port))
        self.start = self.auto() if start is None else start

    def auto(self) -> bool:
        value = os.environ.get(Env.service_start, "1").lower()
        return value not in (EnvValue.false, EnvValue.no, EnvValue.off)


class ServiceProc:
    def __init__(self, cfg: ServiceConfig) -> None:
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.log = LogFactory.get()

    def ready(self) -> None:
        if self.live():
            return
        if not self.cfg.start:
            raise RuntimeError(Msg.service_down)
        self.start()
        self.wait()

    def live(self) -> bool:
        try:
            ServiceWire.ask(self.cfg, {Field.command: ServiceCmd.ping})
            return True
        except OSError:
            return False
        except Exception:
            return False

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        env = os.environ.copy()
        env[Env.service_host] = self.cfg.host
        env[Env.service_port] = str(self.cfg.port)
        env[Env.pythonpath] = self.path(env.get(Env.pythonpath, ""))
        python = os.environ.get(Env.python, sys.executable)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.log.info("service start host=%s port=%s python=%s", self.cfg.host, self.cfg.port, python)
        self.proc = subprocess.Popen(
            [python, "-m", ModuleName.package, Arg.service],
            cwd=str(Path.cwd()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=None,
            creationflags=flags,
        )

    def path(self, old: str) -> str:
        root = str(Path(__file__).resolve().parent.parent)
        return root if not old else os.pathsep.join([root, old])

    def wait(self) -> None:
        end = time.time() + ServiceDefault.wait
        last = None
        while time.time() < end:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(f"{Msg.service_down}: {self.proc.returncode}")
            try:
                ServiceWire.ask(self.cfg, {Field.command: ServiceCmd.ping})
                return
            except Exception as exc:
                last = exc
                time.sleep(ServiceDefault.retry_sleep)
        raise RuntimeError(f"{Msg.service_down}: {last}")


class ServiceWire:
    @staticmethod
    def ask(cfg: ServiceConfig, req: dict) -> dict:
        text = json.dumps(req, ensure_ascii=False, separators=(",", ":")) + "\n"
        data = text.encode(Text.encoding)
        with socket.create_connection((cfg.host, cfg.port), timeout=ServiceDefault.connect_timeout) as sock:
            sock.settimeout(None)
            sock.sendall(data)
            return ServiceWire.read(sock)

    @staticmethod
    def read(sock: socket.socket) -> dict:
        chunks = bytearray()
        while len(chunks) < ServiceDefault.recv:
            part = sock.recv(65536)
            if not part:
                break
            chunks.extend(part)
            if b"\n" in part:
                break
        line = bytes(chunks).split(b"\n", 1)[0]
        if not line:
            raise RuntimeError(Msg.service_down)
        return json.loads(line.decode(Text.encoding))


class ToolClient:
    def __init__(self, cfg: ServiceConfig | None = None, proc: ServiceProc | None = None) -> None:
        self.cfg = cfg or ServiceConfig()
        self.proc = proc or ServiceProc(self.cfg)

    def call(self, name: str, args: dict) -> dict:
        self.proc.ready()
        res = self.send({
            Field.command: ServiceCmd.call,
            JsonKey.name: name,
            JsonKey.arguments: args,
        })
        if not res.get(Field.ok):
            raise RuntimeError(str(res.get(Field.reason, Msg.service_down)))
        return res[JsonKey.result]

    def start(self) -> dict:
        self.proc.ready()
        return {
            Field.status: Msg.ok,
            Field.host: self.cfg.host,
            Field.port: self.cfg.port,
        }

    def send(self, req: dict) -> dict:
        try:
            return ServiceWire.ask(self.cfg, req)
        except OSError:
            self.proc.ready()
            return ServiceWire.ask(self.cfg, req)
