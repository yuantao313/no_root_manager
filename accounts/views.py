from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from allauth.socialaccount.internal import flows as socialaccount_flows
from allauth.socialaccount.models import SocialApp
from django import forms
from django.contrib import messages
from django.contrib.auth import authenticate, login, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required, login_required
from django.contrib.auth.forms import PasswordResetForm as BasePasswordResetForm
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.views import LoginView, PasswordResetView
from django.core.validators import RegexValidator
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy

from config.decorators import superuser_required
from notifications.forms import WebhookForm
from notifications.models import EmailConfig, WebhookConfig
from notifications.services import send_email, send_email_with_config, send_webhook_to
from servers.management import push_notices

from .email_verify import send_smtp_code, send_user_email_code, verify_code
from .models import Announcement, EmailVerification, SystemConfig, UserProfile


class ProfileForm(forms.Form):
    """个人资料编辑：姓名（一体化，映射 first_name）+ 工号 + 邮箱 + 验证码。"""

    name = forms.CharField(label="姓名", max_length=100, required=False)
    employee_id = forms.CharField(label="工号", max_length=50, required=False)
    email = forms.EmailField(label="邮箱", required=False)
    code = forms.CharField(
        label="邮箱验证码", max_length=6, required=False, help_text="修改邮箱时需先点击“发送验证码”并填写"
    )

    def __init__(self, *args, **kwargs):
        instance = kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)
        self.instance = instance
        if instance:
            self.fields["name"].initial = instance.first_name
            self.fields["employee_id"].initial = getattr(getattr(instance, "profile", None), "employee_id", "") or ""
            self.fields["email"].initial = instance.email

    def save(self, commit=True):
        user = self.instance
        user.first_name = self.cleaned_data["name"].strip()
        user.email = self.cleaned_data["email"].strip()
        # 工号写入扩展资料（不存在则创建）
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.employee_id = self.cleaned_data["employee_id"].strip()
        if commit:
            user.save()
            profile.save()
        return user


class NRMPasswordResetForm(BasePasswordResetForm):
    """密码找回表单：通过系统 SMTP 配置（EmailConfig）发送重置邮件。"""

    def send_mail(
        self, subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name=None
    ):
        subject = render_to_string(subject_template_name, context).strip()
        body = render_to_string(email_template_name, context)
        send_email(subject, body, [to_email])


class NRMPasswordResetView(PasswordResetView):
    template_name = "accounts/password_reset_form.html"
    form_class = NRMPasswordResetForm
    email_template_name = "accounts/password_reset_email.html"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


class RegisterForm(UserCreationForm):
    """注册表单：姓名/工号/邮箱/用户名/密码信息齐全（与 OAuth 补全页一致）。

    工号写入 UserProfile（申请单自动带入），姓名存 first_name。
    """

    username = forms.CharField(
        label="用户名",
        max_length=150,
        validators=[RegexValidator(r"^[A-Za-z0-9_]+\Z", "仅允许字母、数字、下划线")],
        help_text="仅限字母/数字/下划线，注册后不可修改，将作为服务器的登录用户名",
        widget=forms.TextInput(attrs={"autocomplete": "username"}),
    )
    first_name = forms.CharField(
        label="姓名",
        max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "真实姓名"}),
    )
    employee_id = forms.CharField(
        label="工号",
        max_length=50,
        widget=forms.TextInput(attrs={"placeholder": "例如 a00123456"}),
    )
    email = forms.EmailField(
        label="邮箱",
        widget=forms.EmailInput(attrs={"placeholder": "用于接收审批通知"}),
    )

    field_order = ["username", "first_name", "employee_id", "email", "password1", "password2"]

    class Meta:
        model = User
        fields = ["username", "first_name", "employee_id", "email", "password1", "password2"]

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data["first_name"].strip()
        user.email = self.cleaned_data["email"].strip()
        if commit:
            user.save()
        # 工号写入扩展资料（申请单自动带入）
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.employee_id = self.cleaned_data["employee_id"].strip()
        if commit:
            profile.save()
        return user


