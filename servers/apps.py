import threading

from django.apps import AppConfig


class ServersConfig(AppConfig):
    name = "servers"

    def ready(self):
        """服务启动时后台同步 NPU 状态到内存缓存（不阻塞启动）。

        仅在有 NPU 服务器时执行 SSH 检测；用 daemon 线程避免拖慢启动，
        也避免在 pytest/迁移等场景下同步阻塞。
        """
        from django.conf import settings

        # 测试环境跳过启动同步（pytest 隔离库无真实机器）
        if getattr(settings, "DJANGO_TESTING", False):
            return
        try:
            threading.Thread(target=self._sync_npu, daemon=True).start()
        except Exception:  # noqa: BLE001 —— 启动同步失败不影响服务
            pass

    @staticmethod
    def _sync_npu():
        from .management import sync_npu_states

        sync_npu_states()
