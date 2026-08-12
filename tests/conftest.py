"""pytest 全局配置：标记测试环境（apps.py ready() 据此跳过启动时 SSH 同步）。"""

import os

os.environ.setdefault("DJANGO_TESTING", "1")