def register(request):
    """用户注册：所有用户平等注册为普通用户，注册后自动登录。"""
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            # 多认证后端（axes + ModelBackend）必须显式指定 backend
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, f"注册成功，欢迎 {user.username}。")
            return redirect("applications:my")
    else:
        form = RegisterForm()
    return render(request, "accounts/register.html", {"form": form})


@login_not_required
def social_signup(request):
    """GitCode 社交注册页：Tab 支持"创建新账号"或"绑定已有账号"。

    - 创建模式（默认）：用 SignupForm 完成注册（含注册要素）
    - 绑定模式（POST 带 bind_existing）：账号+密码验证通过后，
      sociallogin.connect 绑定到已有用户并直接登录
    """
    sociallogin = socialaccount_flows.signup.get_pending_signup(request)
    if not sociallogin:
        return redirect("accounts:login")

    if request.method == "POST":
        if "bind_existing" in request.POST:
            # 绑定已有账号：账号+密码验证
            username = request.POST.get("bind_username", "").strip()
            password = request.POST.get("bind_password", "")
            user = authenticate(request, username=username, password=password)
            if user is not None:
                sociallogin.connect(request, user)
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                messages.success(request, f"GitCode 已绑定到账号 {user.username}，欢迎回来。")
                return redirect("applications:my")
            messages.error(request, "账号或密码错误，绑定失败。")
            form = GitCodeSignupForm(sociallogin=sociallogin)
        else:
            # 创建新账号：默认注册流程（含注册要素）
            form = GitCodeSignupForm(request.POST, sociallogin=sociallogin)
            if form.is_valid():
                return socialaccount_flows.signup.signup_by_form(request, sociallogin, form)
    else:
        form = GitCodeSignupForm(sociallogin=sociallogin)

    return render(
        request,
        "socialaccount/signup.html",
        {"form": form, "account": sociallogin.account},
    )


def _gitcode_enabled():
    """GitCode OAuth 是否启用：SocialApp 已配置且系统开关开启。"""
    if not SocialApp.objects.filter(provider="gitcode").exists():
        return False
    return SystemConfig.get_singleton().gitcode_enabled


@superuser_required
def toggle_switch(request):
    """系统功能开关即时切换（AJAX）：gitcode / email / webhook。

    切换后立即写入数据库并生效，不依赖各配置页的"保存"按钮。
    返回 JSON：{"ok": true} 或 {"ok": false, "error": "..."}。
    """
    switch = request.POST.get("switch", "")
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "仅支持 POST"}, status=405)
    enabled = request.POST.get("enabled") == "1"
    if switch == "gitcode":
        cfg = SystemConfig.get_singleton()
        cfg.gitcode_enabled = enabled
        cfg.save()
    elif switch == "email":
        cfg = EmailConfig.objects.first() or EmailConfig()
        cfg.enabled = enabled
        cfg.save()
    elif switch == "webhook":
        hook = WebhookConfig.objects.filter(owner__isnull=True).first() or WebhookConfig(owner=None)
        hook.enabled = enabled
        hook.save()
    else:
        return JsonResponse({"ok": False, "error": "未知开关"}, status=400)
    return JsonResponse({"ok": True})


