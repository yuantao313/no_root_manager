import secrets

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from notifications.forms import WebhookForm
from notifications.models import WebhookConfig

from .gitcode import (
    GitCodeOAuthError,
    build_authorize_url,
    exchange_token,
    get_user,
)
from .models import GitCodeBinding
from .username_gen import generate_username_groups


class ProfileForm(forms.Form):
    """个人资料编辑：姓名（一体化，映射 first_name）+ 邮箱。"""

    name = forms.CharField(label="姓名", max_length=100, required=False)
    email = forms.EmailField(label="邮箱", required=False)

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
            login(request, user)
            messages.success(request, f"注册成功，欢迎 {user.username}。")
            return redirect("applications:my")
    else:
        form = UserCreationForm()
    return render(request, "accounts/register.html", {"form": form})


@login_required
def profile(request):
    """个人中心：资料行内编辑（每个字段右侧编辑按钮）+ 内嵌 Webhook（仅管理员）。"""
    hooks = WebhookConfig.objects.filter(owner=request.user)
    form = ProfileForm(instance=request.user)
    webhook_form = WebhookForm()

    if request.method == "POST":
        # 区分提交来源：保存个人资料 / 添加 Webhook
        if "save_profile" in request.POST:
            form = ProfileForm(request.POST, instance=request.user)
            if form.is_valid():
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
            "form": form,
            "webhook_form": webhook_form,
            "hooks": hooks,
        },
    )


def gitcode_login(request):
    """GitCode OAuth 登录：跳转到授权页（state 防 CSRF）。"""
    if not settings.GITCODE_CLIENT_ID:
        messages.error(request, "GitCode 登录未配置（缺少 GITCODE_CLIENT_ID）。")
        return redirect("accounts:login")
    state = secrets.token_urlsafe(16)
    request.session["gitcode_oauth_state"] = state
    redirect_uri = request.build_absolute_uri(reverse("accounts:gitcode_callback"))
    url = build_authorize_url(settings.GITCODE_CLIENT_ID, redirect_uri, state)
    return redirect(url)


@login_required
def gitcode_bind(request):
    """已注册用户绑定 GitCode：跳转授权页（与登录共用同一回调地址，
    通过 session state 键区分绑定模式，避免 GitCode 需要注册多个回调）。"""
    if not settings.GITCODE_CLIENT_ID:
        messages.error(request, "GitCode 登录未配置（缺少 GITCODE_CLIENT_ID）。")
        return redirect("accounts:profile")
    if hasattr(request.user, "gitcode_binding"):
        messages.info(request, "您已绑定 GitCode 账号，无需重复绑定。")
        return redirect("accounts:profile")
    state = secrets.token_urlsafe(16)
    request.session["gitcode_bind_state"] = state
    redirect_uri = request.build_absolute_uri(reverse("accounts:gitcode_callback"))
    url = build_authorize_url(settings.GITCODE_CLIENT_ID, redirect_uri, state)
    return redirect(url)


def _gitcode_bind_done(request, code):
    """绑定模式的收尾：校验 code、换 token、取用户信息并建立绑定。"""
    if not request.user.is_authenticated:
        messages.error(request, "绑定 GitCode 需要先登录。")
        return redirect("accounts:login")
    if not code:
        messages.error(request, "GitCode 未返回授权码。")
        return redirect("accounts:profile")

    try:
        token_data = exchange_token(
            settings.GITCODE_CLIENT_ID,
            settings.GITCODE_CLIENT_SECRET,
            code,
        )
        access_token = token_data.get("access_token", "")
        if not access_token:
            raise GitCodeOAuthError("未获取到 access_token")
        user_data = get_user(access_token)
    except GitCodeOAuthError as e:
        messages.error(request, f"GitCode 绑定失败：{e}")
        return redirect("accounts:profile")

    user_id = user_data.get("id")
    if not user_id:
        messages.error(request, "GitCode 未返回用户 id。")
        return redirect("accounts:profile")

    # 该 GitCode 账号已被其他用户绑定则拒绝
    if GitCodeBinding.objects.filter(gitcode_id=user_id).exists():
        messages.error(request, "该 GitCode 账号已绑定其他用户，无法重复绑定。")
        return redirect("accounts:profile")

    GitCodeBinding.objects.create(
        user=request.user,
        gitcode_id=user_id,
        gitcode_username=(user_data.get("login") or "")[:100],
    )
    messages.success(request, "GitCode 账号绑定成功。")
    return redirect("accounts:profile")


def gitcode_callback(request):
    """GitCode OAuth 回调（登录与绑定共用）。

    通过 state 匹配 session 中不同的键来区分模式：
    - gitcode_bind_state：绑定（当前用户已登录）
    - gitcode_oauth_state：登录
    """
    state = request.GET.get("state", "")
    code = request.GET.get("code", "")

    # 先判定模式：绑定优先（其 state 只存于已登录会话）
    if state and state == request.session.pop("gitcode_bind_state", ""):
        return _gitcode_bind_done(request, code)
    if state != request.session.pop("gitcode_oauth_state", ""):
        messages.error(request, "GitCode 登录校验失败（state 不匹配），请重试。")
        return redirect("accounts:login")

    if not code:
        messages.error(request, "GitCode 未返回授权码。")
        return redirect("accounts:login")

    try:
        token_data = exchange_token(
            settings.GITCODE_CLIENT_ID,
            settings.GITCODE_CLIENT_SECRET,
            code,
        )
        access_token = token_data.get("access_token", "")
        if not access_token:
            raise GitCodeOAuthError("未获取到 access_token")
        user_data = get_user(access_token)
    except GitCodeOAuthError as e:
        messages.error(request, f"GitCode 登录失败：{e}")
        return redirect("accounts:login")

    # 用户 id 映射：优先用绑定表查找（已注册用户绑定的 GitCode 账号），
    # 未绑定则创建 gc<id> 用户并记录绑定；不使用 login（可改，防映射失效）
    user_id = user_data.get("id")
    if not user_id:
        messages.error(request, "GitCode 未返回用户 id。")
        return redirect("accounts:login")

    binding = GitCodeBinding.objects.filter(gitcode_id=user_id).first()
    if binding:
        # 已绑定：直接登录绑定用户（已注册用户绑定的账号，或历史 gc<id> 用户）
        user = binding.user
        login(request, user)
        messages.success(request, f"欢迎回来，{user.first_name or user.username}。")
        return redirect("applications:my")

    # 未绑定：创建 gc<id> 用户并记录绑定映射
    username = f"gc{user_id}"
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "email": (user_data.get("email") or "")[:100],
        },
    )
    if created:
        # 无密码：只能通过 GitCode OAuth 登录
        user.set_unusable_password()
        user.save()
        GitCodeBinding.objects.create(
            user=user,
            gitcode_id=user_id,
            gitcode_username=(user_data.get("login") or "")[:100],
        )
    login(request, user)
    if created:
        # 首次登录：引导设置个人姓名（申请与通知依赖，未设置前不能提交申请）
        messages.success(request, "GitCode 登录成功。请先设置个人姓名，再提交申请。")
        return redirect("accounts:profile")
    messages.success(request, f"欢迎回来，{user.first_name or user.username}。")
    return redirect("applications:my")
