from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from ssh_sftp_mcp.constants import Auth, Default, ExecMode, Field, Limits, Msg, Protocol, Tool, WinrmDefault
from ssh_sftp_mcp.winrm import Flags


class BatchExec:
    @staticmethod
    def one(manager, index: int, item: dict) -> dict:
        try:
            if not isinstance(item, dict):
                raise TypeError(Msg.bad_batch)
            mode = ExecMode(str(item.get(Field.mode, ExecMode.host.value)))
            inp = item.get(Field.input)
            if inp is None:
                raise KeyError(Msg.missing)
            data = manager.send(str(item[Field.session_id]), str(inp), mode, item)
            data[Field.index] = index
            data[Field.ok] = True
            return data
        except Exception as exc:
            return {
                Field.index: index,
                Field.session_id: item.get(Field.session_id) if isinstance(item, dict) else None,
                Field.input: item.get(Field.input) if isinstance(item, dict) else None,
                Field.ok: False,
                Field.status: Msg.bad_batch if not isinstance(item, dict) else Msg.remote_failed,
                Field.reason: str(exc),
            }

    @classmethod
    def run(cls, manager, items: list) -> dict:
        if not isinstance(items, list):
            raise TypeError(Msg.bad_batch)
        if len(items) > Limits.batch:
            raise ValueError(f"{Msg.batch_too_large}: {Limits.batch}")
        results: list[dict | None] = [None] * len(items)
        workers = max(1, min(len(items), Limits.batch_workers))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(cls.one, manager, index, item): index
                for index, item in enumerate(items)
            }
            for future in as_completed(futures):
                index = futures[future]
                results[index] = future.result()
        return {Field.items: [item for item in results if item is not None]}


class ToolApi:
    def __init__(self, manager):
        self.manager = manager

    @staticmethod
    def proto(args: dict, default: str) -> str:
        value = str(args.get(Field.protocol, default))
        if value not in (Protocol.ssh.value, Protocol.winrm.value):
            raise ValueError(Msg.bad_protocol)
        return value

    @staticmethod
    def auth(args: dict) -> str:
        if Field.auth not in args or args.get(Field.auth) in (None, ""):
            return Auth.ntlm
        value = str(args[Field.auth])
        if value not in Auth.values:
            raise ValueError(Msg.bad_auth)
        return value

    def route(self, name: str, args: dict) -> dict | list:
        if name == Tool.register:
            return self.manager.reg(
                str(args[Field.ip]),
                int(args[Field.port]),
                str(args[Field.username]),
                str(args[Field.password]),
                self.proto(args, Protocol.ssh.value),
                Flags.on(args.get(Field.ssl), False),
                self.auth(args),
            )
        if name == Tool.winrm_register:
            port = int(args.get(Field.port, WinrmDefault.port))
            return self.manager.reg(
                str(args[Field.ip]),
                port,
                str(args[Field.username]),
                str(args[Field.password]),
                Protocol.winrm.value,
                Flags.on(args.get(Field.ssl), port == WinrmDefault.https),
                self.auth(args),
            )
        if name == Tool.update:
            protocol = self.proto(args, "") if Field.protocol in args else None
            if protocol == "":
                protocol = None
            return self.manager.upd(
                str(args[Field.old_ip]),
                str(args[Field.ip]),
                int(args[Field.port]),
                str(args[Field.username]),
                str(args[Field.password]),
                protocol,
                Flags.on(args.get(Field.ssl), False) if Field.ssl in args else None,
                self.auth(args) if Field.auth in args else None,
            )
        if name == Tool.delete:
            return self.manager.delete(str(args[Field.ip]))
        if name in (Tool.connect, Tool.winrm_connect):
            return self.manager.conn(str(args[Field.ip]))
        if name in (Tool.once, Tool.winrm_once):
            mode = ExecMode(str(args.get(Field.mode, ExecMode.host.value)))
            opts = dict(args)
            if name == Tool.winrm_once:
                opts[Field.protocol] = Protocol.winrm.value
            return self.manager.once(
                str(args[Field.ip]),
                int(args.get(Field.port, WinrmDefault.port if name == Tool.winrm_once else Default.port)),
                str(args[Field.username]),
                str(args[Field.password]),
                str(args[Field.command]),
                mode,
                opts,
            )
        if name in (Tool.sessions, Tool.winrm_sessions):
            return {Field.sessions: self.manager.list(str(args[Field.ip]))}
        if name in (Tool.close, Tool.winrm_close):
            return {Field.sessions: self.manager.close(str(args[Field.ip]), args.get(Field.session_id))}
        if name in (Tool.send, Tool.winrm_send):
            mode = ExecMode(str(args.get(Field.mode, ExecMode.host.value)))
            inp = args.get(Field.input)
            if inp is None:
                raise KeyError(Msg.missing)
            return self.manager.send(str(args[Field.session_id]), str(inp), mode, args)
        if name == Tool.send_many:
            return BatchExec.run(self.manager, args[Field.items])
        if name == Tool.output or name == Tool.winrm_output:
            return self.manager.output(
                str(args[Field.session_id]),
                args.get(Field.command_id),
                int(args.get(Field.cursor, 0)),
                int(args.get(Field.limit, Default.read)),
            )
        if name == Tool.status:
            return self.manager.status(str(args[Field.session_id]), args.get(Field.command_id))
        if name in (Tool.upload, Tool.winrm_upload):
            return self.manager.transfer(
                str(args[Field.ip]),
                str(args[Field.local_path]),
                str(args[Field.remote_path]),
                True,
                args.get(Field.session_id),
                args,
            )
        if name in (Tool.download, Tool.winrm_download):
            return self.manager.transfer(
                str(args[Field.ip]),
                str(args[Field.local_path]),
                str(args[Field.remote_path]),
                False,
                args.get(Field.session_id),
                args,
            )
        if name == Tool.servers:
            return {Field.servers: self.manager.store.list()}
        raise KeyError(Msg.tool_not_found)
