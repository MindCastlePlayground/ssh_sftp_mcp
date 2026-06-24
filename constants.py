from enum import Enum


class Meta:
    name = "ssh-sftp-mcp"
    version = "0.1.0"
    protocol = "2025-06-18"


class Store:
    dir = ".ssh_sftp_mcp"
    servers = "servers.json"
    log = "ssh_sftp_mcp.log"


class ServiceKey:
    prefix = "_mcp_service_"
    dash = "-"
    underline = "_"


class ServiceFile:
    tool = "tool_service.py"


class ServiceName:
    tool = "tool"


class ServiceEntry:
    tool = "ToolApi"


class ServiceError:
    module_spec = "cannot create service module spec"
    loader = "service module has no loader"


class Env:
    home = "SSH_SFTP_MCP_HOME"


class Default:
    port = 22
    namespace = "default"
    shell = "sh"
    read = 65536


class JsonKey:
    jsonrpc = "jsonrpc"
    id = "id"
    method = "method"
    params = "params"
    result = "result"
    error = "error"
    code = "code"
    message = "message"
    protocol_version = "protocolVersion"
    capabilities = "capabilities"
    server_info = "serverInfo"
    name = "name"
    version = "version"
    tools = "tools"
    list_changed = "listChanged"
    arguments = "arguments"
    content = "content"
    type = "type"
    text = "text"
    is_error = "isError"
    structured = "structuredContent"
    input_schema = "inputSchema"
    description = "description"
    properties = "properties"
    required = "required"
    additional = "additionalProperties"
    items = "items"
    default = "default"
    enum = "enum"


class RpcMethod:
    initialize = "initialize"
    initialized = "notifications/initialized"
    ping = "ping"
    tools_list = "tools/list"
    tools_call = "tools/call"


class RpcValue:
    version = "2.0"
    object = "object"
    string = "string"
    integer = "integer"
    boolean = "boolean"
    text = "text"
    array = "array"


class Text:
    bom = "\ufeff"
    encoding = "utf-8"
    empty = ""


class Log:
    name = "ssh_sftp_mcp"
    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    date = "%Y-%m-%d %H:%M:%S"
    level = "SSH_SFTP_MCP_LOG_LEVEL"
    default = "INFO"
    ready = "logging ready"


class RpcError:
    parse = -32700
    invalid_request = -32600
    method_not_found = -32601
    invalid_params = -32602
    internal = -32603


class Field:
    ip = "ip"
    port = "port"
    username = "username"
    password = "password"
    session_id = "session_id"
    command_id = "command_id"
    command = "command"
    cursor = "cursor"
    limit = "limit"
    local_path = "local_path"
    remote_path = "remote_path"
    mode = "mode"
    container = "container"
    namespace = "namespace"
    pod = "pod"
    shell = "shell"
    registered = "registered"
    sessions = "sessions"
    servers = "servers"
    status = "status"
    reason = "reason"
    exit_code = "exit_code"
    output = "output"
    next_cursor = "next_cursor"
    done = "done"
    started_at = "started_at"
    ended_at = "ended_at"
    last_seen_at = "last_seen_at"
    origin = "origin"
    bytes = "bytes"
    path = "path"
    old_ip = "old_ip"
    fallback = "fallback"
    method = "method"
    service = "service"
    version = "version"
    loaded_version = "loaded_version"
    mtime = "mtime"
    mtime_ns = "mtime_ns"
    size = "size"


class Tool:
    register = "ssh_register"
    update = "ssh_update"
    delete = "ssh_delete"
    connect = "ssh_connect"
    temp = "ssh_temp_connect"
    sessions = "ssh_list_sessions"
    close = "ssh_close"
    exec = "ssh_exec"
    read = "ssh_read"
    status = "ssh_status"
    upload = "sftp_upload"
    download = "sftp_download"
    servers = "ssh_list_servers"
    version = "service_version"


class ToolDesc:
    register = "Register one SSH server by IP with plaintext password credentials."
    update = "Update a registered SSH server. Use old_ip when changing ip."
    delete = "Delete one registered SSH server by IP and close its live sessions."
    connect = "Open a persistent SSH shell session for a registered IP and return a session ID."
    temp = "Open a persistent SSH shell session without saving credentials and return a session ID."
    sessions = "List existing sessions for an IP, including disconnected sessions."
    close = "Close all sessions for an IP, or one session when session_id is supplied."
    exec = "Send a command to a persistent session. Supports host, docker, and k8s modes."
    read = "Read buffered output by cursor for a session or command."
    status = "Return session status and optional command status."
    upload = "Upload a local file to a server with SFTP."
    download = "Download a remote file from a server with SFTP."
    servers = "List registered SSH servers without passwords."
    version = "Return the current hot-reload service version timestamp."


class Msg:
    ok = "ok"
    missing = "missing required argument"
    duplicate_ip = "ip already registered"
    unknown_ip = "ip is not registered"
    unknown_session = "session not found"
    unknown_command = "command not found"
    disconnected = "ssh session is disconnected"
    no_credential = "no registered credentials or live session for ip"
    tool_not_found = "tool not found"
    invalid_json = "invalid json"
    invalid_request = "invalid request"
    bad_store = "registered server json is invalid; keeping last known registry"
    deleted = "deleted"
    updated = "updated"
    remote_failed = "remote command failed"
    local_missing = "local source file not found"
    stdio_eof = "stdio input closed"
    stdio_closed = "stdio output closed"
    server_failed = "mcp server stopped unexpectedly"


class TransferMethod:
    sftp = "sftp"
    shell = "shell"


class ShellCmd:
    mkdir = "mkdir -p"
    cat = "cat"
    q = "sh -lc"
    and_op = " && "


class Origin(Enum):
    registered = "registered"
    temporary = "temporary"


class SessionStatus(Enum):
    connecting = "connecting"
    connected = "connected"
    disconnected = "disconnected"
    closed = "closed"
    failed = "failed"


class CommandStatus(Enum):
    queued = "queued"
    running = "running"
    done = "done"
    unknown = "unknown"
    failed = "failed"


class ExecMode(Enum):
    host = "host"
    docker = "docker"
    k8s = "k8s"
    raw = "raw"


class Mark:
    prefix = "__MCP_CMD_"
    start = "_START__"
    end = "_END__"
    code = ":"


class Shell:
    echo_off = "stty -echo 2>/dev/null || true\n"


class Regex:
    ansi = r"\x1b\[[0-?]*[ -/]*[@-~]"
    start = r"__MCP_CMD_[0-9a-f]+_START__\r?\n?"
    end = r"__MCP_CMD_[0-9a-f]+_END__:\d+\r?\n?"


class Limits:
    chunks = 2000
    tail = 8192
    read = Default.read
    recv = 32768
    file_chunk = 1048576
    poll = 0.05
    connect_timeout = 15
    keepalive = 30
    prep_wait = 0.2
