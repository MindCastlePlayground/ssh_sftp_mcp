import json
import os
from pathlib import Path
from threading import RLock

from .constants import Env, Field, Msg, Store, Text
from .log import LogFactory
from .models import ServerInfo


class ServerStore:
    def __init__(self, root: Path | None = None):
        env = os.environ.get(Env.home)
        if root:
            self.root = root
        elif env:
            self.root = Path(env)
        else:
            self.root = Path.cwd() / Store.dir
        self.path = self.root / Store.servers
        self.lock = RLock()
        self.items: dict[str, ServerInfo] = {}
        self.mtime = 0
        self.log = LogFactory.setup(self.root)
        self.load()

    def load(self) -> None:
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            if not self.path.exists():
                self.items = {}
                self.mtime = 0
                return
            try:
                data = json.loads(self.path.read_text(encoding=Text.encoding))
                self.items = {
                    str(ip): ServerInfo.from_data(row)
                    for ip, row in data.get(Field.servers, {}).items()
                }
                self.mtime = self.path.stat().st_mtime_ns
                self.log.info("registry loaded path=%s servers=%s", self.path, len(self.items))
            except Exception:
                self.log.exception(Msg.bad_store)

    def save(self) -> None:
        with self.lock:
            data = {Field.servers: {ip: info.data() for ip, info in self.items.items()}}
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding=Text.encoding)
            self.mtime = self.path.stat().st_mtime_ns
            self.log.info("registry saved path=%s servers=%s", self.path, len(self.items))

    def sync(self) -> None:
        with self.lock:
            if not self.path.exists():
                if self.mtime:
                    self.items = {}
                    self.mtime = 0
                    self.log.info("registry cleared path=%s", self.path)
                return
            mtime = self.path.stat().st_mtime_ns
            if mtime != self.mtime:
                self.load()

    def reg(self, ip: str, port: int, username: str, password: str) -> dict:
        with self.lock:
            self.sync()
            if ip in self.items:
                raise ValueError(Msg.duplicate_ip)
            self.items[ip] = ServerInfo(ip=ip, port=port, username=username, password=password)
            self.save()
            return self.items[ip].public()

    def upd(self, old_ip: str, ip: str, port: int, username: str, password: str) -> dict:
        with self.lock:
            self.sync()
            if old_ip not in self.items:
                raise KeyError(Msg.unknown_ip)
            if old_ip != ip and ip in self.items:
                raise ValueError(Msg.duplicate_ip)
            if old_ip != ip:
                del self.items[old_ip]
            self.items[ip] = ServerInfo(ip=ip, port=port, username=username, password=password)
            self.save()
            data = self.items[ip].public()
            data[Field.status] = Msg.updated
            return data

    def delete(self, ip: str) -> dict:
        with self.lock:
            self.sync()
            if ip not in self.items:
                raise KeyError(Msg.unknown_ip)
            del self.items[ip]
            self.save()
            return {Field.ip: ip, Field.status: Msg.deleted}

    def get(self, ip: str) -> ServerInfo:
        with self.lock:
            self.sync()
            if ip not in self.items:
                raise KeyError(Msg.unknown_ip)
            return self.items[ip]

    def has(self, ip: str) -> bool:
        with self.lock:
            self.sync()
            return ip in self.items

    def list(self) -> list[dict]:
        with self.lock:
            self.sync()
            return [item.public() for item in self.items.values()]
