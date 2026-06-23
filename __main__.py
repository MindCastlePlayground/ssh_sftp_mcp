from .rpc import StdioApp


class Main:
    @staticmethod
    def run() -> None:
        StdioApp().run()


Main.run()

