import logging
import os
import sys
from pathlib import Path

from .constants import Log, Store, Text


class CloseFileHandler(logging.Handler):
    terminator = "\n"

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def emit(self, record: logging.LogRecord) -> None:
        if not self.path.parent.exists():
            return
        msg = self.format(record)
        with self.path.open("a", encoding=Text.encoding) as stream:
            stream.write(msg + self.terminator)


class LogFactory:
    ready = False
    files: set[Path] = set()

    @classmethod
    def setup(cls, root: Path | None = None) -> logging.Logger:
        logger = logging.getLogger(Log.name)
        level = getattr(logging, os.environ.get(Log.level, Log.default).upper(), logging.INFO)
        logger.setLevel(level)
        logger.propagate = False
        formatter = logging.Formatter(Log.fmt, Log.date)
        if not cls.ready:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(formatter)
            stream.setLevel(level)
            logger.addHandler(stream)
            cls.ready = True
        if root:
            root.mkdir(parents=True, exist_ok=True)
            path = root / Store.log
            if path not in cls.files:
                file = CloseFileHandler(path)
                file.setFormatter(formatter)
                file.setLevel(level)
                logger.addHandler(file)
                cls.files.add(path)
        logger.info(Log.ready)
        return logger

    @staticmethod
    def get() -> logging.Logger:
        return logging.getLogger(Log.name)
