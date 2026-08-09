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
from django.contrib.auth.views import LoginView, PasswordResetView
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy

from config.decorators import superuser_required
from notifications.forms import WebhookForm
from notifications.models import EmailConfig, WebhookConfig
from notifications.services import send_email, send_email_with_config
from servers.models import Server, ServerAdminBinding

from .email_verify import send_smtp_code, send_user_email_code, verify_code
from .models import EmailVerification, SystemConfig
from .username_gen import generate_username_groups


class ProfileForm(forms.Form):
    """个人资料编辑：姓名（一体化，映射 first_name）+ 邮箱 + 验证码。"""

    name = forms.CharField(label="姓名", max_length=100, required=False)
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
            self.fields["email"].initial = instance.email

    def save(self, commit=True):
        user = self.instance
        user.first_name = self.cleaned_data["name"].strip()
        user.email = self.cleaned_data["email"].strip()
        if commit:
            user.save()
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


def username_suggestions(request):
    """用户名建议接口：根据姓名返回候选用户名（含复姓/单姓分组），无需登录。"""
    name = request.GET.get("name", "").strip()
    data = generate_username_groups(name)
    return JsonResponse(data, json_dumps_params={"ensure_ascii": False})


def register(request):
    """用户注册：所有用户平等注册为普通用户，注册后自动登录。"""
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # 多认证后端（axes + ModelBackend）必须显式指定 backend
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, f"注册成功，欢迎 {user.username}。")
            return redirect("applications:my")
    else:
        form = UserCreationForm()
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
            form = SocialSignupForm(sociallogin=sociallogin)
        else:
            # 创建新账号：默认注册流程（含注册要素）
            form = SocialSignupForm(request.POST, sociallogin=sociallogin)
            if form.is_valid():
                return socialaccount_flows.signup.signup_by_form(request, sociallogin, form)
    else:
        form = SocialSignupForm(sociallogin=sociallogin)

    return render(
        request,
        "socialaccount/signup.html",
        {"form": form, "account": sociallogin.account},
    )


def _gitcode_enabled():
    """GitCode OAuth 是否已配置：直接检测 allauth SocialApp（唯一配置源）。"""
    return SocialApp.objects.filter(provider="gitcode").exists()


class GitCodeLoginView(LoginView):
    """登录页：传递 GitCode OAuth 是否已配置（控制入口显示，未配置不崩溃）。"""

    template_name = "accounts/login.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["gitcode_enabled"] = _gitcode_enabled()
        return context


@login_required
def profile(request):
    """个人中心：资料行内编辑 + 邮箱验证码确认 + 内嵌 Webhook（仅管理员）。"""
    hooks = WebhookConfig.objects.filter(owner=request.user)
    form = ProfileForm(instance=request.user)
    webhook_form = WebhookForm()

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
                hook = webhook_form.save(commit=False)
                hook.owner = request.user
                hook.save()
                messages.success(request, f"Webhook「{hook.name}」已添加。")
                return redirect("accounts:profile")

    return render(
        request,
        "accounts/profile.html",
        {
            "user": request.user,
            "gitcode_enabled": _gitcode_enabled(),
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
    bindings = ServerAdminBinding.objects.select_related("server", "admin").all()
    staff_users = User.objects.filter(is_staff=True, is_superuser=False).order_by("username")
    servers = Server.objects.all().order_by("name")

    # 发码冷却：距上次发送 <60 秒则剩余秒数 >0（模板禁用按钮）
    sent_at = request.session.get("smtp_code_sent_at")
    cooldown_remaining = 0
    if sent_at:
        cooldown_remaining = max(0, 60 - int(timezone.now().timestamp() - sent_at))
    smtp_verified = bool(request.session.get("smtp_verified", False))

    if request.method == "POST":
        if "save_gitcode" in request.POST:
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
            use_tls = "use_tls" in request.POST
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
                    "use_tls": use_tls,
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
                        use_tls,
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
            email_cfg.use_tls = pending.get("use_tls", False)
            email_cfg.enabled = pending.get("enabled", False)
            email_cfg.save()
            request.session.pop("pending_smtp", None)
            request.session.pop("smtp_verified", None)
            messages.success(request, "SMTP 配置已通过验证并保存。")
        elif "add_webhook" in request.POST:
            name = request.POST.get("name", "").strip()
            url = request.POST.get("url", "").strip()
            if name and url:
                WebhookConfig.objects.create(
                    name=name,
                    url=url,
                    secret=request.POST.get("secret", "").strip(),
                    enabled="enabled" in request.POST,
                    owner=None,
                )
                messages.success(request, "全局 Webhook 已添加。")
            else:
                messages.error(request, "Webhook 名称与 URL 必填。")
        elif "del_webhook" in request.POST:
            hook = WebhookConfig.objects.filter(pk=request.POST.get("webhook_id")).first()
            if hook and hook.owner is None:
                hook.delete()
                messages.success(request, "Webhook 已删除。")
        elif "add_binding" in request.POST:
            server_id = request.POST.get("server_id")
            admin_id = request.POST.get("admin_id")
            if server_id and admin_id:
                ServerAdminBinding.objects.get_or_create(server_id=server_id, admin_id=admin_id)
                messages.success(request, "绑定关系已添加。")
        elif "del_binding" in request.POST:
            binding = ServerAdminBinding.objects.filter(pk=request.POST.get("binding_id")).first()
            if binding:
                binding.delete()
                messages.success(request, "绑定关系已解除。")
        return redirect("accounts:settings")

    return render(
        request,
        "accounts/settings.html",
        {
            "syscfg": syscfg,
            "gitcode_app": SocialApp.objects.filter(provider="gitcode").first(),
            "gitcode_callback_url": request.build_absolute_uri(reverse("gitcode_callback")),
            "email_cfg": email_cfg,
            "hooks": hooks,
            "bindings": bindings,
            "staff_users": staff_users,
            "servers": servers,
            "cooldown_remaining": cooldown_remaining,
            "smtp_verified": smtp_verified,
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
