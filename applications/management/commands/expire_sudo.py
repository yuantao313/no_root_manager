"""撤销已到期的 sudo 权限（当日失效，次日需重新申请）。

用法：uv run python manage.py expire_sudo
建议配置 cron 每天执行一次，例如：
0 1 * * * cd /path/to/project && uv run python manage.py expire_sudo
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from applications.models import SudoGrant
from servers.management import revoke_sudo


class Command(BaseCommand):
    help = "撤销已到期的 sudo 权限（当日失效）"

    def handle(self, *args, **options):
        now = timezone.now()
        expired = SudoGrant.objects.filter(status=SudoGrant.Status.ACTIVE, expires_at__lt=now)
        revoked = 0
        for grant in expired:
            ok, msg = revoke_sudo(grant.server, grant.username)
            grant.status = SudoGrant.Status.EXPIRED
            grant.revoked_at = now
            grant.revoke_note = (grant.revoke_note + "\n" if grant.revoke_note else "") + f"自动失效：{msg}"
            grant.save(update_fields=["status", "revoked_at", "revoke_note"])
            revoked += 1
            self.stdout.write(self.style.SUCCESS(f"已撤销：{grant}"))
        self.stdout.write(self.style.SUCCESS(f"完成，共撤销 {revoked} 条 sudo 权限"))
