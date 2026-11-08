import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from ssh_sftp_mcp.constants import CommandStatus, ExecMode, Field, Origin, Protocol, Tool
from ssh_sftp_mcp.models import CommandInfo, ServerInfo
from ssh_sftp_mcp.session import SessionManager, SshSession
from ssh_sftp_mcp.store import ServerStore
from ssh_sftp_mcp.tool_service import ToolApi
from ssh_sftp_mcp.tools import ToolCatalog
from ssh_sftp_mcp.winrm import WinrmSession, WinrmTransfer


class MemLink:
    def __init__(self):
        self.scripts = []
        self.cwd = r"C:\work"
        self.files = {}
        self.opened = False
        self.closed = False
        self.dead = False

    def alive(self):
        return self.opened and not self.closed and not self.dead

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def run_ps(self, script):
        self.scripts.append(script)
        if script.strip().startswith("Set-Location "):
            self.cwd = script.strip().split(" ", 1)[1].strip("'")
            return "", "", 0
        if "GetUnresolvedProviderPathFromPSPath" in script:
            raw = script.split("GetUnresolvedProviderPathFromPSPath('", 1)[1].split("')", 1)[0].replace("''", "'")
            if len(raw) > 2 and raw[1] == ":":
                dest = raw
            else:
                dest = self.cwd.rstrip("\\") + "\\" + raw
            return dest, "", 0
        if "Get-FileHash" in script:
            path = script.split("LiteralPath '", 1)[1].split("'", 1)[0]
            return hashlib.sha256(self.files[path]).hexdigest(), "", 0
        if script.strip().startswith("echo "):
            return script.strip()[5:], "", 0
        return "ok", "", 0

    def run_cmd(self, command):
        self.scripts.append("cmd:" + command)
        return "cmd-ok", "", 0

    def copy(self, local_path, remote_path):
        self.files[remote_path] = Path(local_path).read_bytes()
        return remote_path

    def fetch(self, remote_path, local_path):
        Path(local_path).write_bytes(self.files[remote_path])


