import asyncio
import hashlib
import os
import posixpath
import re
import shlex
import time
import uuid
from pathlib import Path

import asyncssh

from ssh_sftp_mcp.models import ServerInfo
from ssh_sftp_mcp.transfer_constants import TransferError, TransferMode, TransferOption, TransferResult


class AsyncTransfer:
    def __init__(self, info: ServerInfo, opts=None):
        self.info = info
        opts = opts or {}
        self.workers = self._option(opts, TransferOption.workers, 32, 1, 128)
        self.block_size = self._option(opts, TransferOption.block_size, 131072, 4096, 131072)
        self.verify = opts.get(TransferOption.verify, TransferOption.auto)
        if self.verify not in (TransferOption.auto, TransferOption.readback):
            raise ValueError(TransferError.bad_option)
        self.transfer_seconds = 0.0
        self.verify_seconds = 0.0

    @staticmethod
    def _option(opts, key, default, low, high):
        value = opts.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f'{TransferError.bad_option}: {key} must be {low}..{high}')
        return value

    def run(self, local_path, remote_path, upload):
        return asyncio.run(self.execute(local_path, remote_path, upload))

    async def execute(self, local_path, remote_path, upload):
        started = time.perf_counter()
        local = Path(local_path)
        remote = str(remote_path)
        if remote.startswith('-'):
            remote = './' + remote
        if not upload:
            local.parent.mkdir(parents=True, exist_ok=True)
        async with asyncssh.connect(
            self.info.ip, port=self.info.port, username=self.info.username,
            password=self.info.password, known_hosts=None, config=None,
            agent_path=None, client_keys=[], login_timeout=15, connect_timeout=20,
            keepalive_interval=30,
        ) as conn:
            try:
                sftp = await conn.start_sftp_client()
            except (asyncssh.ChannelOpenError, asyncssh.SFTPNoConnection):
                result = await self._shell(conn, local, remote, upload)
            else:
                async with sftp:
                    result = await self._sftp(conn, sftp, local, remote, upload)
        result[TransferResult.total_seconds] = time.perf_counter() - started
        return result

    async def _sftp(self, conn, sftp, local, remote, upload):
        temp = remote + '.part-' + uuid.uuid4().hex if upload else str(local) + '.part-' + uuid.uuid4().hex
        created = False
        try:
            if upload:
                stamp = local.stat()
                size = stamp.st_size
                started = time.perf_counter()
                digest = await asyncio.to_thread(self._hash_local, local)
                self.verify_seconds += time.perf_counter() - started
                parent = posixpath.dirname(remote)
                if parent:
                    await sftp.makedirs(parent, exist_ok=True)
                async with sftp.open(temp, 'xb', encoding=None, max_requests=1) as handle:
                    created = True
                    await self._parallel(handle, local, size, True)
                    try:
                        await handle.fsync()
                    except asyncssh.SFTPOpUnsupported:
                        pass
                started = time.perf_counter()
                self._local_unchanged(local, stamp)
                if (await sftp.stat(temp)).size != size:
                    raise IOError(TransferError.verify)
                remote_hash, readback = await self._remote_digest(conn, sftp, temp, size)
                self._local_unchanged(local, stamp)
                if remote_hash != digest:
                    raise IOError(TransferError.verify)
                self.verify_seconds += time.perf_counter() - started
                await self._publish(sftp, temp, remote)
            else:
                attrs = await sftp.stat(remote)
                if attrs.size is None:
                    raise IOError(TransferError.short_read)
                size = attrs.size
                with open(temp, 'xb'):
                    created = True
                async with sftp.open(remote, 'rb', encoding=None, max_requests=1) as handle:
                    await self._parallel(handle, temp, size, False)
                started = time.perf_counter()
                if Path(temp).stat().st_size != size:
                    raise IOError(TransferError.verify)
                digest = await asyncio.to_thread(self._hash_local, temp)
                remote_hash, readback = await self._remote_digest(conn, sftp, remote, size)
                now = await sftp.stat(remote)
                if now.size != size or now.mtime != attrs.mtime:
                    raise IOError(TransferError.changed)
                if remote_hash != digest:
                    raise IOError(TransferError.verify)
                self.verify_seconds += time.perf_counter() - started
                os.replace(temp, local)
            created = False
            return self._result(size, TransferMode.sftp, digest, remote_hash, readback)
        finally:
            if created:
                if upload:
                    try:
                        await sftp.remove(temp)
                    except (asyncssh.SFTPError, asyncssh.Error):
                        pass  # A disconnected server may retain a uniquely named staging file.
                else:
                    Path(temp).unlink(missing_ok=True)

    async def _parallel(self, handle, path, size, upload):
        started = time.perf_counter()
        offset = 0

        async def worker():
            nonlocal offset
            with open(path, 'rb' if upload else 'r+b') as file:
                while offset < size:
                    # No await while claiming a range. File offsets are never shared across workers.
                    off = offset
                    length = min(self.block_size, size - off)
                    offset += length
                    if upload:
                        file.seek(off)
                        data = file.read(length)
                        if len(data) != length:
                            raise IOError(TransferError.short_read)
                        count = await handle.write(data, offset=off)
                        if count != length:
                            raise IOError(TransferError.short_write)
                    else:
                        data = await self._read_full(handle, off, length)
                        file.seek(off)
                        if file.write(data) != length:
                            raise IOError(TransferError.short_write)

        count = min(self.workers, max(1, (size + self.block_size - 1) // self.block_size))
        await self._join([worker() for _ in range(count)])
        self.transfer_seconds += time.perf_counter() - started

    @staticmethod
    async def _join(coros):
        tasks = [asyncio.create_task(coro) for coro in coros]
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    @staticmethod
    async def _read_full(handle, offset, length):
        chunks = []
        done = 0
        while done < length:
            data = await handle.read(length - done, offset=offset + done)
            if not data or len(data) > length - done:
                raise IOError(TransferError.short_read)
            chunks.append(data)
            done += len(data)
        return b''.join(chunks)

    async def _remote_digest(self, conn, sftp, path, size):
        value = None
        if self.verify == TransferOption.auto:
            value = await self._remote_hash(conn, path)
        readback = value is None
        if readback:
            value = await self._hash_readback(sftp, path, size)
        if (await sftp.stat(path)).size != size:
            raise IOError(TransferError.changed)
        return value, readback

    @staticmethod
    async def _remote_hash(conn, path):
        quoted = shlex.quote(path)
        script = (
            "import hashlib,sys\nh=hashlib.sha256()\n"
            "with open(sys.argv[1],'rb') as f:\n"
            " while True:\n  b=f.read(1048576)\n  if not b: break\n  h.update(b)\n"
            "print(h.hexdigest())"
        )
        commands = [
            'sha256sum < ' + quoted,
            'busybox sha256sum < ' + quoted,
            'openssl dgst -sha256 < ' + quoted,
            'python3 -c ' + shlex.quote(script) + ' ' + quoted,
        ]
        for command in commands:
            try:
                result = await conn.run(command, check=False, timeout=120)
            except (asyncssh.ChannelOpenError, asyncio.TimeoutError):
                continue
            if result.exit_status == 0:
                matches = re.findall(r'(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])', result.stdout)
                if len(matches) == 1:
                    return matches[0].lower()
        return None

    async def _hash_readback(self, sftp, path, size):
        digest = hashlib.sha256()
        async with sftp.open(path, 'rb', encoding=None, max_requests=1) as handle:
            window = self.block_size * self.workers
            for start in range(0, size, window):
                offsets = range(start, min(size, start + window), self.block_size)
                blocks = await self._join([self._read_full(handle, off, min(self.block_size, size - off)) for off in offsets])
                for data in blocks:
                    digest.update(data)
        return digest.hexdigest()

    @staticmethod
    async def _publish(sftp, temp, remote):
        try:
            await sftp.posix_rename(temp, remote)
            return
        except asyncssh.SFTPOpUnsupported:
            first = None
        except asyncssh.SFTPError as exc:
            first = exc
        try:
            # Standard SFTP rename refuses to replace an existing target. Never delete it first.
            await sftp.rename(temp, remote)
        except asyncssh.SFTPError:
            if first is not None:
                raise first
            raise

    @staticmethod
    def _local_unchanged(path, stamp):
        current = path.stat()
        if (current.st_size, current.st_mtime_ns) != (stamp.st_size, stamp.st_mtime_ns):
            raise IOError(TransferError.changed)

    @staticmethod
    def _hash_local(path):
        digest = hashlib.sha256()
        with open(path, 'rb') as file:
            while data := file.read(1048576):
                digest.update(data)
        return digest.hexdigest()

    async def _shell(self, conn, local, remote, upload):
        temp = remote + '.part-' + uuid.uuid4().hex if upload else str(local) + '.part-' + uuid.uuid4().hex
        created = False
        try:
            if upload:
                stamp = local.stat()
                size = stamp.st_size
                started = time.perf_counter()
                digest = await asyncio.to_thread(self._hash_local, local)
                self.verify_seconds += time.perf_counter() - started
                parent = posixpath.dirname(remote) or '.'
                command = 'mkdir -p ' + shlex.quote(parent) + ' && (umask 077; set -C; cat > ' + shlex.quote(temp) + ')'
                started = time.perf_counter()
                async with conn.create_process(command, encoding=None) as proc:
                    created = True
                    with open(local, 'rb') as file:
                        while data := file.read(self.block_size):
                            proc.stdin.write(data)
                            await proc.stdin.drain()
                    proc.stdin.write_eof()
                    result = await proc.wait()
                    if result.exit_status != 0:
                        raise IOError(TransferError.short_write)
                self.transfer_seconds += time.perf_counter() - started
                started = time.perf_counter()
                self._local_unchanged(local, stamp)
                remote_hash, readback = await self._shell_digest(conn, temp, size)
                self._local_unchanged(local, stamp)
                if remote_hash != digest:
                    raise IOError(TransferError.verify)
                self.verify_seconds += time.perf_counter() - started
                await conn.run('test ! -d ' + shlex.quote(remote) + ' && mv -f ' + shlex.quote(temp) + ' ' + shlex.quote(remote), check=True)
            else:
                size = await self._shell_size(conn, remote)
                started = time.perf_counter()
                with open(temp, 'xb') as file:
                    created = True
                    await self._shell_read(conn, remote, file)
                self.transfer_seconds += time.perf_counter() - started
                started = time.perf_counter()
                if Path(temp).stat().st_size != size:
                    raise IOError(TransferError.short_read)
                digest = await asyncio.to_thread(self._hash_local, temp)
                remote_hash, readback = await self._shell_digest(conn, remote, size)
                if remote_hash != digest:
                    raise IOError(TransferError.verify)
                self.verify_seconds += time.perf_counter() - started
                os.replace(temp, local)
            created = False
            return self._result(size, TransferMode.shell, digest, remote_hash, readback)
        finally:
            if created:
                if upload:
                    await conn.run('rm -f ' + shlex.quote(temp), check=False)
                else:
                    Path(temp).unlink(missing_ok=True)

    @staticmethod
    async def _shell_size(conn, path):
        result = await conn.run('wc -c < ' + shlex.quote(path), check=True)
        return int(result.stdout.strip())

    async def _shell_digest(self, conn, path, size):
        value = await self._remote_hash(conn, path) if self.verify == TransferOption.auto else None
        readback = value is None
        if readback:
            digest = hashlib.sha256()
            count = await self._shell_read(conn, path, digest)
            if count != size:
                raise IOError(TransferError.verify)
            value = digest.hexdigest()
        if await self._shell_size(conn, path) != size:
            raise IOError(TransferError.changed)
        return value, readback

    async def _shell_read(self, conn, path, target):
        count = 0
        async with conn.create_process('cat ' + shlex.quote(path), encoding=None) as proc:
            while data := await proc.stdout.read(self.block_size):
                if hasattr(target, 'update'):
                    target.update(data)
                elif target.write(data) != len(data):
                    raise IOError(TransferError.short_write)
                count += len(data)
            result = await proc.wait()
            if result.exit_status != 0:
                raise IOError(TransferError.short_read)
        return count

    def _result(self, size, method, digest, remote, readback):
        return {
            TransferResult.bytes: size,
            TransferResult.method: method,
            TransferResult.sha256: digest,
            TransferResult.verified: True,
            TransferResult.verification: TransferResult.readback if readback else TransferResult.remote_sha256,
            TransferResult.remote_sha256: remote,
            TransferResult.workers: self.workers if method == TransferMode.sftp else 1,
            TransferResult.block_size: self.block_size,
            TransferResult.transfer_seconds: self.transfer_seconds,
            TransferResult.verify_seconds: self.verify_seconds,
        }
