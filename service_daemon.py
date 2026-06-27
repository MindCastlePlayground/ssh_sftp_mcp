from __future__ import annotations

import json
import socket
import threading

from .constants import Field, JsonKey, Msg, ServiceCmd, ServiceDefault, Text
from .log import LogFactory
from .service_client import ServiceConfig
from .tools import ToolFactory


class ServiceResponse:
    @staticmethod
    def ok(result: dict | None = None) -> dict:
        return {Field.ok: True, JsonKey.result: result or {}}

    @staticmethod
    def err(reason: str) -> dict:
        return {Field.ok: False, Field.reason: reason}


class ServiceDaemon:
    def __init__(self, cfg: ServiceConfig | None = None) -> None:
        self.cfg = cfg or ServiceConfig(start=False)
        self.service = ToolFactory.make_local()
        self.log = LogFactory.get()

    def run(self) -> None:
        self.log.info("service listen host=%s port=%s", self.cfg.host, self.cfg.port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.cfg.host, self.cfg.port))
            server.listen(ServiceDefault.backlog)
            while True:
                conn, addr = server.accept()
                thread = threading.Thread(target=self.handle, args=(conn, addr), daemon=True)
                thread.start()

    def handle(self, conn: socket.socket, addr) -> None:
        with conn:
            try:
                req = self.read(conn)
                res = self.route(req)
            except Exception as exc:
                self.log.exception("service request failed addr=%s", addr)
                res = ServiceResponse.err(str(exc))
            self.write(conn, res)

    def read(self, conn: socket.socket) -> dict:
        chunks = bytearray()
        while len(chunks) < ServiceDefault.recv:
            part = conn.recv(65536)
            if not part:
                break
            chunks.extend(part)
            if b"\n" in part:
                break
        line = bytes(chunks).split(b"\n", 1)[0]
        if not line:
            raise ValueError(Msg.invalid_request)
        data = json.loads(line.decode(Text.encoding))
        if not isinstance(data, dict):
            raise ValueError(Msg.invalid_request)
        return data

    def route(self, req: dict) -> dict:
        cmd = req.get(Field.command)
        if cmd == ServiceCmd.ping:
            return ServiceResponse.ok()
        if cmd == ServiceCmd.call:
            name = str(req.get(JsonKey.name, ""))
            args = req.get(JsonKey.arguments, {})
            if not isinstance(args, dict):
                raise ValueError(Msg.invalid_request)
            return ServiceResponse.ok(self.service.call(name, args))
        raise KeyError(Msg.service_bad_cmd)

    def write(self, conn: socket.socket, res: dict) -> None:
        text = json.dumps(res, ensure_ascii=False, separators=(",", ":")) + "\n"
        conn.sendall(text.encode(Text.encoding))
