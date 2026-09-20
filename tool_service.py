from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from ssh_sftp_mcp.constants import Default, ExecMode, Field, Limits, Msg, Tool


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
        if name == Tool.once:
            mode = ExecMode(str(args.get(Field.mode, ExecMode.host.value)))
            return self.manager.once(
                str(args[Field.ip]),
                int(args[Field.port]),
                str(args[Field.username]),
                str(args[Field.password]),
                str(args[Field.command]),
                mode,
                args,
            )
        if name == Tool.sessions:
            return {Field.sessions: self.manager.list(str(args[Field.ip]))}
        if name == Tool.close:
            return {Field.sessions: self.manager.close(str(args[Field.ip]), args.get(Field.session_id))}
        if name == Tool.send:
            mode = ExecMode(str(args.get(Field.mode, ExecMode.host.value)))
            inp = args.get(Field.input)
            if inp is None:
                raise KeyError(Msg.missing)
            return self.manager.send(str(args[Field.session_id]), str(inp), mode, args)
        if name == Tool.send_many:
            return BatchExec.run(self.manager, args[Field.items])
        if name == Tool.output:
            return self.manager.output(
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
                args,
            )
        if name == Tool.download:
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
