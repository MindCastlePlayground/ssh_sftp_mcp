import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from ssh_sftp_mcp.constants import CommandStatus, ExecMode, Field, JsonKey, Limits, Msg, Origin, RpcError, RpcMethod, RpcValue, ServiceCmd, ServiceFile, ServiceName, SessionStatus, Text, Tool, TransferMethod
from ssh_sftp_mcp.models import CommandInfo, ServerInfo
from ssh_sftp_mcp.rpc import McpServer, StdioApp, StdioClosed
from ssh_sftp_mcp.service_client import ServiceConfig, ServiceWire, ToolClient
from ssh_sftp_mcp.service_host import ServiceHost, ServiceSpec
from ssh_sftp_mcp.session import SessionManager, SshSession
from ssh_sftp_mcp.store import ServerStore
from ssh_sftp_mcp.tools import ToolCatalog, ToolFacade, ToolService


class SmokeTest(unittest.TestCase):
    def test_catalog_has_required_tools(self):
        names = {item[JsonKey.name] for item in ToolCatalog.list()}
        self.assertIn(Tool.register, names)
        self.assertIn(Tool.update, names)
        self.assertIn(Tool.delete, names)
        self.assertIn(Tool.connect, names)
        self.assertIn(Tool.once, names)
        self.assertIn(Tool.sessions, names)
        self.assertIn(Tool.close, names)
        self.assertIn(Tool.send, names)
        self.assertIn(Tool.send_many, names)
        self.assertIn(Tool.output, names)
        self.assertIn(Tool.upload, names)
        self.assertIn(Tool.download, names)
        self.assertIn(Tool.version, names)
        self.assertIn(Tool.service_start, names)

    def test_catalog_guides_interactive_session_ids(self):
        items = {item[JsonKey.name]: item for item in ToolCatalog.list()}
        for name in (Tool.connect, Tool.send, Tool.output):
            self.assertIn(Field.session_id, items[name][JsonKey.description])
        self.assertIn("not recommended", items[Tool.once][JsonKey.description])

    def test_old_tool_names_are_not_compatible(self):
        service = ToolService(object())
        for name in ("ssh_persistent_session", "ssh_temp_connect", "ssh_exec", "ssh_exec_many", "ssh_read"):
            res = service.call(name, {})
            self.assertTrue(res[JsonKey.is_error])
            self.assertIn(Msg.tool_not_found, res[JsonKey.structured][Field.reason])

    def test_register_duplicate_ip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ServerStore(Path(tmp))
            store.reg("127.0.0.1", 22, "u", "p")
            with self.assertRaises(ValueError):
                store.reg("127.0.0.1", 22, "u", "p")

    def test_update_and_delete_registered_ip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ServerStore(Path(tmp))
            store.reg("127.0.0.1", 22, "u", "p")
            data = store.upd("127.0.0.1", "127.0.0.2", 2222, "u2", "p2")
            self.assertEqual(data[Field.ip], "127.0.0.2")
            self.assertEqual(store.get("127.0.0.2").port, 2222)
            with self.assertRaises(KeyError):
                store.get("127.0.0.1")
            deleted = store.delete("127.0.0.2")
            self.assertEqual(deleted[Field.ip], "127.0.0.2")
            self.assertEqual(store.list(), [])

    def test_store_syncs_external_json_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ServerStore(Path(tmp))
            store.reg("127.0.0.1", 22, "u", "p")
            time.sleep(0.01)
            store.path.write_text(json.dumps({
                Field.servers: {
                    "10.0.0.1": {
                        Field.ip: "10.0.0.1",
                        Field.port: 2200,
                        Field.username: "root",
                        Field.password: "pw",
                    }
                }
            }), encoding=Text.encoding)
            self.assertEqual(store.get("10.0.0.1").port, 2200)
            self.assertFalse(store.has("127.0.0.1"))

    def test_bad_external_json_keeps_last_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ServerStore(Path(tmp))
            store.reg("127.0.0.1", 22, "u", "p")
            time.sleep(0.01)
            store.path.write_text("{", encoding=Text.encoding)
            self.assertEqual(store.get("127.0.0.1").username, "u")

    def test_json_rpc_tools_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = ToolService(SessionManager(ServerStore(Path(tmp))))
            server = McpServer(service)
            res = server.handle({
                JsonKey.jsonrpc: RpcValue.version,
                JsonKey.id: 1,
                JsonKey.method: RpcMethod.tools_list,
            })
            self.assertIn(JsonKey.result, res)
            self.assertIn(JsonKey.tools, res[JsonKey.result])

    def test_json_rpc_initialize_with_bom(self):
        server = McpServer()
        req = json.loads((Text.bom + json.dumps({
            JsonKey.jsonrpc: RpcValue.version,
            JsonKey.id: 1,
            JsonKey.method: RpcMethod.initialize,
            JsonKey.params: {JsonKey.protocol_version: "2025-06-18"},
        })).lstrip(Text.bom))
        res = server.handle(req)
        self.assertEqual(res[JsonKey.result][JsonKey.protocol_version], "2025-06-18")

    def test_tool_result_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = ToolService(SessionManager(ServerStore(Path(tmp))))
            res = service.call(Tool.register, {
                Field.ip: "127.0.0.1",
                Field.port: 22,
                Field.username: "u",
                Field.password: "p",
            })
            self.assertFalse(res[JsonKey.is_error])
            data = json.loads(res[JsonKey.content][0][JsonKey.text])
            self.assertEqual(data[Field.ip], "127.0.0.1")

    def test_service_host_reloads_changed_file_same_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "svc.py"
            path.write_text("class Api:\n    def value(self):\n        return 'v1'\n", encoding=Text.encoding)
            host = ServiceHost(ServiceSpec("tmp", path, "Api"))
            self.assertEqual(host.api().value(), "v1")
            time.sleep(0.01)
            path.write_text("class Api:\n    def value(self):\n        return 'v2'\n", encoding=Text.encoding)
            self.assertEqual(host.api().value(), "v2")

    def test_service_version_tool_reports_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = ToolService(SessionManager(ServerStore(Path(tmp))))
            res = service.call(Tool.version, {})
            self.assertFalse(res[JsonKey.is_error])
            data = res[JsonKey.structured]
            self.assertEqual(data[Field.service], ServiceName.tool)
            self.assertTrue(data[Field.path].endswith(ServiceFile.tool))
            self.assertIn(str(data[Field.mtime_ns]), data[Field.version])
            self.assertGreater(data[Field.size], 0)
            self.assertIsNone(data[Field.loaded_version])

    def test_service_version_tracks_loaded_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "svc.py"
            path.write_text("class Api:\n    def value(self):\n        return 'v1'\n", encoding=Text.encoding)
            host = ServiceHost(ServiceSpec("tmp", path, "Api"))
            first = host.info()
            self.assertIsNone(first[Field.loaded_version])
            host.api().value()
            second = host.info()
            self.assertEqual(second[Field.loaded_version], second[Field.version])
            time.sleep(0.01)
            path.write_text("class Api:\n    def value(self):\n        return 'v2'\n", encoding=Text.encoding)
            third = host.info()
            self.assertNotEqual(third[Field.loaded_version], third[Field.version])

    def test_tool_client_calls_ready_before_service_request(self):
        class Proc:
            def __init__(self):
                self.ready_count = 0

            def ready(self):
                self.ready_count += 1

        class Client(ToolClient):
            def __init__(self, proc):
                super().__init__(ServiceConfig(start=False), proc)
                self.req = {}

            def send(self, req):
                self.req = req
                return {
                    Field.ok: True,
                    JsonKey.result: {
                        JsonKey.content: [],
                        JsonKey.structured: {Field.ok: True},
                        JsonKey.is_error: False,
                    },
                }

        proc = Proc()
        client = Client(proc)
        res = client.call(Tool.servers, {})
        self.assertEqual(proc.ready_count, 1)
        self.assertEqual(client.req[Field.command], ServiceCmd.call)
        self.assertEqual(client.req[JsonKey.name], Tool.servers)
        self.assertFalse(res[JsonKey.is_error])

    def test_service_wire_waits_for_long_tool_response(self):
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def serve():
            try:
                conn, _ = server.accept()
                with conn:
                    conn.recv(4096)
                    time.sleep(1.2)
                    conn.sendall(json.dumps({
                        Field.ok: True,
                        JsonKey.result: {Field.status: Msg.ok},
                    }).encode(Text.encoding) + b"\n")
            finally:
                server.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        res = ServiceWire.ask(ServiceConfig(port=port, start=False), {Field.command: ServiceCmd.ping})
        thread.join(timeout=2)
        self.assertTrue(res[Field.ok])
        self.assertEqual(res[JsonKey.result][Field.status], Msg.ok)

    def test_facade_service_start_is_local_tool(self):
        class Client:
            def __init__(self):
                self.called = False

            def start(self):
                return {Field.status: Msg.ok, Field.host: "127.0.0.1", Field.port: 9}

            def call(self, name, args):
                self.called = True
                raise AssertionError("service_start should not route to backend call")

        client = Client()
        res = ToolFacade(client).call(Tool.service_start, {})
        self.assertFalse(res[JsonKey.is_error])
        self.assertEqual(res[JsonKey.structured][Field.status], Msg.ok)
        self.assertFalse(client.called)

    def test_facade_service_failure_stays_tool_error(self):
        class Client:
            def call(self, name, args):
                raise ConnectionError("service exited")

        service = ToolFacade(Client())
        server = McpServer(service)
        res = server.handle({
            JsonKey.jsonrpc: RpcValue.version,
            JsonKey.id: 7,
            JsonKey.method: RpcMethod.tools_call,
            JsonKey.params: {JsonKey.name: Tool.servers, JsonKey.arguments: {}},
        })
        result = res[JsonKey.result]
        self.assertTrue(result[JsonKey.is_error])
        self.assertIn("service exited", result[JsonKey.structured][Field.reason])
        ping = server.handle({JsonKey.jsonrpc: RpcValue.version, JsonKey.id: 8, JsonKey.method: RpcMethod.ping})
        self.assertEqual(ping[JsonKey.result], {})

    def test_exec_many_runs_items_concurrently(self):
        class Manager:
            def __init__(self):
                self.lock = threading.Lock()
                self.active = 0
                self.max_active = 0

            def send(self, session_id, command, mode, opts):
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.05)
                with self.lock:
                    self.active -= 1
                return {
                    Field.session_id: session_id,
                    Field.command_id: f"cmd-{session_id}",
                    Field.input: command,
                    Field.status: CommandStatus.queued.value,
                }

        manager = Manager()
        service = ToolService(manager)
        res = service.call(Tool.send_many, {
            Field.items: [
                {Field.session_id: "s1", Field.input: "pwd"},
                {Field.session_id: "s2", Field.input: "whoami"},
            ]
        })
        self.assertFalse(res[JsonKey.is_error])
        data = res[JsonKey.structured][Field.items]
        self.assertEqual([item[Field.index] for item in data], [0, 1])
        self.assertEqual([item[Field.session_id] for item in data], ["s1", "s2"])
        self.assertTrue(all(item[Field.ok] for item in data))
        self.assertGreater(manager.max_active, 1)

    def test_exec_many_keeps_per_item_errors(self):
        class Manager:
            def send(self, session_id, command, mode, opts):
                if session_id == "bad":
                    raise RuntimeError("no session")
                return {
                    Field.session_id: session_id,
                    Field.command_id: f"cmd-{session_id}",
                    Field.input: command,
                    Field.status: CommandStatus.queued.value,
                }

        service = ToolService(Manager())
        res = service.call(Tool.send_many, {
            Field.items: [
                {Field.session_id: "ok", Field.input: "pwd"},
                {Field.session_id: "bad", Field.input: "pwd"},
                "bad item",
            ]
        })
        self.assertFalse(res[JsonKey.is_error])
        data = res[JsonKey.structured][Field.items]
        self.assertEqual([item[Field.index] for item in data], [0, 1, 2])
        self.assertTrue(data[0][Field.ok])
        self.assertFalse(data[1][Field.ok])
        self.assertFalse(data[2][Field.ok])
        self.assertEqual(data[2][Field.status], Msg.bad_batch)

    def test_temp_exec_routes_to_blocking_once(self):
        class Manager:
            def once(self, ip, port, username, password, command, mode, opts):
                return {
                    Field.ip: ip,
                    Field.port: port,
                    Field.username: username,
                    Field.command: command,
                    Field.mode: mode.value,
                    Field.output: "ok\n",
                    Field.stderr: "",
                    Field.exit_code: 0,
                    Field.ok: True,
                }

        service = ToolService(Manager())
        res = service.call(Tool.once, {
            Field.ip: "127.0.0.1",
            Field.port: 22,
            Field.username: "u",
            Field.password: "p",
            Field.command: "echo ok",
        })
        self.assertFalse(res[JsonKey.is_error])
        self.assertEqual(res[JsonKey.structured][Field.output], "ok\n")
        self.assertTrue(res[JsonKey.structured][Field.ok])

    def test_json_rpc_recovers_from_handler_exception(self):
        class BadServer:
            def handle(self, req):
                raise RuntimeError("boom")

        app = StdioApp(BadServer())
        captured = []
        app.write = captured.append
        app.once(json.dumps({JsonKey.jsonrpc: RpcValue.version, JsonKey.id: 9, JsonKey.method: RpcMethod.ping}))
        self.assertEqual(captured[0][JsonKey.error][JsonKey.code], RpcError.internal)

    def test_command_prompt_split_across_chunks(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        cmd = CommandInfo(command_id="abc", command="echo ok")
        cmd.status = CommandStatus.running
        session.commands[cmd.command_id] = cmd
        session.scan("ok\r\nroot@host:~")
        self.assertEqual(cmd.status, CommandStatus.running)
        session.scan("# ")
        self.assertEqual(cmd.status, CommandStatus.done)
        self.assertIsNone(cmd.exit_code)

    def test_command_prompt_without_exit_code_finishes(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        cmd = CommandInfo(command_id="abc", command="echo ok")
        cmd.status = CommandStatus.running
        session.commands[cmd.command_id] = cmd
        session.scan("root@host:~$ ")
        self.assertEqual(cmd.status, CommandStatus.done)
        self.assertIsNone(cmd.exit_code)

    def test_command_read_cleans_echo_and_prompt(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        cmd = CommandInfo(command_id="abc", command="echo ok")
        session.commands[cmd.command_id] = cmd
        text = "echo ok\r\n\x1b[?2004lok\r\nroot@host:~# "
        self.assertEqual(session.clean(text, cmd), "ok")

    def test_command_read_keeps_output_before_known_prompt(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        session.prompt = "test-shell$ "
        cmd = CommandInfo(command_id="abc", command='printf "$A"')
        session.commands[cmd.command_id] = cmd
        text = "value-1test-shell$ "
        self.assertEqual(session.clean(text, cmd), "value-1")

    def test_host_mode_sends_command_without_wrapper(self):
        class Net:
            def __init__(self):
                self.parts = []

            def alive(self):
                return True

            def write(self, text):
                self.parts.append(text)

        net = Net()
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        session.status = SessionStatus.connected
        session.net = net
        data = session.send("export A=B", ExecMode.host, {})
        payload = "".join(net.parts)
        self.assertEqual(payload, "export A=B\n")
        self.assertEqual(data[Field.status], CommandStatus.running.value)

    def test_netmiko_write_sends_full_payload(self):
        class Net:
            def __init__(self):
                self.text = ""

            def write(self, text):
                self.text += text

        net = Net()
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        payload = "select '" + ("x" * (Limits.send + 23)) + "';"
        session.write(net, payload)
        self.assertEqual(net.text, payload)

    def test_output_limit_advances_inside_one_chunk(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        session.add("abcdef")
        first = session.output(None, 0, 2)
        second = session.output(None, first[Field.next_cursor], 2)
        third = session.output(None, second[Field.next_cursor], 10)
        self.assertEqual(first[Field.output], "ab")
        self.assertEqual(second[Field.output], "cd")
        self.assertEqual(third[Field.output], "ef")

    def test_raw_mode_preserves_psql_meta_command(self):
        class Net:
            def __init__(self):
                self.parts = []

            def alive(self):
                return True

            def write(self, text):
                self.parts.append(text)

        net = Net()
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        session.status = SessionStatus.connected
        session.net = net
        data = session.send("\\c app_db", ExecMode.raw, {})
        payload = "".join(net.parts)
        self.assertEqual(payload, "\\c app_db\n")
        self.assertEqual(data[Field.status], CommandStatus.done.value)
        self.assertEqual(data[Field.reason], Msg.raw_sent)

    def test_container_modes_validate_required_target(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        with self.assertRaises(ValueError):
            session.target("pwd", ExecMode.docker, {})
        with self.assertRaises(ValueError):
            session.target("pwd", ExecMode.k8s, {})

    def test_transfer_routes_to_reloadable_async_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "a.txt"
            local.write_bytes(b"ok")
            manager = SessionManager(ServerStore(Path(tmp) / "state"))
            info = ServerInfo("127.0.0.1", 22, "u", "p")
            manager.store.reg(info.ip, info.port, info.username, info.password)
            service = Mock()
            service.run.return_value = {Field.bytes: 2, Field.method: TransferMethod.sftp}
            manager.transfers.api = Mock(return_value=service)
            opts = {"workers": 4}
            data = manager.transfer(info.ip, str(local), "/tmp/a.txt", True, None, opts)
            service.run.assert_called_once_with(str(local), "/tmp/a.txt", True)
            self.assertEqual(manager.transfers.api.call_args.args, (info, opts))
            self.assertIsNone(data[Field.session_id])
            self.assertEqual(data[Field.bytes], 2)

    def test_upload_resolves_relative_source_from_package_dir(self):
        old = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                session = Mock(id="sid", info=ServerInfo("127.0.0.1", 22, "u", "p"))
                manager = SessionManager(ServerStore(Path(tmp) / "state"))
                service = Mock()
                service.run.return_value = {}
                manager.transfers.api = Mock(return_value=service)
                data = manager.transfer_session(session, "README.md", "/tmp/README.md", True)
            finally:
                os.chdir(old)
        expected = Path(__file__).resolve().parents[1] / "README.md"
        self.assertEqual(Path(service.run.call_args.args[0]), expected)
        self.assertEqual(Path(data[Field.local_path]), expected)

    def test_upload_missing_source_fails_before_sftp(self):
        class Log:
            def info(self, *args):
                return None

            def exception(self, *args):
                return None

        class Session:
            id = "sid"
            info = ServerInfo("127.0.0.1", 22, "u", "p")
            log = Log()

            def sftp(self):
                raise AssertionError("sftp should not open for missing local file")

            def probe(self):
                return True

        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(ServerStore(Path(tmp) / "state"))
            with self.assertRaisesRegex(FileNotFoundError, Msg.local_missing):
                manager.transfer_session(Session(), "missing.txt", "/tmp/missing.txt", True)

    def test_stdio_write_closed_raises_marker_exception(self):
        class ClosedStdout:
            def write(self, text):
                raise BrokenPipeError("pipe closed")

            def flush(self):
                return None

        old = sys.stdout
        try:
            sys.stdout = ClosedStdout()
            app = StdioApp(object())
            with self.assertRaises(StdioClosed):
                app.write({JsonKey.jsonrpc: RpcValue.version, JsonKey.id: 1, JsonKey.result: {}})
        finally:
            sys.stdout = old

    def test_shell_upload_streams_file_chunks(self):
        class WriteChannel:
            def __init__(self):
                self.timeout = "unset"

            def settimeout(self, timeout):
                self.timeout = timeout

            def shutdown_write(self):
                return None

        class Stdin:
            def __init__(self):
                self.channel = WriteChannel()
                self.writes = []

            def write(self, data):
                self.writes.append(len(data))

        class ExitChannel:
            def __init__(self):
                self.timeout = "unset"

            def settimeout(self, timeout):
                self.timeout = timeout

            def recv_exit_status(self):
                return 0

        class Stdout:
            def __init__(self):
                self.channel = ExitChannel()

            def read(self):
                return b""

        class Stderr:
            def read(self):
                return b""

        class Net:
            def __init__(self):
                self.stdin = Stdin()
                self.timeout = "unset"

            def alive(self):
                return True

            def exec(self, command):
                self.command = command
                self.timeout = Limits.transfer_timeout
                return self.stdin, Stdout(), Stderr()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.bin"
            path.write_bytes(b"x" * (Limits.file_chunk + 3))
            net = Net()
            session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
            session.status = SessionStatus.connected
            session.net = net
            size = session.shell_upload(str(path), "/tmp/large.bin")
        self.assertEqual(size, Limits.file_chunk + 3)
        self.assertEqual(net.stdin.writes, [Limits.file_chunk, 3])
        self.assertIsNone(net.timeout)
        self.assertIsNone(net.stdin.channel.timeout)

    def test_sftp_download_uses_bounded_prefetch(self):
        class Sftp:
            def __init__(self):
                self.prefetch = None
                self.max_prefetch = None

            def get(self, remote_path, local_path, prefetch=True, max_concurrent_prefetch_requests=None):
                self.remote_path = remote_path
                self.local_path = local_path
                self.prefetch = prefetch
                self.max_prefetch = max_concurrent_prefetch_requests
                Path(local_path).write_bytes(b"ok")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.bin"
            sftp = Sftp()
            session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
            size = session.sftp_download(sftp, "/tmp/large.bin", str(path))
            saved_size = path.stat().st_size
        self.assertEqual(size, 2)
        self.assertEqual(saved_size, 2)
        self.assertEqual(sftp.remote_path, "/tmp/large.bin")
        self.assertTrue(sftp.prefetch)
        self.assertEqual(sftp.max_prefetch, Limits.sftp_prefetch)

    def test_shell_download_streams_file_chunks(self):
        class Stdin:
            def __init__(self):
                self.closed = False
                self.channel = ExitChannel()

            def close(self):
                self.closed = True

        class ExitChannel:
            def __init__(self):
                self.timeout = "unset"

            def settimeout(self, timeout):
                self.timeout = timeout

            def recv_exit_status(self):
                return 0

        class Stdout:
            def __init__(self, chunks):
                self.chunks = chunks
                self.reads = []
                self.channel = ExitChannel()

            def read(self, size=-1):
                self.reads.append(size)
                if self.chunks:
                    return self.chunks.pop(0)
                return b""

        class Stderr:
            def read(self):
                return b""

        class Net:
            def __init__(self):
                self.stdin = Stdin()
                self.stdout = Stdout([b"x" * Limits.file_chunk, b"y" * 3])
                self.timeout = "unset"

            def alive(self):
                return True

            def exec(self, command):
                self.command = command
                self.timeout = Limits.transfer_timeout
                return self.stdin, self.stdout, Stderr()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.bin"
            net = Net()
            session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
            session.status = SessionStatus.connected
            session.net = net
            size = session.shell_download("/tmp/large.bin", str(path))
            saved_size = path.stat().st_size
        self.assertEqual(size, Limits.file_chunk + 3)
        self.assertEqual(saved_size, Limits.file_chunk + 3)
        self.assertEqual(net.stdout.reads, [Limits.file_chunk, Limits.file_chunk, Limits.file_chunk])
        self.assertIsNone(net.timeout)
        self.assertIsNone(net.stdin.channel.timeout)
        self.assertIsNone(net.stdout.channel.timeout)

    def test_transfer_failure_is_not_retried_as_unverified_shell_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "a.txt"
            local.write_bytes(b"ok")
            session = Mock(id="sid", info=ServerInfo("127.0.0.1", 22, "u", "p"))
            manager = SessionManager(ServerStore(Path(tmp) / "state"))
            service = Mock()
            service.run.side_effect = RuntimeError("transfer integrity verification failed")
            manager.transfers.api = Mock(return_value=service)
            with self.assertRaisesRegex(RuntimeError, "integrity"):
                manager.transfer_session(session, str(local), "/tmp/a.txt", True)
            session.shell_upload.assert_not_called()

    def test_transfer_rejects_session_from_different_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(ServerStore(Path(tmp)))
            manager.sessions["sid"] = Mock(info=ServerInfo("other", 22, "u", "p"))
            with self.assertRaisesRegex(ValueError, Msg.session_mismatch):
                manager.transfer("requested", "anything", "/tmp/file", True, "sid")
