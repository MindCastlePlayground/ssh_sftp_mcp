import asyncio
import hashlib
import os
import shlex
import subprocess
import sys
import re
import shlex
import tempfile
import unittest
from pathlib import Path

import asyncssh

from ssh_sftp_mcp.models import ServerInfo
from ssh_sftp_mcp.transfer import AsyncTransfer


class TransferKey:
    workers = "workers"
    block_size = "block_size"
    verify = "verify"
    readback = "readback"


class FaultSFTP(asyncssh.SFTPServer):
    def __init__(self, chan, root, state):
        super().__init__(chan, chroot=os.fsencode(root))
        self.state = state

    async def read(self, file_obj, offset, length):
        if self.state.get("eof") and offset >= self.state["eof"]:
            return b""
        self._enter()
        try:
            if self.state.get("delay"):
                await asyncio.sleep((offset // 8192 % 3) * 0.002)
            data = self._read(file_obj, offset, length)
        finally:
            self._leave()
        if self.state.get("short"):
            data = data[: min(len(data), 8191)]
        return data

    async def write(self, file_obj, offset, data):
        if self.state.get("fail") and offset == self.state["fail"]:
            raise asyncssh.SFTPFailure("injected write failure")
        self._enter()
        try:
            if self.state.get("delay"):
                await asyncio.sleep((offset // 8192 % 3) * 0.002)
            if self.state.get("corrupt") and offset == self.state["corrupt"]:
                data = bytes([data[0] ^ 0xFF]) + data[1:]
            return self._write(file_obj, offset, data)
        finally:
            self._leave()

    def _enter(self):
        self.state["inflight"] = self.state.get("inflight", 0) + 1
        self.state["max_inflight"] = max(self.state.get("max_inflight", 0), self.state["inflight"])

    def _leave(self):
        self.state["inflight"] -= 1

    @staticmethod
    def _read(file_obj, offset, length):
        file_obj.seek(offset)
        return file_obj.read(length)

    @staticmethod
    def _write(file_obj, offset, data):
        file_obj.seek(offset)
        return file_obj.write(data)


class NoExec(asyncssh.SSHServer):
    def begin_auth(self, username):
        return False


class SFTPFixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ssh-sftp-transfer-")
        self.root = Path(self.tmp.name)
        self.state = {}
        self.key = asyncssh.generate_private_key("ssh-rsa")
        self.listener = None

    async def start(self):
        self.listener = await asyncssh.listen(
            "127.0.0.1", 0,
            server_factory=NoExec,
            server_host_keys=[self.key],
            sftp_factory=lambda chan: FaultSFTP(chan, self.root, self.state),
        )
        self.info = ServerInfo("127.0.0.1", self.listener.get_port(), "test", "unused")

    async def stop(self):
        if self.listener:
            self.listener.close()
            await self.listener.wait_closed()
        self.tmp.cleanup()


class ShellResult:
    def __init__(self, status=0, stdout=b""):
        self.exit_status = status
        self.stdout = stdout


class ShellProcess:
    def __init__(self, conn, command):
        self.conn, self.command = conn, command
        self.stdin, self.stdout = self, self
        self.buf = bytearray()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def write(self, data):
        self.buf.extend(data)

    async def drain(self):
        return None

    def write_eof(self):
        if "cat > " in self.command:
            value = self.command.split("cat > ", 1)[1].split(")", 1)[0].strip()
            path = shlex.split(value)[0]
            self.conn.files[path] = bytes(self.buf)

    async def read(self, size=-1):
        data = self.buf[:size] if size >= 0 else self.buf[:]
        del self.buf[:len(data)]
        return bytes(data)

    async def wait(self):
        return ShellResult()


class ShellConn:
    def __init__(self, files):
        self.files = files

    def create_process(self, command, **kwargs):
        process = ShellProcess(self, command)
        if command.startswith("cat "):
            path = shlex.split(command[4:])[0]
            process.buf.extend(self.files[path])
        return process

    async def run(self, command, check=False, **kwargs):
        if command.startswith("wc -c < "):
            path = shlex.split(command[7:])[0]
            return ShellResult(stdout=str(len(self.files[path])).encode())
        if command.startswith("test ! -d "):
            parts = shlex.split(command)
            temp, remote = parts[7], parts[8]
            self.files[remote] = self.files.pop(temp)
            return ShellResult()
        if command.startswith("rm -f "):
            self.files.pop(shlex.split(command[6:])[0], None)
            return ShellResult()
        raise AssertionError(f"unexpected shell command: {command}")


class TransferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = SFTPFixture()
        await self.fixture.start()
        self.local = Path(tempfile.mkdtemp(prefix="ssh-sftp-local-"))

    async def asyncTearDown(self):
        for item in self.local.glob("*"):
            item.unlink(missing_ok=True)
        self.local.rmdir()
        await self.fixture.stop()

    def path(self, name):
        return self.local / name

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    async def transfer(self, local, remote, upload, **opts):
        options = {TransferKey.workers: 8, TransferKey.block_size: 8192}
        options.update(opts)
        return await AsyncTransfer(self.fixture.info, options).execute(local, remote, upload)

    async def test_parallel_fake_handle_has_concurrent_nonoverlapping_ranges(self):
        source = self.path("parallel.bin")
        data = os.urandom(4096 * 13 + 19)
        source.write_bytes(data)
        transfer = AsyncTransfer(self.fixture.info, {"workers": 4, "block_size": 4096})

        class Handle:
            def __init__(self, size):
                self.out = bytearray(size)
                self.ranges = []
                self.active = 0
                self.max_active = 0

            async def write(self, chunk, offset):
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                await asyncio.sleep((offset // 4096 % 4) * 0.001)
                self.out[offset:offset + len(chunk)] = chunk
                self.ranges.append((offset, offset + len(chunk)))
                self.active -= 1
                return len(chunk)

        handle = Handle(len(data))
        await transfer._parallel(handle, str(source), len(data), True)
        self.assertGreater(handle.max_active, 1)
        self.assertEqual(bytes(handle.out), data)
        ranges = sorted(handle.ranges)
        self.assertEqual(ranges[0][0], 0)
        self.assertTrue(all(left[1] <= right[0] for left, right in zip(ranges, ranges[1:])))

    async def test_python_remote_hash_fallback_handles_quoted_path(self):
        source = self.path("hash space's.bin")
        source.write_bytes(os.urandom(65539))

        class Conn:
            async def run(self, command, **kwargs):
                if not command.startswith('python3 '):
                    return ShellResult(127, '')
                args = shlex.split(command)
                result = await asyncio.to_thread(subprocess.run, [sys.executable, *args[1:]], capture_output=True, text=True)
                return ShellResult(result.returncode, result.stdout)

        self.assertEqual(await AsyncTransfer._remote_hash(Conn(), str(source)), self.digest(source))

    async def test_remote_hash_parses_openssl_and_falls_back_when_tools_unavailable(self):
        expected = hashlib.sha256(b"hash").hexdigest()

        class Result:
            def __init__(self, status, output):
                self.exit_status = status
                self.stdout = output

        class Conn:
            def __init__(self, results):
                self.results = iter(results)
                self.commands = []

            async def run(self, command, **kwargs):
                self.commands.append(command)
                result = next(self.results)
                if isinstance(result, BaseException):
                    raise result
                return result

        conn = Conn([Result(1, ""), Result(1, ""), Result(0, f"SHA256 (x) = {expected}")])
        self.assertEqual(await AsyncTransfer._remote_hash(conn, "/x"), expected)
        self.assertIn("openssl dgst -sha256", conn.commands[2])
        unavailable = Conn([asyncssh.ChannelOpenError(1, "denied")] * 4)
        self.assertIsNone(await AsyncTransfer._remote_hash(unavailable, "/x"))

    async def test_shell_upload_and_download_binary_readback(self):
        source = self.path("shell-source.bin")
        source.write_bytes(os.urandom(8192 + 37))
        conn = ShellConn({})
        transfer = AsyncTransfer(self.fixture.info, {"verify": "readback", "block_size": 4096})
        result = await transfer._shell(conn, source, "/shell.bin", True)
        self.assertEqual(result["method"], "shell")
        target = self.path("shell-target.bin")
        await transfer._shell(conn, target, "/shell.bin", False)
        self.assertEqual(target.read_bytes(), source.read_bytes())

    async def test_shell_hash_mismatch_does_not_publish_or_replace_old_target(self):
        source = self.path("shell-source.bin")
        source.write_bytes(b"new bytes")
        conn = ShellConn({"/old.bin": b"old bytes"})
        transfer = AsyncTransfer(self.fixture.info, {"verify": "readback", "block_size": 4096})

        class BadShell(ShellConn):
            async def run(self, command, check=False, **kwargs):
                result = await super().run(command, check, **kwargs)
                if command.startswith("wc -c < "):
                    result.stdout = b"999"
                return result

        bad = BadShell(conn.files)
        with self.assertRaises(Exception):
            await transfer._shell(bad, source, "/old.bin", True)
        self.assertEqual(conn.files["/old.bin"], b"old bytes")
        self.assertFalse(any(".part-" in key for key in conn.files))

    async def test_upload_download_hashes_tail_and_empty(self):
        source = self.path("source.bin")
        source.write_bytes(os.urandom(8192 * 9 + 37))
        result = await self.transfer(source, "/roundtrip.bin", True, verify=TransferKey.readback)
        self.assertEqual(result["bytes"], source.stat().st_size)
        self.assertEqual(result["sha256"], self.digest(source))
        target = self.path("target.bin")
        result = await self.transfer(target, "/roundtrip.bin", False, verify=TransferKey.readback)
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertEqual(result["sha256"], self.digest(source))
        empty = self.path("empty.bin")
        empty.touch()
        await self.transfer(empty, "/empty.bin", True, verify=TransferKey.readback)
        downloaded = self.path("empty.out")
        await self.transfer(downloaded, "/empty.bin", False, verify=TransferKey.readback)
        self.assertEqual(downloaded.read_bytes(), b"")

    async def test_out_of_order_replies(self):
        source = self.path("source.bin")
        source.write_bytes(os.urandom(8192 * 11 + 3))
        self.fixture.state["delay"] = True
        await self.transfer(source, "/ordered.bin", True)
        self.fixture.state.clear()
        self.fixture.state["delay"] = True
        self.fixture.state["short"] = True
        target = self.path("target.bin")
        await self.transfer(target, "/ordered.bin", False)
        self.assertEqual(target.read_bytes(), source.read_bytes())

    async def test_forced_readback_when_remote_exec_is_denied(self):
        source = self.path("source.bin")
        source.write_bytes(b"readback" * 1000)
        result = await self.transfer(source, "/readback.bin", True, verify=TransferKey.readback)
        self.assertTrue(result["verified"])
        self.assertEqual(result["verification"], TransferKey.readback)

    async def test_corrupt_ack_fails_and_preserves_existing_destination(self):
        source = self.path("source.bin")
        source.write_bytes(os.urandom(8192 * 3 + 11))
        remote = self.fixture.root / "corrupt.bin"
        remote.write_bytes(b"old remote contents")
        self.fixture.state["corrupt"] = 8192
        with self.assertRaises(Exception):
            await self.transfer(source, "/corrupt.bin", True, verify=TransferKey.readback)
        self.assertEqual(remote.read_bytes(), b"old remote contents")
        self.assertFalse(any(p.name.startswith("corrupt.bin.part-") for p in self.fixture.root.iterdir()))

    async def test_failed_worker_does_not_publish(self):
        source = self.path("source.bin")
        source.write_bytes(os.urandom(8192 * 4))
        remote = self.fixture.root / "failed.bin"
        remote.write_bytes(b"keep this")
        self.fixture.state["fail"] = 8192
        with self.assertRaises(Exception):
            await self.transfer(source, "/failed.bin", True)
        self.assertEqual(remote.read_bytes(), b"keep this")
        self.assertFalse(any(p.name.startswith("failed.bin.part-") for p in self.fixture.root.iterdir()))

    async def test_premature_eof_fails_and_preserves_old_local(self):
        source = self.path("remote-source.bin")
        source.write_bytes(os.urandom(8192 * 3 + 99))
        await self.transfer(source, "/eof.bin", True)
        target = self.path("existing.bin")
        target.write_bytes(b"old local contents")
        self.fixture.state["eof"] = 8192
        with self.assertRaises(Exception):
            await self.transfer(target, "/eof.bin", False)
        self.assertEqual(target.read_bytes(), b"old local contents")
        self.assertFalse(any(p.name.startswith("existing.bin.part-") for p in self.local.iterdir()))


if __name__ == "__main__":
    unittest.main()
