import json
from pathlib import Path

from .constants import Default, ExecMode, Field, JsonKey, Msg, ServiceEntry, ServiceFile, ServiceName, RpcValue, Tool, ToolDesc
from .log import LogFactory
from .service_host import ServiceHost, ServiceSpec
from .service_client import ToolClient
from .session import SessionManager
from .store import ServerStore


class Schemas:
    @staticmethod
    def obj(props: dict, req: list[str]) -> dict:
        return {
            JsonKey.type: RpcValue.object,
            JsonKey.properties: props,
            JsonKey.required: req,
            JsonKey.additional: False,
        }

    @staticmethod
    def text(desc: str = "") -> dict:
        data = {JsonKey.type: RpcValue.string}
        if desc:
            data[JsonKey.description] = desc
        return data

    @staticmethod
    def integer(default: int | None = None) -> dict:
        data = {JsonKey.type: RpcValue.integer}
        if default is not None:
            data[JsonKey.default] = default
        return data

    @staticmethod
    def array(item: dict) -> dict:
        return {JsonKey.type: RpcValue.array, JsonKey.items: item}

    @staticmethod
    def mode() -> dict:
        return {JsonKey.type: RpcValue.string, JsonKey.enum: [item.value for item in ExecMode]}


class ToolCatalog:
    @staticmethod
    def item(name: str, desc: str, schema: dict) -> dict:
        return {
            JsonKey.name: name,
            JsonKey.description: desc,
            JsonKey.input_schema: schema,
        }

    @classmethod
    def list(cls) -> list[dict]:
        text = Schemas.text
        integer = Schemas.integer
        return [
            cls.item(Tool.register, ToolDesc.register, Schemas.obj({
                Field.ip: text(),
                Field.port: integer(Default.port),
                Field.username: text(),
                Field.password: text(),
            }, [Field.ip, Field.port, Field.username, Field.password])),
            cls.item(Tool.update, ToolDesc.update, Schemas.obj({
                Field.old_ip: text(),
                Field.ip: text(),
                Field.port: integer(Default.port),
                Field.username: text(),
                Field.password: text(),
            }, [Field.old_ip, Field.ip, Field.port, Field.username, Field.password])),
            cls.item(Tool.delete, ToolDesc.delete, Schemas.obj({
                Field.ip: text(),
            }, [Field.ip])),
            cls.item(Tool.connect, ToolDesc.connect, Schemas.obj({Field.ip: text()}, [Field.ip])),
            cls.item(Tool.once, ToolDesc.once, Schemas.obj({
                Field.ip: text(),
                Field.port: integer(Default.port),
                Field.username: text(),
                Field.password: text(),
                Field.command: text(),
                Field.mode: Schemas.mode(),
                Field.container: text(),
                Field.namespace: text(),
                Field.pod: text(),
                Field.shell: text(),
            }, [Field.ip, Field.port, Field.username, Field.password, Field.command])),
            cls.item(Tool.sessions, ToolDesc.sessions, Schemas.obj({Field.ip: text()}, [Field.ip])),
            cls.item(Tool.close, ToolDesc.close, Schemas.obj({
                Field.ip: text(),
                Field.session_id: text(),
            }, [Field.ip])),
            cls.item(Tool.send, ToolDesc.send, Schemas.obj({
                Field.session_id: text(),
                Field.input: text(),
                Field.mode: Schemas.mode(),
                Field.container: text(),
                Field.namespace: text(),
                Field.pod: text(),
                Field.shell: text(),
            }, [Field.session_id, Field.input])),
            cls.item(Tool.send_many, ToolDesc.send_many, Schemas.obj({
                Field.items: Schemas.array(Schemas.obj({
                    Field.session_id: text(),
                    Field.input: text(),
                    Field.mode: Schemas.mode(),
                    Field.container: text(),
                    Field.namespace: text(),
                    Field.pod: text(),
                    Field.shell: text(),
                }, [Field.session_id, Field.input])),
            }, [Field.items])),
            cls.item(Tool.output, ToolDesc.output, Schemas.obj({
                Field.session_id: text(),
                Field.command_id: text(),
                Field.cursor: integer(0),
                Field.limit: integer(Default.read),
            }, [Field.session_id])),
            cls.item(Tool.status, ToolDesc.status, Schemas.obj({
                Field.session_id: text(),
                Field.command_id: text(),
            }, [Field.session_id])),
            cls.item(Tool.upload, ToolDesc.upload, Schemas.obj({
                Field.ip: text(),
                Field.local_path: text(),
                Field.remote_path: text(),
                Field.session_id: text(),
            }, [Field.ip, Field.local_path, Field.remote_path])),
            cls.item(Tool.download, ToolDesc.download, Schemas.obj({
                Field.ip: text(),
                Field.remote_path: text(),
                Field.local_path: text(),
                Field.session_id: text(),
            }, [Field.ip, Field.remote_path, Field.local_path])),
            cls.item(Tool.servers, ToolDesc.servers, Schemas.obj({}, [])),
            cls.item(Tool.version, ToolDesc.version, Schemas.obj({}, [])),
            cls.item(Tool.service_start, ToolDesc.service_start, Schemas.obj({}, [])),
        ]


class ToolResult:
    @staticmethod
    def make(data: dict | list, is_error: bool) -> dict:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return {
            JsonKey.content: [{JsonKey.type: RpcValue.text, JsonKey.text: text}],
            JsonKey.structured: data if isinstance(data, dict) else {JsonKey.items: data},
            JsonKey.is_error: is_error,
        }


class ToolService:
    def __init__(self, manager: SessionManager):
        self.manager = manager
        self.host = ServiceHost(ServiceSpec(
            ServiceName.tool,
            Path(__file__).resolve().with_name(ServiceFile.tool),
            ServiceEntry.tool,
        ))
        self.log = LogFactory.get()

    def call(self, name: str, args: dict) -> dict:
        try:
            self.log.info("tool call start name=%s", name)
            data = self.route(name, args or {})
            self.log.info("tool call ok name=%s", name)
            return self.result(data, False)
        except Exception as exc:
            self.log.exception("tool call failed name=%s", name)
            return self.result({Field.reason: str(exc)}, True)

    def route(self, name: str, args: dict) -> dict | list:
        if name == Tool.service_start:
            return {Field.status: Msg.ok}
        if name == Tool.version:
            return self.host.info()
        return self.host.api(self.manager).route(name, args)

    def result(self, data: dict | list, is_error: bool) -> dict:
        return ToolResult.make(data, is_error)


class ToolFacade:
    def __init__(self, client: ToolClient | None = None):
        self.client = client or ToolClient()
        self.log = LogFactory.get()

    def call(self, name: str, args: dict) -> dict:
        try:
            self.log.info("facade call start name=%s", name)
            if name == Tool.service_start:
                data = ToolResult.make(self.client.start(), False)
            else:
                data = self.client.call(name, args or {})
            self.log.info("facade call ok name=%s", name)
            return data
        except Exception as exc:
            self.log.exception("facade call failed name=%s", name)
            return self.result({Field.reason: str(exc)}, True)

    def result(self, data: dict | list, is_error: bool) -> dict:
        return ToolResult.make(data, is_error)


class ToolFactory:
    @staticmethod
    def make() -> ToolFacade:
        return ToolFacade()

    @staticmethod
    def make_local() -> ToolService:
        return ToolService(SessionManager(ServerStore()))
