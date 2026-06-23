import json

from .constants import Default, ExecMode, Field, JsonKey, Msg, RpcValue, Tool, ToolDesc
from .log import LogFactory
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
            cls.item(Tool.temp, ToolDesc.temp, Schemas.obj({
                Field.ip: text(),
                Field.port: integer(Default.port),
                Field.username: text(),
                Field.password: text(),
            }, [Field.ip, Field.port, Field.username, Field.password])),
            cls.item(Tool.sessions, ToolDesc.sessions, Schemas.obj({Field.ip: text()}, [Field.ip])),
            cls.item(Tool.close, ToolDesc.close, Schemas.obj({
                Field.ip: text(),
                Field.session_id: text(),
            }, [Field.ip])),
            cls.item(Tool.exec, ToolDesc.exec, Schemas.obj({
                Field.session_id: text(),
                Field.command: text(),
                Field.mode: Schemas.mode(),
                Field.container: text(),
                Field.namespace: text(),
                Field.pod: text(),
                Field.shell: text(),
            }, [Field.session_id, Field.command])),
            cls.item(Tool.read, ToolDesc.read, Schemas.obj({
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
        ]


class ToolService:
    def __init__(self, manager: SessionManager):
        self.manager = manager
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
        if name == Tool.register:
            return self.manager.reg(
                str(args[Field.ip]),
                int(args[Field.port]),
                str(args[Field.username]),
                str(args[Field.password]),
            )
        if name == Tool.update:
            return self.manager.upd(
                str(args[Field.old_ip]),
                str(args[Field.ip]),
                int(args[Field.port]),
                str(args[Field.username]),
                str(args[Field.password]),
            )
        if name == Tool.delete:
            return self.manager.delete(str(args[Field.ip]))
        if name == Tool.connect:
            return self.manager.conn(str(args[Field.ip]))
        if name == Tool.temp:
            return self.manager.temp(
                str(args[Field.ip]),
                int(args[Field.port]),
                str(args[Field.username]),
                str(args[Field.password]),
            )
        if name == Tool.sessions:
            return {Field.sessions: self.manager.list(str(args[Field.ip]))}
        if name == Tool.close:
            return {Field.sessions: self.manager.close(str(args[Field.ip]), args.get(Field.session_id))}
        if name == Tool.exec:
            mode = ExecMode(str(args.get(Field.mode, ExecMode.host.value)))
            return self.manager.exec(str(args[Field.session_id]), str(args[Field.command]), mode, args)
        if name == Tool.read:
            return self.manager.read(
                str(args[Field.session_id]),
                args.get(Field.command_id),
                int(args.get(Field.cursor, 0)),
                int(args.get(Field.limit, Default.read)),
            )
        if name == Tool.status:
            return self.manager.status(str(args[Field.session_id]), args.get(Field.command_id))
        if name == Tool.upload:
            return self.manager.transfer(
                str(args[Field.ip]),
                str(args[Field.local_path]),
                str(args[Field.remote_path]),
                True,
                args.get(Field.session_id),
            )
        if name == Tool.download:
            return self.manager.transfer(
                str(args[Field.ip]),
                str(args[Field.local_path]),
                str(args[Field.remote_path]),
                False,
                args.get(Field.session_id),
            )
        if name == Tool.servers:
            return {Field.servers: self.manager.store.list()}
        raise KeyError(Msg.tool_not_found)

    def result(self, data: dict | list, is_error: bool) -> dict:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return {
            JsonKey.content: [{JsonKey.type: RpcValue.text, JsonKey.text: text}],
            JsonKey.structured: data if isinstance(data, dict) else {JsonKey.items: data},
            JsonKey.is_error: is_error,
        }


class ToolFactory:
    @staticmethod
    def make() -> ToolService:
        return ToolService(SessionManager(ServerStore()))
