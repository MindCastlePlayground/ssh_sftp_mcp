import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ssh_sftp_mcp.constants import CommandStatus, ExecMode, Field, JsonKey, Limits, Msg, Origin, RpcError, RpcMethod, RpcValue, ServiceFile, ServiceName, SessionStatus, Text, Tool, TransferMethod
from ssh_sftp_mcp.models import CommandInfo, ServerInfo
from ssh_sftp_mcp.rpc import McpServer, StdioApp, StdioClosed
from ssh_sftp_mcp.service_host import ServiceHost, ServiceSpec
from ssh_sftp_mcp.session import SessionManager, SshSession
from ssh_sftp_mcp.store import ServerStore
from ssh_sftp_mcp.tools import ToolCatalog, ToolService


class SmokeTest(unittest.TestCase):
    def test_catalog_has_required_tools(self):
        names = {item[JsonKey.name] for item in ToolCatalog.list()}
        self.assertIn(Tool.register, names)
        self.assertIn(Tool.update, names)
        self.assertIn(Tool.delete, names)
        self.assertIn(Tool.connect, names)
        self.assertIn(Tool.temp, names)
        self.assertIn(Tool.sessions, names)
        self.assertIn(Tool.close, names)
        self.assertIn(Tool.exec, names)
        self.assertIn(Tool.read, names)
        self.assertIn(Tool.upload, names)
        self.assertIn(Tool.download, names)
        self.assertIn(Tool.version, names)

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

    def test_json_rpc_recovers_from_handler_exception(self):
        class BadServer:
            def handle(self, req):
                raise RuntimeError("boom")

        app = StdioApp(BadServer())
        captured = []
        app.write = captured.append
        app.once(json.dumps({JsonKey.jsonrpc: RpcValue.version, JsonKey.id: 9, JsonKey.method: RpcMethod.ping}))
        self.assertEqual(captured[0][JsonKey.error][JsonKey.code], RpcError.internal)

    def test_command_marker_split_across_chunks(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        cmd = CommandInfo(command_id="abc", command="echo ok")
        session.commands[cmd.command_id] = cmd
        session.scan("__MCP_CMD_abc_STA")
        session.scan("RT__")
        self.assertEqual(cmd.status, CommandStatus.running)
        session.scan("__MCP_CMD_abc_END")
        session.scan("__:7")
        self.assertEqual(cmd.status, CommandStatus.done)
        self.assertEqual(cmd.exit_code, 7)

    def test_command_echo_without_exit_code_does_not_finish(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        cmd = CommandInfo(command_id="abc", command="echo ok")
        session.commands[cmd.command_id] = cmd
        session.scan("__MCP_CMD_abc_END__")
        self.assertNotEqual(cmd.status, CommandStatus.done)
        session.scan(":0")
        self.assertEqual(cmd.status, CommandStatus.done)
        self.assertEqual(cmd.exit_code, 0)

    def test_command_read_cleans_markers_and_prompt(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        cmd = CommandInfo(command_id="abc", command="echo ok")
        session.commands[cmd.command_id] = cmd
        text = "__MCP_CMD_abc_START__\r\n\x1b[?2004lok\r\n__MCP_CMD_abc_END__:0\r\nroot@host:~# "
        self.assertEqual(session.clean(text, cmd), "ok")

    def test_container_modes_validate_required_target(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        with self.assertRaises(ValueError):
            session.target("pwd", ExecMode.docker, {})
        with self.assertRaises(ValueError):
            session.target("pwd", ExecMode.k8s, {})

    def test_transfer_uses_shell_fallback_when_sftp_fails(self):
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
                raise PermissionError("denied")

            def probe(self):
                return True

            def shell_upload(self, local_path, remote_path):
                return Path(local_path).stat().st_size

        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "a.txt"
            local.write_text("ok", encoding=Text.encoding)
            manager = SessionManager(ServerStore(Path(tmp) / "state"))
            data = manager.transfer_session(Session(), str(local), "/tmp/a.txt", True)
            self.assertEqual(data[Field.method], TransferMethod.shell)
            self.assertEqual(data[Field.bytes], 2)

    def test_upload_resolves_relative_source_from_package_dir(self):
        class Log:
            def info(self, *args):
                return None

            def exception(self, *args):
                return None

        class Sftp:
            def __init__(self, session):
                self.session = session

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def put(self, local_path, remote_path):
                self.session.local_path = local_path

        class Session:
            id = "sid"
            info = ServerInfo("127.0.0.1", 22, "u", "p")
            log = Log()
            local_path = ""

            def sftp(self):
                return Sftp(self)

            def probe(self):
                return True

        old = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                session = Session()
                manager = SessionManager(ServerStore(Path(tmp) / "state"))
                data = manager.transfer_session(session, "README.md", "/tmp/README.md", True)
            finally:
                os.chdir(old)
        expected = Path(__file__).resolve().parents[1] / "README.md"
        self.assertEqual(Path(session.local_path), expected)
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
        class Transport:
            def is_active(self):
                return True

        class WriteChannel:
            def shutdown_write(self):
                return None

        class Stdin:
            def __init__(self):
                self.channel = WriteChannel()
                self.writes = []

            def write(self, data):
                self.writes.append(len(data))

        class ExitChannel:
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

        class Client:
            def __init__(self):
                self.stdin = Stdin()

            def get_transport(self):
                return Transport()

            def exec_command(self, command):
                return self.stdin, Stdout(), Stderr()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.bin"
            path.write_bytes(b"x" * (Limits.file_chunk + 3))
            client = Client()
            session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
            session.status = SessionStatus.connected
            session.client = client
            size = session.shell_upload(str(path), "/tmp/large.bin")
        self.assertEqual(size, Limits.file_chunk + 3)
        self.assertEqual(client.stdin.writes, [Limits.file_chunk, 3])

    def test_transfer_skips_shell_fallback_when_sftp_disconnects(self):
        class Log:
            def info(self, *args):
                return None

            def exception(self, *args):
                return None

            def warning(self, *args):
                return None

        class Session:
            id = "sid"
            info = ServerInfo("127.0.0.1", 22, "u", "p")
            log = Log()
            status = SessionStatus.connected
            reason = ""
            fallback = False

            def sftp(self):
                raise RuntimeError(Msg.disconnected)

            def probe(self):
                self.mark(SessionStatus.disconnected, Msg.disconnected)
                return False

            def mark(self, status, reason):
                self.status = status
                self.reason = reason

            def shell_upload(self, local_path, remote_path):
                self.fallback = True
                return 0

        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "a.txt"
            local.write_text("ok", encoding=Text.encoding)
            session = Session()
            manager = SessionManager(ServerStore(Path(tmp) / "state"))
            with self.assertRaisesRegex(RuntimeError, Msg.disconnected):
                manager.transfer_session(session, str(local), "/tmp/a.txt", True)
        self.assertFalse(session.fallback)
        self.assertEqual(session.status, SessionStatus.disconnected)