class GitCodeSignupForm(SocialSignupForm):
    """GitCode 首次登录创建新账号：与注册一致，强制填写姓名/工号/用户名/密码/邮箱。

    - 邮箱从 OAuth 预填（SocialSignupForm 的 initial 机制），缺失时手动填写
    - 密码采用 password1/password2 双字段，复用 Django 密码强度校验（与注册一致）
    """

    first_name = forms.CharField(
        label="姓名",
        max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "真实姓名（用于生成目标机用户名）"}),
    )
    employee_id = forms.CharField(
        label="工号",
        max_length=50,
        widget=forms.TextInput(attrs={"placeholder": "例如 a00123456"}),
    )
    password1 = forms.CharField(
        label="密码",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="确认密码",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 仅邮箱从 OAuth 自动填充；用户名/姓名不预填（避免以 gc<id> 占位身份进入系统）
        self.initial.pop("username", None)
        self.initial.pop("first_name", None)
        self.initial.pop("last_name", None)
        # 邮箱必填（OAuth 已提供则预填，未提供需手动填写）
        self.fields["email"].required = True
        self.fields["email"].label = "邮箱"
        self.fields["username"].label = "用户名"
        self.fields["username"].help_text = "仅限字母/数字/下划线，注册后不可修改，将作为服务器的登录用户名"
        self.fields["username"].validators = [RegexValidator(r"^[A-Za-z0-9_]+\Z", "仅允许字母、数字、下划线")]
        # 字段顺序：姓名 → 工号 → 用户名 → 邮箱 → 密码 → 确认密码
        order = ["first_name", "employee_id", "username", "email", "password1", "password2"]
        self.fields = {key: self.fields[key] for key in order if key in self.fields}
        for key in self.fields:
            if key not in order:
                self.fields[key] = self.fields.pop(key)

    def custom_signup(self, request, user):
        # 工号写入扩展资料（申请单自动带入），与注册表单一致
        super().custom_signup(request, user)
        employee_id = (self.cleaned_data.get("employee_id") or "").strip()
        if employee_id:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.employee_id = employee_id
            profile.save()

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("两次输入的密码不一致。")
        return password2

    def _post_clean(self):
        super()._post_clean()
        # 与注册一致：应用 AUTH_PASSWORD_VALIDATORS 密码强度校验
        password1 = self.cleaned_data.get("password1")
        if password1:
            try:
                validate_password(password1)
            except forms.ValidationError as error:
                self.add_error("password1", error)


class GitCodeLoginView(LoginView):
    """登录页：传递 GitCode OAuth 是否已配置（控制入口显示，未配置不崩溃）。"""

    template_name = "accounts/login.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["gitcode_enabled"] = _gitcode_enabled()
        return context


def _email_code_cooldown(request):
    """邮箱验证码剩余冷却秒数（60 秒窗口）。"""
    from django.utils import timezone

    sent_at = request.session.get("email_code_sent_at")
    if not sent_at:
        return 0
    return max(0, 60 - int(timezone.now().timestamp() - sent_at))


@login_required
def send_email_code_ajax(request):
    """AJAX 发送邮箱验证码（修改邮箱前置）：60 秒冷却 + JSON 返回，不刷新页面。"""
    from django.utils import timezone

    email = (request.POST.get("email") or "").strip()
    # 60 秒冷却（session 时间戳）
    sent_at = request.session.get("email_code_sent_at")
    if sent_at:
        remaining = 60 - int(timezone.now().timestamp() - sent_at)
        if remaining > 0:
            return JsonResponse(
                {"ok": False, "error": f"发送过于频繁，请 {remaining} 秒后再试。", "cooldown": remaining}
            )
    if not email:
        return JsonResponse({"ok": False, "error": "请先填写新的邮箱地址。", "cooldown": 0})
    if email == request.user.email:
        return JsonResponse({"ok": False, "error": "新邮箱与当前邮箱相同，无需验证。", "cooldown": 0})
    ok = send_user_email_code(email, request.user)
    if ok:
        request.session["email_code_sent_at"] = int(timezone.now().timestamp())
        return JsonResponse({"ok": True, "error": "", "cooldown": 60})
    return JsonResponse({"ok": False, "error": "验证码发送失败（SMTP 未配置或不可用）。", "cooldown": 0})


@login_required
def verify_email_code_ajax(request):
    """AJAX 校验邮箱验证码：通过才允许保存邮箱，失败前端提示错误（不刷新）。

    注意：此处为非消耗校验（consume=False），真正消耗发生在
    save_profile 保存邮箱时——避免验证码被前端预检消耗后，
    后端二次校验报"已使用"导致邮箱无法保存。
    """
    email = (request.POST.get("email") or "").strip()
    code = (request.POST.get("code") or "").strip()
    ok, err = verify_code(email, code, EmailVerification.PURPOSE_USER_EMAIL, user=request.user, consume=False)
    return JsonResponse({"ok": ok, "error": err})


@login_required
def profile(request):
    """个人中心：资料行内编辑 + 邮箱验证码确认 + 内嵌 Webhook（仅管理员）。"""
    hooks = WebhookConfig.objects.filter(owner=request.user)
    form = ProfileForm(instance=request.user)
    # 个人 Webhook 单例：已有配置时预填表单（保存即覆盖）
    my_hook = hooks.first()
    webhook_form = WebhookForm(
        instance=my_hook,
        initial={"name": my_hook.name, "url": my_hook.url, "enabled": my_hook.enabled} if my_hook else None,
    )

    if request.method == "POST":
        # 1. 发送邮箱验证码（修改邮箱的前置步骤）
        if "send_email_code" in request.POST:
            new_email = request.POST.get("email", "").strip()
            if not new_email:
                messages.error(request, "请先填写新的邮箱地址。")
            elif new_email == request.user.email:
                messages.info(request, "新邮箱与当前邮箱相同，无需验证。")
            else:
                ok = send_user_email_code(new_email, request.user)
                if ok:
                    messages.success(request, f"验证码已发送至 {new_email}，请查收并填写。")
                else:
                    messages.error(request, "验证码发送失败（SMTP 未配置或不可用）。")
            return redirect("accounts:profile")
        # 2. 保存个人资料（邮箱变更需验证码）
        if "save_profile" in request.POST:
            form = ProfileForm(request.POST, instance=request.user)
            if form.is_valid():
                new_email = form.cleaned_data["email"].strip()
                if new_email != request.user.email:
                    # 邮箱变更：必须通过验证码校验
                    ok, err = verify_code(
                        new_email,
                        form.cleaned_data["code"],
                        EmailVerification.PURPOSE_USER_EMAIL,
                        user=request.user,
                    )
                    if not ok:
                        messages.error(request, f"邮箱验证失败：{err}")
                        return redirect("accounts:profile")
                form.save()
                messages.success(request, "个人信息已更新。")
                return redirect("accounts:profile")
        elif "add_webhook" in request.POST and request.user.is_staff:
            webhook_form = WebhookForm(request.POST)
            if webhook_form.is_valid():
                # 个人 Webhook 单例：有则更新，无则新建（save 内自动清理旧的）
                hook = WebhookConfig.objects.filter(owner=request.user).first() or WebhookConfig(owner=request.user)
                hook.name = webhook_form.cleaned_data["name"]
                hook.url = webhook_form.cleaned_data["url"]
                if webhook_form.cleaned_data.get("secret"):
                    hook.secret = webhook_form.cleaned_data["secret"]
                hook.enabled = webhook_form.cleaned_data["enabled"]
                hook.save()
                messages.success(request, f"Webhook「{hook.name}」已保存。")
                return redirect("accounts:profile")
        elif "test_webhook" in request.POST and request.user.is_staff:
            # 测试个人 Webhook：直接对表单填写的 URL 推送测试事件（不保存）
            test_url = request.POST.get("url", "").strip()
            test_secret = request.POST.get("secret", "").strip()
            test_platform = request.POST.get("name", "").strip()
            ok, msg = send_webhook_to(test_url, test_secret, platform=test_platform)
            if ok:
                messages.success(request, f"Webhook 测试：{msg}")
            else:
                messages.error(request, f"Webhook 测试：{msg}")
            return redirect("accounts:profile")

    return render(
        request,
        "accounts/profile.html",
        {
            "user": request.user,
            "gitcode_enabled": _gitcode_enabled(),
            # 邮箱验证码剩余冷却秒数（前端初始倒计时，不刷新）
            "email_code_cooldown": _email_code_cooldown(request),
            "form": form,
            "webhook_form": webhook_form,
            "hooks": hooks,
        },
    )


@superuser_required
def settings(request):
    """系统设置（仅超级管理员）：GitCode 配置/邮件/全局 Webhook/管理员-服务器绑定。"""
    from django.utils import timezone

    syscfg = SystemConfig.get_singleton()
    email_cfg = EmailConfig.objects.first()
    hooks = WebhookConfig.objects.filter(owner__isnull=True)

    # 发码冷却：距上次发送 <60 秒则剩余秒数 >0（模板禁用按钮）
    sent_at = request.session.get("smtp_code_sent_at")
    cooldown_remaining = 0
    if sent_at:
        cooldown_remaining = max(0, 60 - int(timezone.now().timestamp() - sent_at))
    smtp_verified = bool(request.session.get("smtp_verified", False))

    if request.method == "POST":
        if "save_site_base_url" in request.POST:
            # 站点基准地址：GitCode 回调与 webhook 审批链接统一使用
            base_url = request.POST.get("site_base_url", "").strip().rstrip("/")
            syscfg.site_base_url = base_url
            syscfg.save()
            messages.success(request, "站点地址已保存。")
        elif "save_mail_webhook" in request.POST:
            # 邮件 Webhook（独立于通知 Webhook）：发送方式显式选择 + URL/Token
            send_via = request.POST.get("send_via", "").strip()
            new_url = request.POST.get("mail_webhook_url", "").strip()
            new_token = request.POST.get("mail_webhook_token", "").strip()
            if send_via in dict(EmailConfig.SEND_VIA_CHOICES):
                email_cfg = EmailConfig.objects.first() or EmailConfig()
                email_cfg.send_via = send_via
                if new_url:
                    email_cfg.mail_webhook_url = new_url
                if new_token:
                    email_cfg.mail_webhook_token = new_token
                email_cfg.save()
                messages.success(request, "邮件 Webhook 配置已保存。")
            else:
                messages.error(request, "发送方式无效。")
        elif "save_gitcode" in request.POST:
            # 唯一配置源：allauth SocialApp（由 django-allauth 插件管理）
            client_id = request.POST.get("gitcode_client_id", "").strip()
            # secret 留空表示不修改（不展示明文）
            new_secret = request.POST.get("gitcode_client_secret", "").strip()
            if client_id:
                app, _ = SocialApp.objects.get_or_create(provider="gitcode")
                app.name = "GitCode"
                app.client_id = client_id
                if new_secret:
                    app.secret = new_secret
                app.save()
                from django.contrib.sites.models import Site

                app.sites.add(Site.objects.get_current())
                messages.success(request, "GitCode 配置已保存。")
            else:
                messages.error(request, "请填写 GitCode Client ID。")
        elif "save_email" in request.POST:
            # 第一步：发送验证码（60 秒冷却），用表单配置（未入库）发信
            if cooldown_remaining > 0:
                messages.error(request, f"发送过于频繁，请 {cooldown_remaining} 秒后再试。")
                return redirect("accounts:settings")
            host = request.POST.get("host", "").strip()
            port = int(request.POST.get("port") or 465)
            username = request.POST.get("username", "").strip()
            new_pw = request.POST.get("password", "").strip()
            from_email = request.POST.get("from_email", "").strip()
            use_ssl = "use_ssl" in request.POST
            enabled = "enabled" in request.POST
            target = request.POST.get("verify_email", "").strip()
            if not host or not username:
                messages.error(request, "SMTP 服务器与用户名必填。")
            elif not target:
                messages.error(request, "请填写验证收件邮箱（用于验证 SMTP 配置）。")
            else:
                # 暂存待验证配置到 session（密码仅存于会话，验证通过后入库）
                request.session["pending_smtp"] = {
                    "host": host,
                    "port": port,
                    "username": username,
                    "password": new_pw,
                    "from_email": from_email,
                    "use_ssl": use_ssl,
                    "enabled": enabled,
                    "verify_email": target,
                }
                request.session["smtp_verified"] = False  # 重新发码使已验证失效

                # 用"待验证配置"发验证码邮件（写库前即可确认 SMTP 可用）
                def _send_with_pending(subject, body, to_list):
                    return send_email_with_config(
                        host,
                        port,
                        username,
                        new_pw,
                        from_email,
                        use_ssl,
                        subject,
                        body,
                        to_list,
                    )

                ok = send_smtp_code(target, _send_with_pending)
                if ok:
                    # 存时间戳（秒），session JSON 序列化不支持 datetime
                    request.session["smtp_code_sent_at"] = int(timezone.now().timestamp())
                    messages.success(request, f"验证码已发送至 {target}，请查收并点击“验证”。")
                else:
                    messages.error(request, "验证码发送失败：当前 SMTP 配置不可用（请检查服务器/端口/认证），未保存。")
                    request.session.pop("pending_smtp", None)
            return redirect("accounts:settings")
        elif "verify_smtp_code" in request.POST:
            # 第二步：校验验证码，通过后允许保存（模板据此启用保存按钮）
            pending = request.session.get("pending_smtp")
            if not pending:
                messages.error(request, "请先发送验证码。")
                return redirect("accounts:settings")
            target = pending.get("verify_email", "")
            ok, err = verify_code(
                target, request.POST.get("code", ""), EmailVerification.PURPOSE_SMTP_CONFIG, user=None
            )
            if ok:
                request.session["smtp_verified"] = True
                messages.success(request, "验证通过，配置已解锁，请点击“保存配置”。")
            else:
                messages.error(request, f"验证失败：{err}")
            return redirect("accounts:settings")
        elif "save_email_final" in request.POST:
            # 第三步：已验证通过才允许写入数据库
            pending = request.session.get("pending_smtp")
            if not pending:
                messages.error(request, "请先发送验证码并验证。")
                return redirect("accounts:settings")
            if not request.session.get("smtp_verified"):
                messages.error(request, "请先验证验证码，验证通过后才能保存。")
                return redirect("accounts:settings")
            email_cfg = EmailConfig.objects.first() or EmailConfig()
            email_cfg.host = pending["host"]
            email_cfg.port = pending["port"]
            email_cfg.username = pending["username"]
            if pending.get("password"):
                email_cfg.password = pending["password"]
            email_cfg.from_email = pending.get("from_email", "")
            email_cfg.use_ssl = pending.get("use_ssl", True)
            email_cfg.enabled = pending.get("enabled", False)
            # 发送方式与页面单选联动（SMTP 保存时固定为 smtp）
            email_cfg.send_via = EmailConfig.SEND_VIA_SMTP
            email_cfg.save()
            request.session.pop("pending_smtp", None)
            request.session.pop("smtp_verified", None)
            messages.success(request, "SMTP 配置已通过验证并保存。")
        elif "add_webhook" in request.POST:
            name = request.POST.get("name", "").strip()
            url = request.POST.get("url", "").strip()
            if name and url:
                # 全局 Webhook 单例：有则更新，无则新建（save 内自动清理旧的）
                hook = WebhookConfig.objects.filter(owner__isnull=True).first() or WebhookConfig(owner=None)
                hook.name = name
                hook.url = url
                if request.POST.get("secret", "").strip():
                    hook.secret = request.POST.get("secret", "").strip()
                hook.enabled = "enabled" in request.POST
                hook.save()
                messages.success(request, "全局 Webhook 已保存。")
            else:
                messages.error(request, "Webhook 名称与 URL 必填。")
        elif "test_webhook" in request.POST:
            # 测试全局 Webhook：直接对表单填写的 URL 推送测试事件（不保存）
            test_url = request.POST.get("url", "").strip()
            test_secret = request.POST.get("secret", "").strip()
            test_platform = request.POST.get("name", "").strip()
            ok, msg = send_webhook_to(test_url, test_secret, platform=test_platform)
            if ok:
                messages.success(request, f"Webhook 测试：{msg}")
            else:
                messages.error(request, f"Webhook 测试：{msg}")
        elif "del_webhook" in request.POST:
            hook = WebhookConfig.objects.filter(pk=request.POST.get("webhook_id")).first()
            if hook and hook.owner is None:
                hook.delete()
                messages.success(request, "Webhook 已删除。")
        elif "add_announcement" in request.POST:
            # 公告内容为 markdown 子集（# 标题 / **加粗** / *斜体* / {颜色} / [链接](url)），
            # 保存后由转换器分别渲染到首页公告栏（HTML）与服务器 motd（ANSI）
            content = request.POST.get("content", "").strip()
            if content:
                # 公告单例：有则更新，无则新建（save 内自动清理其他记录）
                ann = Announcement.objects.first() or Announcement()
                ann.content = content
                ann.enabled = "enabled" in request.POST
                ann.save()
                # 默认自动推送公告到全部服务器 motd（无需手动）
                ok_push, msg_push = push_notices()
                messages.success(request, "公告已保存并自动推送到服务器。" + (msg_push if ok_push else ""))
            else:
                messages.error(request, "公告内容必填。")
        return redirect("accounts:settings")

    return render(
        request,
        "accounts/settings.html",
        {
            "syscfg": syscfg,
            "gitcode_app": SocialApp.objects.filter(provider="gitcode").first(),
            "announcements": Announcement.objects.all(),
            # 公告编辑器初始内容：markdown 源码（模板内直接 .first.content 会抛 AttributeError，故视图预处理）
            "announcement_initial": (Announcement.objects.first().content if Announcement.objects.first() else ""),
            # 固定回调地址（与 provider 登录实际使用的 redirect_uri 一致，见 GitCodeOAuth2Adapter.get_callback_url）
            "gitcode_callback_url": f"{syscfg.get_site_base_url()}{reverse('gitcode_callback')}",
            "email_cfg": email_cfg,
            "hooks": hooks,
            # 邮件发送方式选项（邮件 Webhook tab 下拉）
            "email_send_via_choices": EmailConfig.SEND_VIA_CHOICES,
            "cooldown_remaining": cooldown_remaining,
            "smtp_verified": smtp_verified,
            # 顶部功能开关状态（切换即时生效）
            "switch_gitcode": syscfg.gitcode_enabled,
            "switch_email": bool(email_cfg and email_cfg.enabled),
            "switch_webhook": bool(hooks and hooks.first().enabled),
            # Webhook 平台选项（表单下拉）
            "webhook_platform_choices": WebhookConfig.PLATFORM_CHOICES,
        },
    )


@login_required
def gitcode_unbind(request):
    """解绑 GitCode：无密码用户（仅靠 GitCode 登录）须先设置本地密码，
    否则解绑后将无法登录系统。"""
    account = request.user.socialaccount_set.filter(provider="gitcode").first()
    if account is None:
        messages.info(request, "您尚未绑定 GitCode 账号。")
    elif not request.user.has_usable_password():
        messages.error(request, "请先设置本地密码，再解绑 GitCode（否则将无法登录）。")
    else:
        account.delete()
        messages.success(request, "GitCode 账号已解绑。")
    return redirect("accounts:profile")


@login_required
def set_password(request):
    """为当前用户设置本地密码（GitCode 无密码用户解绑前置条件）。"""
    if request.method == "POST":
        form = SetPasswordForm(request.user, request.POST)
        if form.is_valid():
            form.save()
            # 设置后更新会话认证（防止密码变更导致会话失效）
            update_session_auth_hash(request, form.user)
            messages.success(request, "本地密码已设置。")
            return redirect("accounts:profile")
    else:
        form = SetPasswordForm(request.user)
    return render(request, "accounts/set_password.html", {"form": form})
