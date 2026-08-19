import pytest
from django.contrib.auth import get_user_model
from django.template import Context, Template

pytestmark = pytest.mark.django_db


def render_user(user):
    return Template("{% load user_display %}{{ user|user_label }}").render(Context({"user": user}))


def test_user_label_shows_name_and_username():
    user = get_user_model().objects.create_user(username="zhangsanfeng", first_name="张三丰")

    assert render_user(user) == "张三丰（zhangsanfeng）"


def test_user_label_falls_back_to_username_without_name():
    user = get_user_model().objects.create_user(username="zhangsanfeng")

    assert render_user(user) == "zhangsanfeng"


def test_user_label_can_use_historical_name_snapshot():
    user = get_user_model().objects.create_user(username="zhangsanfeng")
    rendered = Template("{% load user_display %}{{ user|user_label:'张三丰' }}").render(Context({"user": user}))

    assert rendered == "张三丰（zhangsanfeng）"
