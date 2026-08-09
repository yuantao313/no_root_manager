/* NRM 全局前端脚本（原各模板内联 <script> 迁移至此）。
 *
 * 约定：模板中需要传入的 URL/初始值一律通过 data-* 属性暴露，
 * 本文件读取元素属性初始化，保证静态化、无模板标签依赖。
 */
(function () {
    "use strict";

    function getCookie(name) {
        var m = document.cookie.match(new RegExp("(^|; )" + name + "=([^;]*)"));
        return m ? decodeURIComponent(m[2]) : "";
    }

    /* ===== 个人中心：行内编辑 ===== */
    function editField(field) {
        document.querySelectorAll(".field-input").forEach(function (i) { i.style.display = "none"; });
        document.querySelectorAll(".field-text").forEach(function (s) { s.style.display = "inline"; });
        document.querySelectorAll(".field-edit").forEach(function (a) { a.style.display = "inline"; });
        var input = document.getElementById("field-" + field);
        document.querySelector('.field-text[data-field="' + field + '"]').style.display = "none";
        document.querySelector('.field-edit[data-field="' + field + '"]').style.display = "none";
        input.style.display = "inline";
        var firstInput = input.querySelector("input");
        if (firstInput) { firstInput.focus(); }
    }
    function cancelEdit(field) {
        var input = document.getElementById("field-" + field);
        input.style.display = "none";
        document.querySelector('.field-text[data-field="' + field + '"]').style.display = "inline";
        document.querySelector('.field-edit[data-field="' + field + '"]').style.display = "inline";
    }
    document.querySelectorAll(".field-edit").forEach(function (a) {
        a.addEventListener("click", function () { editField(this.dataset.field); });
    });

    /* ===== 邮箱验证码 AJAX（发送不刷新、60 秒倒计时、错误前端提示）===== */
    function showEmailTip(msg, ok) {
        var tip = document.getElementById("email-code-tip");
        if (!tip) return;
        tip.textContent = msg;
        tip.className = ok ? "text-success" : "text-danger";
        tip.style.fontSize = "13px";
        tip.style.display = "inline";
    }
    function startCooldown(seconds) {
        var btn = document.getElementById("btn-send-code");
        if (!btn) return;
        btn.disabled = true;
        var left = seconds;
        btn.textContent = "已发送（" + left + " 秒后重试）";
        window._emailCodeTimer && clearInterval(window._emailCodeTimer);
        window._emailCodeTimer = setInterval(function () {
            left--;
            if (left <= 0) {
                clearInterval(window._emailCodeTimer);
                btn.disabled = false;
                btn.textContent = "发送验证码";
            } else {
                btn.textContent = "已发送（" + left + " 秒后重试）";
            }
        }, 1000);
    }

    var sendBtn = document.getElementById("btn-send-code");
    if (sendBtn) {
        var sendUrl = sendBtn.dataset.sendUrl;
        sendBtn.addEventListener("click", function () {
            var email = document.getElementById("field-email-input").value;
            fetch(sendUrl, {
                method: "POST",
                headers: { "X-CSRFToken": getCookie("csrftoken") },
                body: new URLSearchParams({ email: email }),
            }).then(function (r) { return r.json(); }).then(function (data) {
                if (data.ok) {
                    showEmailTip("验证码已发送，请查收（60 秒后可重发）。", true);
                    startCooldown(data.cooldown || 60);
                } else if (data.cooldown > 0) {
                    startCooldown(data.cooldown);
                    showEmailTip(data.error, false);
                } else {
                    showEmailTip(data.error, false);
                }
            }).catch(function () { showEmailTip("网络异常，发送失败。", false); });
        });
    }

    var profileForm = document.getElementById("profile-form");
    if (profileForm) {
        var verifyUrl = profileForm.dataset.verifyUrl;
        var currentEmail = profileForm.dataset.currentEmail || "";
        profileForm.addEventListener("submit", function (e) {
            var emailField = document.getElementById("field-email");
            if (!emailField || emailField.style.display === "none") return; // 非邮箱编辑
            var emailInput = document.getElementById("field-email-input");
            if (emailInput && emailInput.value.trim() !== currentEmail) {
                e.preventDefault();
                var code = document.getElementById("field-code-input").value;
                if (!code) { showEmailTip("请先填写邮箱验证码。", false); return; }
                fetch(verifyUrl, {
                    method: "POST",
                    headers: { "X-CSRFToken": getCookie("csrftoken") },
                    body: new URLSearchParams({ email: emailInput.value, code: code }),
                }).then(function (r) { return r.json(); }).then(function (data) {
                    if (data.ok) {
                        profileForm.submit();
                    } else {
                        showEmailTip("验证码错误：" + data.error, false);
                    }
                }).catch(function () { showEmailTip("网络异常，校验失败。", false); });
            }
        });
    }
    // 页面加载：已有冷却则前端直接显示倒计时（不刷新恢复）
    var cooldownAttr = document.getElementById("email-code-tip") && document.getElementById("email-code-tip").dataset.cooldown;
    if (cooldownAttr && parseInt(cooldownAttr, 10) > 0) {
        startCooldown(parseInt(cooldownAttr, 10));
    }

    /* ===== 申请页：附加分组联动 ===== */
    var serverSelect = document.querySelector('select[name="target_server"]');
    var groupsUl = document.getElementById("id_applied_groups");
    if (serverSelect && groupsUl) {
        var groupsApiUrl = serverSelect.dataset.groupsUrl || "/servers/api/groups/";
        var selected = Array.prototype.slice.call(groupsUl.querySelectorAll("input:checked")).map(function (i) { return i.value; });
        function renderGroups(groups) {
            var html = groups.map(function (g) {
                var checked = selected.indexOf(g) !== -1 ? " checked" : "";
                return '<li><label style="font-weight:normal;font-size:14px;margin:4px 0;">' +
                    '<input type="checkbox" name="applied_groups" value="' + g + '"' + checked + "> " + g + "</label></li>";
            }).join("");
            groupsUl.innerHTML = html || '<li class="text-muted">该服务器无附加分组</li>';
        }
        function loadGroups(serverId) {
            if (!serverId) { groupsUl.innerHTML = '<li class="text-muted">请先选择目标服务器</li>'; return; }
            fetch(groupsApiUrl + serverId + "/")
                .then(function (r) { return r.json(); })
                .then(function (data) { renderGroups(data.extra_groups || []); })
                .catch(function () { groupsUl.innerHTML = '<li class="text-muted">分组加载失败</li>'; });
        }
        serverSelect.addEventListener("change", function () {
            selected = [];
            loadGroups(this.value);
        });
        loadGroups(serverSelect.value);
    }

    /* ===== 凭据表单：私钥拖拽上传 ===== */
    var dropzone = document.getElementById("dropzone");
    if (dropzone) {
        var fileInput = document.getElementById("keyfile");
        var keyArea = document.querySelector('textarea[name="private_key"]');
        dropzone.addEventListener("click", function () { fileInput.click(); });
        fileInput.addEventListener("change", function () {
            if (fileInput.files.length) loadFile(fileInput.files[0]);
        });
        dropzone.addEventListener("dragover", function (e) {
            e.preventDefault();
            dropzone.style.borderColor = "#337ab7";
        });
        dropzone.addEventListener("dragleave", function () {
            dropzone.style.borderColor = "#bbb";
        });
        dropzone.addEventListener("drop", function (e) {
            e.preventDefault();
            dropzone.style.borderColor = "#bbb";
            if (e.dataTransfer.files.length) loadFile(e.dataTransfer.files[0]);
        });
        function loadFile(file) {
            var reader = new FileReader();
            reader.onload = function () {
                keyArea.value = reader.result;
                dropzone.textContent = "已载入文件：" + file.name + "（可继续拖入替换）";
                dropzone.style.borderColor = "#5cb85c";
            };
            reader.readAsText(file);
        }
    }
})();
