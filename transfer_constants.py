class TransferOption:
    workers = "workers"
    block_size = "block_size"
    verify = "verify"
    auto = "auto"
    readback = "readback"


class TransferResult:
    bytes = "bytes"
    method = "method"
    sha256 = "sha256"
    verified = "verified"
    verification = "verification"
    remote_sha256 = "remote_sha256"
    readback = "readback"
    workers = "workers"
    block_size = "block_size"
    transfer_seconds = "transfer_seconds"
    verify_seconds = "verify_seconds"
    total_seconds = "total_seconds"


class TransferMode:
    upload = "upload"
    download = "download"
    sftp = "sftp"
    shell = "shell"


class TransferError:
    bad_option = "invalid transfer option"
    short_read = "short remote read"
    short_write = "short remote write"
    changed = "source changed during transfer"
    verify = "transfer integrity verification failed"
