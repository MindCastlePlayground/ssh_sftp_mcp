from dataclasses import dataclass, field
from time import time

from .constants import CommandStatus, Field, Origin, SessionStatus


@dataclass
class ServerInfo:
    ip: str
    port: int
    username: str
    password: str

    def data(self) -> dict:
        return {
            Field.ip: self.ip,
            Field.port: self.port,
            Field.username: self.username,
            Field.password: self.password,
        }

    def public(self) -> dict:
        return {
            Field.ip: self.ip,
            Field.port: self.port,
            Field.username: self.username,
            Field.registered: True,
        }

    @classmethod
    def from_data(cls, data: dict) -> "ServerInfo":
        return cls(
            ip=str(data[Field.ip]),
            port=int(data[Field.port]),
            username=str(data[Field.username]),
            password=str(data[Field.password]),
        )


@dataclass
class OutputChunk:
    seq: int
    text: str
    start: int = 0
    at: float = field(default_factory=time)

    def data(self) -> dict:
        return {
            Field.cursor: self.seq,
            Field.output: self.text,
            Field.last_seen_at: self.at,
        }


@dataclass
class CommandInfo:
    command_id: str
    command: str
    sent: str = ""
    status: CommandStatus = CommandStatus.queued
    start_seq: int = 0
    end_seq: int | None = None
    exit_code: int | None = None
    started_at: float = field(default_factory=time)
    ended_at: float | None = None
    reason: str = ""

    def data(self) -> dict:
        return {
            Field.command_id: self.command_id,
            Field.command: self.command,
            Field.status: self.status.value,
            Field.started_at: self.started_at,
            Field.ended_at: self.ended_at,
            Field.exit_code: self.exit_code,
            Field.done: self.status in (CommandStatus.done, CommandStatus.failed),
            Field.reason: self.reason,
        }


@dataclass
class SessionView:
    session_id: str
    ip: str
    port: int
    username: str
    origin: Origin
    status: SessionStatus
    reason: str
    started_at: float
    last_seen_at: float

    def data(self) -> dict:
        return {
            Field.session_id: self.session_id,
            Field.ip: self.ip,
            Field.port: self.port,
            Field.username: self.username,
            Field.origin: self.origin.value,
            Field.status: self.status.value,
            Field.reason: self.reason,
            Field.started_at: self.started_at,
            Field.last_seen_at: self.last_seen_at,
        }
