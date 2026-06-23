import json
import sys

from .constants import JsonKey, Meta, Msg, RpcError, RpcMethod, RpcValue, Text
from .log import LogFactory
from .tools import ToolCatalog, ToolFactory, ToolService


class Response:
    @staticmethod
    def ok(req_id, result: dict) -> dict:
        return {JsonKey.jsonrpc: RpcValue.version, JsonKey.id: req_id, JsonKey.result: result}

    @staticmethod
    def err(req_id, code: int, message: str) -> dict:
        return {
            JsonKey.jsonrpc: RpcValue.version,
            JsonKey.id: req_id,
            JsonKey.error: {JsonKey.code: code, JsonKey.message: message},
        }


class McpServer:
    def __init__(self, service: ToolService | None = None):
        self.service = service or ToolFactory.make()
        self.log = LogFactory.get()

    def handle(self, req: dict) -> dict | None:
        try:
            if not isinstance(req, dict):
                return Response.err(None, RpcError.invalid_request, Msg.invalid_request)
            req_id = req.get(JsonKey.id)
            method = req.get(JsonKey.method)
            params = req.get(JsonKey.params, {})
            self.log.info("rpc request method=%s id=%s", method, req_id)
            if method == RpcMethod.initialized:
                return None
            if method == RpcMethod.initialize:
                return Response.ok(req_id, self.init(params))
            if method == RpcMethod.ping:
                return Response.ok(req_id, {})
            if method == RpcMethod.tools_list:
                return Response.ok(req_id, {JsonKey.tools: ToolCatalog.list()})
            if method == RpcMethod.tools_call:
                return Response.ok(req_id, self.call(params))
            return Response.err(req_id, RpcError.method_not_found, str(method))
        except Exception as exc:
            self.log.exception("rpc request failed")
            req_id = req.get(JsonKey.id) if isinstance(req, dict) else None
            return Response.err(req_id, RpcError.internal, str(exc))

    def init(self, params: dict) -> dict:
        version = params.get(JsonKey.protocol_version, Meta.protocol) if isinstance(params, dict) else Meta.protocol
        return {
            JsonKey.protocol_version: version,
            JsonKey.capabilities: {JsonKey.tools: {JsonKey.list_changed: False}},
            JsonKey.server_info: {JsonKey.name: Meta.name, JsonKey.version: Meta.version},
        }

    def call(self, params: dict) -> dict:
        if not isinstance(params, dict):
            return self.service.result({JsonKey.message: Msg.invalid_request}, True)
        name = str(params.get(JsonKey.name, ""))
        args = params.get(JsonKey.arguments, {})
        return self.service.call(name, args)


class StdioApp:
    def __init__(self, server: McpServer | None = None):
        self.server = server or McpServer()
        self.log = LogFactory.get()

    def run(self) -> None:
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip().lstrip(Text.bom)
                if not line:
                    continue
                self.once(line)
            except Exception:
                self.log.exception("stdio loop recovered from error")

    def once(self, line: str) -> None:
        try:
            req = json.loads(line)
            res = self.server.handle(req)
            if res is not None:
                self.write(res)
        except json.JSONDecodeError:
            self.write(Response.err(None, RpcError.parse, Msg.invalid_json))
        except Exception as exc:
            self.log.exception("stdio request failed")
            self.write(Response.err(None, RpcError.internal, str(exc)))

    def write(self, res: dict) -> None:
        sys.stdout.write(json.dumps(res, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
