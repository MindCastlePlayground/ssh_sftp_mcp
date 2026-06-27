import sys

from .constants import Arg, Msg
from .rpc import StdioApp
from .log import LogFactory
from .service_client import ServiceConfig
from .service_daemon import ServiceDaemon


class Main:
    @staticmethod
    def run() -> None:
        LogFactory.setup()
        try:
            if Main.service():
                ServiceDaemon(ServiceConfig(start=False)).run()
            else:
                StdioApp().run()
        except BaseException:
            LogFactory.get().exception(Msg.server_failed)
            raise

    @staticmethod
    def service() -> bool:
        return Arg.service in sys.argv[1:]


Main.run()