class OscTest(unittest.TestCase):
    def test_systemd_osc_sets_exit_code_and_finishes(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        cmd = CommandInfo(command_id="abc", command="false")
        cmd.status = CommandStatus.running
        session.commands[cmd.command_id] = cmd
        session.scan("\x1b]3008;end=abc;exit=failure;status=3\x1b\\")
        self.assertEqual(cmd.status, CommandStatus.done)
        self.assertEqual(cmd.exit_code, 3)

    def test_long_osc_line_still_matches_prompt(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        cmd = CommandInfo(command_id="abc", command="echo ok")
        cmd.status = CommandStatus.running
        session.commands[cmd.command_id] = cmd
        noise = "\x1b]3008;start=" + ("a" * 240) + ";type=shell;cwd=/home/mcpssh\x1b\\"
        session.scan("ok\n" + noise + "\x1b[?2004hmcpssh@messenger:~$ ")
        self.assertEqual(cmd.status, CommandStatus.done)
        self.assertEqual(session.clean(session.tail, cmd).strip(), "ok")

    def test_previous_osc_mark_does_not_finish_the_next_command(self):
        session = SshSession(ServerInfo("127.0.0.1", 22, "u", "p"), Origin.temporary)
        first = CommandInfo(command_id="a", command="true")
        first.status = CommandStatus.running
        session.commands[first.command_id] = first
        session.scan("\x1b]3008;end=abc;exit=success\x1b\\")
        self.assertEqual(first.exit_code, 0)
        second = CommandInfo(command_id="b", command="next")
        second.status = CommandStatus.running
        session.commands[second.command_id] = second
        session.scan("partial")
        self.assertEqual(second.status, CommandStatus.running)


class WinrmBehavior(unittest.TestCase):
    def test_catalog_lists_winrm_tools(self):
        names = {item["name"] for item in ToolCatalog.list()}
        for name in (Tool.winrm_register, Tool.winrm_connect, Tool.winrm_send, Tool.winrm_once, Tool.winrm_upload, Tool.winrm_download):
            self.assertIn(name, names)

    def test_register_roundtrip_keeps_winrm(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ServerStore(Path(tmp))
            store.reg("10.0.0.8", 5985, "Administrator", "secret", Protocol.winrm.value, False, "ntlm")
            again = ServerStore(Path(tmp))
            info = again.get("10.0.0.8")
            self.assertEqual(info.protocol, Protocol.winrm.value)
            self.assertEqual(info.port, 5985)
            self.assertNotIn("secret", str(info.public()))

    def test_update_keeps_protocol_when_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ServerStore(Path(tmp))
            store.reg("10.0.0.8", 5985, "Administrator", "secret", Protocol.winrm.value, True, "basic")
            store.upd("10.0.0.8", "10.0.0.8", 5986, "Administrator", "next")
            info = store.get("10.0.0.8")
            self.assertEqual(info.protocol, Protocol.winrm.value)
            self.assertTrue(info.ssl)
            self.assertEqual(info.auth, "basic")
            self.assertEqual(info.password, "next")

    def test_session_reuses_one_link_and_relative_upload(self):
        info = ServerInfo("10.0.0.8", 5985, "Administrator", "secret", Protocol.winrm.value)
        link = MemLink()
        session = WinrmSession(info, Origin.registered, link)
        session.conn()
        moved = session.send("Set-Location 'D:\\data'", ExecMode.host, {})
        self.assertEqual(moved[Field.exit_code], 0)
        self.assertEqual(link.cwd, r"D:\data")
        again = session.send("echo persist", ExecMode.host, {Field.shell: "powershell"})
        text = session.output(again[Field.command_id], 0, 1000)
        self.assertIn("persist", text[Field.output])
        self.assertEqual(text[Field.exit_code], 0)
        self.assertIs(session.link, link)
        self.assertEqual(link.closed, False)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "note.txt"
            blob = b"winrm-bytes"
            src.write_bytes(blob)
            up = WinrmTransfer(info, link).run(str(src), "note.txt", True)
            self.assertTrue(up["verified"])
            self.assertEqual(link.files[r"D:\data\note.txt"], blob)
            dst = Path(tmp) / "back.txt"
            down = WinrmTransfer(info, link).run(str(dst), "note.txt", False)
            self.assertEqual(dst.read_bytes(), blob)
            self.assertEqual(down["sha256"], hashlib.sha256(blob).hexdigest())
            self.assertEqual(down["method"], "winrm")
        session.close()
        self.assertTrue(link.closed)

    def test_one_shot_route_marks_winrm_protocol(self):
        seen = {}

        class Manager:
            def once(self, ip, port, username, password, command, mode, opts):
                seen.update(ip=ip, port=port, command=command, opts=opts)
                return {Field.ok: True, Field.output: "once"}

        data = ToolApi(Manager()).route(Tool.winrm_once, {
            Field.ip: "10.0.0.8",
            Field.username: "Administrator",
            Field.password: "secret",
            Field.command: "whoami",
        })
        self.assertEqual(data[Field.output], "once")
        self.assertEqual(seen["port"], 5985)
        self.assertEqual(seen["opts"][Field.protocol], Protocol.winrm.value)

    def test_cmd_shell_uses_run_cmd(self):
        info = ServerInfo("10.0.0.8", 5985, "Administrator", "secret", Protocol.winrm.value)
        link = MemLink()
        session = WinrmSession(info, Origin.temporary, link)
        session.conn()
        data = session.send("whoami", ExecMode.host, {Field.shell: "cmd"})
        self.assertEqual(data[Field.output], "cmd-ok")
        self.assertTrue(link.scripts[-1].startswith("cmd:"))


class LiveSsh(unittest.TestCase):
    def setUp(self):
        if os.environ.get("SSH_SFTP_MCP_LIVE") != "1":
            self.skipTest("set SSH_SFTP_MCP_LIVE=1 to run the local ssh acceptance test")
        self.home = tempfile.TemporaryDirectory()
        os.environ["SSH_SFTP_MCP_HOME"] = self.home.name
        self.mgr = SessionManager(ServerStore(Path(self.home.name)))
        self.ip = os.environ.get("SSH_SFTP_MCP_LIVE_HOST", "127.0.0.1")
        self.port = int(os.environ.get("SSH_SFTP_MCP_LIVE_PORT", "22"))
        self.user = os.environ["SSH_SFTP_MCP_LIVE_USER"]
        self.password = os.environ["SSH_SFTP_MCP_LIVE_PASSWORD"]
        self.mgr.reg(self.ip, self.port, self.user, self.password)

    def tearDown(self):
        if hasattr(self, "mgr"):
            try:
                self.mgr.close(self.ip)
            except Exception:
                pass
        if hasattr(self, "home"):
            self.home.cleanup()

    def wait(self, sid, cid, needle):
        import time
        last = ""
        for _ in range(40):
            out = self.mgr.output(sid, cid, 0, 65536)
            status = self.mgr.status(sid, cid)[Field.command][Field.status]
            last = out[Field.output]
            if status == CommandStatus.done.value and needle in last:
                return out
            time.sleep(0.25)
        self.fail(f"command did not finish: {last!r}")

    def test_reuse_once_upload_download(self):
        opened = self.mgr.conn(self.ip)
        sid = opened[Field.session_id]
        first = self.mgr.send(sid, "export MCP_MARK=persist; cd \"$HOME\"; echo MARK_READY", ExecMode.host, {})
        self.wait(sid, first[Field.command_id], "MARK_READY")
        second = self.mgr.send(sid, "printf 'MARK=%s CWD=%s\\n' \"$MCP_MARK\" \"$(pwd)\"", ExecMode.host, {})
        out = self.wait(sid, second[Field.command_id], "MARK=persist")
        self.assertIn(self.user, out[Field.output])
        once = self.mgr.once(self.ip, self.port, self.user, self.password, "echo ONCE_OK", ExecMode.host, {})
        self.assertTrue(once[Field.ok])
        self.assertIn("ONCE_OK", once[Field.output])
        blob = b"live-ssh-" + os.urandom(30000)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "up.bin"
            src.write_bytes(blob)
            remote = f"/home/{self.user}/ssh-mcp-live.bin"
            up = self.mgr.transfer(self.ip, str(src), remote, True, sid, {Field.session_id: sid})
            self.assertTrue(up["verified"])
            self.assertEqual(up["sha256"], hashlib.sha256(blob).hexdigest())
            self.assertEqual(up[Field.session_id], sid)
            dst = Path(tmp) / "down.bin"
            down = self.mgr.transfer(self.ip, str(dst), remote, False, sid, {})
            self.assertEqual(dst.read_bytes(), blob)
            self.assertTrue(down["verified"])
            self.mgr.send(sid, f"rm -f {remote}", ExecMode.host, {})
