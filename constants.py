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
    transfer = "transfer.py"


class ServiceName:
    tool = "tool"
    transfer = "transfer"


class ServiceEntry:
    tool = "ToolApi"
    transfer = "AsyncTransfer"


class ServiceError:
    module_spec = "cannot create service module spec"
    loader = "service module has no loader"


class Env:
    home = "SSH_SFTP_MCP_HOME"
    service_host = "SSH_SFTP_MCP_SERVICE_HOST"
    service_port = "SSH_SFTP_MCP_SERVICE_PORT"
    service_start = "SSH_SFTP_MCP_SERVICE_START"
    python = "SSH_SFTP_MCP_PYTHON"
    pythonpath = "PYTHONPATH"


class Arg:
    service = "--service"


class ModuleName:
    package = "ssh_sftp_mcp"


class ServiceDefault:
    host = "127.0.0.1"
    port = 8765
    wait = 5.0
    connect_timeout = 1.0
    retry_sleep = 0.05
    backlog = 16
    recv = 1048576


class ServiceCmd:
    ping = "ping"
    call = "call"


class EnvValue:
    false = "0"
    no = "false"
    off = "off"


class Default:
    port = 22
    namespace = "default"
    shell = "sh"
    read = 65536


class NetmikoKey:
    device_type = "device_type"
    host = "host"
    port = "port"
    username = "username"
    password = "password"
    conn_timeout = "conn_timeout"
    auth_timeout = "auth_timeout"
    banner_timeout = "banner_timeout"
    blocking_timeout = "blocking_timeout"
    timeout = "timeout"
    session_timeout = "session_timeout"
    keepalive = "keepalive"
    allow_agent = "allow_agent"
    use_keys = "use_keys"
    fast_cli = "fast_cli"
    encoding = "encoding"
    default_enter = "default_enter"


class NetmikoValue:
    terminal = "terminal_server"


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
    input = "input"
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
    stderr = "stderr"
    next_cursor = "next_cursor"
    done = "done"
    started_at = "started_at"
    ended_at = "ended_at"
    last_seen_at = "last_seen_at"
    origin = "origin"
    bytes = "bytes"
    path = "path"
    old_ip = "old_ip"
    host = "host"
    fallback = "fallback"
    method = "method"
    service = "service"
    version = "version"
    loaded_version = "loaded_version"
    mtime = "mtime"
    mtime_ns = "mtime_ns"
    size = "size"
    items = "items"
    index = "index"
    ok = "ok"


class LiveEnv:
    enabled = "SSH_SFTP_MCP_E2E_REAL"
    config = "SSH_SFTP_MCP_E2E_CONFIG"


class LiveConfigKey:
    host = "host"
    user = "user"
    port = "port"
    credential_xml = "credentialXml"


class LiveRole:
    public = "public"
    private = "private"


class LiveName:
    pg_container = "ssh_sftp_mcp_pg"
    pg_image = "docker.m.daocloud.io/library/postgres:16-alpine"
    pg_user = "codex"
    pg_password = "CodexPass_2026"
    pg_db = "codex_e2e"
    pg_port = 15432


class LiveMarker:
    private_basic = "private-basic-ok"
    public_basic = "public-basic-ok"
    escape = "ESC: space semi; dollar:$ backslash:\\ quote:\" single:' pipe:| amp:&"
    weak = "weak-done"
    prompt_password = "Password:"
    prompt_choice = "Continue? [y/n]"
    interactive_pw = "PW:codex-secret"
    interactive_choice = "YN:y"
    container = "container-ok"
    database = "db-ok"
    container_database = "container-db-ok"


class Tool:
    register = "ssh_register"
    update = "ssh_update"
    delete = "ssh_delete"
    connect = "ssh_connect"
    send = "ssh_send_input"
    send_many = "ssh_send_many"
    output = "ssh_read_output"
    once = "ssh_temp_exec"
    sessions = "ssh_list_sessions"
    close = "ssh_close"
    status = "ssh_status"
    upload = "sftp_upload"
    download = "sftp_download"
    servers = "ssh_list_servers"
    version = "service_version"
    service_start = "service_start"


class ToolDesc:
    register = "Register one SSH server by IP with plaintext password credentials."
    update = "Update a registered SSH server. Use old_ip when changing ip."
    delete = "Delete one registered SSH server by IP and close its live sessions."
    connect = "Open a persistent interactive SSH shell session for a registered IP. Return session_id and include that session_id in the model's user-facing response so later calls can reuse it."
    send = "Send input to an existing persistent interactive SSH session. Use host/docker/k8s for shell commands. Use raw for REPL programs such as psql so SQL and backslash meta-commands are sent unchanged. Return session_id and command_id; include session_id in the model's user-facing response and read output with ssh_read_output."
    send_many = "Send inputs to multiple persistent interactive SSH sessions concurrently. Each item follows ssh_send_input shape and returns its session_id."
    output = "Read buffered output from a persistent interactive SSH session by cursor. Require session_id and include that session_id in the model's user-facing response."
    once = "Temporarily connect, execute one command, block until stdout/stderr are read, then close. This is a single-use command runner and is not recommended; prefer persistent interactive sessions with ssh_connect, ssh_send_input, and ssh_read_output."
    sessions = "List existing sessions for an IP, including disconnected sessions."
    close = "Close all sessions for an IP, or one session when session_id is supplied."
    status = "Return session status and optional command status."
    upload = "Upload one file with bounded AsyncSSH parallel writes. Always verify SHA-256 before publishing; auto uses remote hashing or full readback. workers=1..128, block_size=4096..131072."
    download = "Download one file with bounded AsyncSSH parallel reads. Always verify SHA-256 before replacing the local target; auto uses remote hashing or full readback. workers=1..128, block_size=4096..131072."
    servers = "List registered SSH servers without passwords."
    version = "Return the current hot-reload service version timestamp."
    service_start = "Start or verify the local SSH/SFTP backend service process."


class Msg:
    ok = "ok"
    missing = "missing required argument"
    duplicate_ip = "ip already registered"
    unknown_ip = "ip is not registered"
    unknown_session = "session not found"
    unknown_command = "command not found"
    disconnected = "ssh session is disconnected"
    session_mismatch = "session does not belong to requested server"
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
    service_down = "service process is unavailable"
    service_bad_cmd = "unknown service command"
    bad_batch = "invalid batch items"
    batch_too_large = "batch item limit exceeded"
    raw_sent = "raw input sent; completion is not tracked"
    command_running = "another command is still running in this session"


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


class Prompt:
    pattern = r"(?m)(?:^|\n)[^\n]{0,200}[#>$%]\s*$"
    line = r"[^\n]{0,200}[#>$%]\s*$"


class Shell:
    enter = "\n"
    echo_off = "stty -echo 2>/dev/null || true\n"


class Regex:
    ansi = r"\x1b\[[0-?]*[ -/]*[@-~]"


class Limits:
    chunks = 2000
    tail = 8192
    read = Default.read
    recv = 32768
    send = 32768
    file_chunk = 1048576
    poll = 0.05
    connect_timeout = 15
    blocking_timeout = 20
    session_timeout = 60
    keepalive = 30
    prep_wait = 0.2
    batch = 32
    batch_workers = 8
    sftp_prefetch = 64
    transfer_timeout = None
