from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from .constants import Field, ServiceKey


class ServiceSpec:
    def __init__(self, name: str, path: Path, entry: str) -> None:
        self.name = name
        self.path = path
        self.entry = entry


class ServiceSig:
    def __init__(self, mtime_ns: int, size: int) -> None:
        self.mtime_ns = mtime_ns
        self.size = size

    def same(self, other: "ServiceSig") -> bool:
        return self.mtime_ns == other.mtime_ns and self.size == other.size

    def text(self) -> str:
        return f"{self.mtime_ns}{ServiceKey.dash}{self.size}"

    def at(self) -> str:
        seconds = self.mtime_ns / 1_000_000_000
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class ServiceRef:
    def __init__(self, module: ModuleType, sig: ServiceSig) -> None:
        self.module = module
        self.sig = sig


class ServiceNames:
    @staticmethod
    def clean(value: str) -> str:
        chars = []
        for char in value:
            if char.isalnum() or char == ServiceKey.underline:
                chars.append(char)
            else:
                chars.append(ServiceKey.underline)
        return "".join(chars)

    @staticmethod
    def module(spec: ServiceSpec, sig: ServiceSig) -> str:
        name = ServiceNames.clean(spec.name)
        token = ServiceNames.clean(sig.text())
        return f"{ServiceKey.prefix}{name}{ServiceKey.underline}{token}"

    @staticmethod
    def prefix(spec: ServiceSpec) -> str:
        name = ServiceNames.clean(spec.name)
        return f"{ServiceKey.prefix}{name}{ServiceKey.underline}"


class ModuleBox:
    @staticmethod
    def sig(path: Path) -> ServiceSig:
        stat = path.stat()
        return ServiceSig(stat.st_mtime_ns, stat.st_size)

    @staticmethod
    def purge(spec: ServiceSpec, keep: str) -> None:
        prefix = ServiceNames.prefix(spec)
        for name in list(sys.modules):
            if name.startswith(prefix) and name != keep:
                del sys.modules[name]

    @staticmethod
    def load(spec: ServiceSpec) -> ServiceRef:
        path = spec.path.resolve()
        sig = ModuleBox.sig(path)
        module_name = ServiceNames.module(spec, sig)
        module = sys.modules.get(module_name)
        if module is None:
            # Compile the bytes just read. importlib's path cache can reuse a
            # pyc from the previous service version when the file is rewritten
            # under a new module name.
            module = ModuleType(module_name)
            module.__file__ = str(path)
            sys.modules[module_name] = module
            code = compile(path.read_bytes(), str(path), "exec")
            exec(code, module.__dict__)
        ModuleBox.purge(spec, module_name)
        return ServiceRef(module, sig)


class ServiceHost:
    def __init__(self, spec: ServiceSpec) -> None:
        self.spec = spec
        self.ref: ServiceRef | None = None

    def load(self) -> ModuleType:
        sig = ModuleBox.sig(self.spec.path.resolve())
        if self.ref is None or not self.ref.sig.same(sig):
            self.ref = ModuleBox.load(self.spec)
        return self.ref.module

    def api(self, *args: Any, **kwargs: Any) -> Any:
        module = self.load()
        entry = getattr(module, self.spec.entry)
        return entry(*args, **kwargs)

    def version(self) -> str:
        return ModuleBox.sig(self.spec.path.resolve()).text()

    def info(self) -> dict:
        path = self.spec.path.resolve()
        sig = ModuleBox.sig(path)
        loaded = self.ref.sig.text() if self.ref else None
        return {
            Field.service: self.spec.name,
            Field.path: str(path),
            Field.version: sig.text(),
            Field.loaded_version: loaded,
            Field.mtime: sig.at(),
            Field.mtime_ns: sig.mtime_ns,
            Field.size: sig.size,
        }
