import json
from pathlib import Path

from .constants import Auth, Default, ExecMode, Field, JsonKey, Msg, Protocol, RpcValue, ServiceEntry, ServiceFile, ServiceName, Tool, ToolDesc, WinrmDefault, WinrmShell
from .log import LogFactory
from .service_host import ServiceHost, ServiceSpec
from .service_client import ToolClient
from .session import SessionManager
from .store import ServerStore
from .transfer_constants import TransferOption


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

    @staticmethod
    def flag(default: bool = False) -> dict:
        return {JsonKey.type: RpcValue.boolean, JsonKey.default: default}

    @staticmethod
    def choice(values: tuple[str, ...] | list[str], default: str | None = None) -> dict:
        data = {JsonKey.type: RpcValue.string, JsonKey.enum: list(values)}
        if default is not None:
            data[JsonKey.default] = default
        return data


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
                Field.protocol: Schemas.choice((Protocol.ssh.value, Protocol.winrm.value), Protocol.ssh.value),
                Field.ssl: Schemas.flag(False),
                Field.auth: Schemas.choice(Auth.values, Auth.ntlm),
            }, [Field.ip, Field.port, Field.username, Field.password])),
            cls.item(Tool.update, ToolDesc.update, Schemas.obj({
                Field.old_ip: text(),
                Field.ip: text(),
                Field.port: integer(Default.port),
                Field.username: text(),
                Field.password: text(),
                Field.protocol: Schemas.choice((Protocol.ssh.value, Protocol.winrm.value)),
                Field.ssl: Schemas.flag(False),
                Field.auth: Schemas.choice(Auth.values),
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
                Field.protocol: Schemas.choice((Protocol.ssh.value, Protocol.winrm.value), Protocol.ssh.value),
                Field.ssl: Schemas.flag(False),
                Field.auth: Schemas.choice(Auth.values, Auth.ntlm),
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
                TransferOption.workers: integer(32),
                TransferOption.block_size: integer(131072),
                TransferOption.verify: {JsonKey.type: RpcValue.string, JsonKey.enum: [TransferOption.auto, TransferOption.readback]},
            }, [Field.ip, Field.local_path, Field.remote_path])),
            cls.item(Tool.download, ToolDesc.download, Schemas.obj({
                Field.ip: text(),
                Field.remote_path: text(),
                Field.local_path: text(),
                Field.session_id: text(),
                TransferOption.workers: integer(32),
                TransferOption.block_size: integer(131072),
                TransferOption.verify: {JsonKey.type: RpcValue.string, JsonKey.enum: [TransferOption.auto, TransferOption.readback]},
            }, [Field.ip, Field.remote_path, Field.local_path])),
            cls.item(Tool.servers, ToolDesc.servers, Schemas.obj({}, [])),
            cls.item(Tool.version, ToolDesc.version, Schemas.obj({}, [])),
            cls.item(Tool.service_start, ToolDesc.service_start, Schemas.obj({}, [])),
            cls.item(Tool.winrm_register, ToolDesc.winrm_register, Schemas.obj({
                Field.ip: text(),
                Field.port: integer(WinrmDefault.port),
                Field.username: text(),
                Field.password: text(),
                Field.ssl: Schemas.flag(False),
                Field.auth: Schemas.choice(Auth.values, Auth.ntlm),
            }, [Field.ip, Field.username, Field.password])),
            cls.item(Tool.winrm_connect, ToolDesc.winrm_connect, Schemas.obj({Field.ip: text()}, [Field.ip])),
            cls.item(Tool.winrm_send, ToolDesc.winrm_send, Schemas.obj({
                Field.session_id: text(),
                Field.input: text(),
                Field.shell: Schemas.choice(WinrmShell.values, WinrmShell.powershell),
            }, [Field.session_id, Field.input])),
            cls.item(Tool.winrm_output, ToolDesc.winrm_output, Schemas.obj({
                Field.session_id: text(),
                Field.command_id: text(),
                Field.cursor: integer(0),
                Field.limit: integer(Default.read),
            }, [Field.session_id])),
            cls.item(Tool.winrm_once, ToolDesc.winrm_once, Schemas.obj({
                Field.ip: text(),
                Field.port: integer(WinrmDefault.port),
                Field.username: text(),
                Field.password: text(),
                Field.command: text(),
                Field.shell: Schemas.choice(WinrmShell.values, WinrmShell.powershell),
                Field.ssl: Schemas.flag(False),
                Field.auth: Schemas.choice(Auth.values, Auth.ntlm),
            }, [Field.ip, Field.username, Field.password, Field.command])),
            cls.item(Tool.winrm_upload, ToolDesc.winrm_upload, Schemas.obj({
                Field.ip: text(),
                Field.local_path: text(),
                Field.remote_path: text(),
                Field.session_id: text(),
            }, [Field.ip, Field.local_path, Field.remote_path])),
            cls.item(Tool.winrm_download, ToolDesc.winrm_download, Schemas.obj({
                Field.ip: text(),
                Field.remote_path: text(),
                Field.local_path: text(),
                Field.session_id: text(),
            }, [Field.ip, Field.remote_path, Field.local_path])),
            cls.item(Tool.winrm_close, ToolDesc.winrm_close, Schemas.obj({
                Field.ip: text(),
                Field.session_id: text(),
            }, [Field.ip])),
            cls.item(Tool.winrm_sessions, ToolDesc.winrm_sessions, Schemas.obj({Field.ip: text()}, [Field.ip])),
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
