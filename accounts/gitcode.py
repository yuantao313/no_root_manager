"""GitCode OAuth 2.0 客户端（授权码模式）。

端点（来自 https://docs.gitcode.com/docs/apis/oauth/）：
- 授权：GET https://gitcode.com/oauth/authorize
- 令牌：POST https://gitcode.com/oauth/token
- 用户信息：GET https://api.gitcode.com/api/v5/user（Bearer 认证）
"""

import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://gitcode.com/oauth/authorize"
TOKEN_URL = "https://gitcode.com/oauth/token"
USER_URL = "https://api.gitcode.com/api/v5/user"


class GitCodeOAuthError(Exception):
    """GitCode OAuth 流程错误。"""


def build_authorize_url(client_id, redirect_uri, state, scope="all_user"):
    """构造授权跳转 URL。"""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_token(client_id, client_secret, code):
    """用授权码换取 access_token。返回原始响应 dict。"""
    params = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.error("GitCode token 交换失败: %s", e)
        raise GitCodeOAuthError(f"令牌交换失败：{e}") from e


def get_user(access_token):
    """用 access_token 获取当前用户信息。"""
    req = urllib.request.Request(
        USER_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.error("GitCode 用户信息获取失败: %s", e)
        raise GitCodeOAuthError(f"用户信息获取失败：{e}") from e
