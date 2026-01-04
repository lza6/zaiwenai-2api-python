import uvicorn
import asyncio
from PySide6.QtCore import QThread, Signal
from app.core.config import settings

class ServerWorker(QThread):
    log_signal = Signal(str, str) # message, level
    status_signal = Signal(bool) # is_running

    def __init__(self):
        super().__init__()
        self.server = None
        self.should_exit = False

    def run(self):
        self.log_signal.emit("正在启动 API 服务...", "info")
        try:
            config = uvicorn.Config(
                app="app.entry:app",
                host=settings.HOST,
                port=settings.PORT,
                log_level="info",
                reload=False
            )
            self.server = uvicorn.Server(config)
            self.status_signal.emit(True)
            self.log_signal.emit(f"服务已启动: http://{settings.HOST}:{settings.PORT}", "success")
            self.server.run()
            
        except Exception as e:
            self.log_signal.emit(f"服务启动失败: {str(e)}", "error")
        finally:
            self.status_signal.emit(False)
            self.log_signal.emit("服务已停止", "warning")

    def stop(self):
        if self.server:
            self.server.should_exit = True
