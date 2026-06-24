from __future__ import annotations

from ssh_sftp_mcp.constants import Default, ExecMode, Field, Msg, Tool


class ToolApi:
    def __init__(self, manager):
        self.manager = manager

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
