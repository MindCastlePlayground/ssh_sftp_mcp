from .rpc import StdioApp
from .constants import Msg
from .log import LogFactory


class Main:
    @staticmethod
    def run() -> None:
        LogFactory.setup()
        try:
            StdioApp().run()
        except BaseException:
            LogFactory.get().exception(Msg.server_failed)
            raise


Main.run()
