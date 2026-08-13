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
                        // form.submit() 原生提交不携带按钮 name（save_profile），
                        // 需手动追加 hidden 标记，否则后端不进入保存分支导致邮箱不变
                        var hidden = document.createElement("input");
                        hidden.type = "hidden";
                        hidden.name = "save_profile";
                        hidden.value = "1";
                        profileForm.appendChild(hidden);
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

    /* ===== 申请页：类型条件显示 + NPU 卡组按钮组 + 转移用户下拉 ===== */
    var serverSelect = document.querySelector('select[name="target_server"]');
    var applyTypeSelect = document.getElementById("id_apply_type");
    var groupsUl = document.getElementById("id_applied_groups");
    var npuField = document.getElementById("npu-field");
    var userGroupsField = document.getElementById("user-groups-field");
    var transferField = document.getElementById("transfer-field");
    var adminField = document.getElementById("admin-field");
    var transferSelect = document.getElementById("id_transfer_username");

    if (serverSelect) {
        var groupsApiUrl = serverSelect.dataset.groupsUrl || "/servers/api/groups/";
        // URL 模板带占位 pk=0（如 /servers/api/groups/0/），替换成真实 serverId
        function apiUrl(tpl, serverId) {
            return tpl.replace("/0/", "/" + serverId + "/");
        }

        // 按申请类型切换显示：
        //   create  → NPU 卡组；transfer → 机器用户下拉；group → 用户组；admin → 提示文案（不选 NPU）
        function toggleTypeFields() {
            var t = applyTypeSelect && applyTypeSelect.value;
            if (npuField) npuField.style.display = t === "create" ? "" : "none";
            if (userGroupsField) userGroupsField.style.display = t === "group" ? "" : "none";
            if (transferField) transferField.style.display = t === "transfer" ? "" : "none";
            if (adminField) adminField.style.display = t === "admin" ? "" : "none";
            // 切回 create 时按当前服务器重新加载卡组按钮
            if (t === "create" && groupsUl) loadGroups(serverSelect.value);
            // transfer 时加载机器用户下拉
            if (t === "transfer") loadTransferUsers(serverSelect.value);
        }

        function loadTransferUsers(serverId) {
            if (!transferSelect) return;
            transferSelect.innerHTML = '<option value="">请先选择目标服务器</option>';
            // select2 重建：先销毁再初始化，确保新选项生效
            if (window.jQuery && window.jQuery.fn && window.jQuery.fn.select2 && transferSelect.dataset.select2init) {
                window.jQuery(transferSelect).select2("destroy");
                delete transferSelect.dataset.select2init;
            }
            if (!serverId) return;
            fetch(apiUrl(groupsApiUrl, serverId))
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    var users = data.users || [];
                    if (!users.length) {
                        transferSelect.innerHTML = '<option value="">该服务器无可接管用户</option>';
                        return;
                    }
                    var html = '<option value="">请选择要接管的账号</option>';
                    users.forEach(function (u) {
                        html += '<option value="' + u + '">' + u + "</option>";
                    });
                    transferSelect.innerHTML = html;
                })
                .catch(function () {
                    transferSelect.innerHTML = '<option value="">读取机器用户失败</option>';
                })
                .finally(function () {
                    // 选项就绪后初始化 select2（可搜索）
                    if (window.jQuery && window.jQuery.fn && window.jQuery.fn.select2) {
                        window.jQuery(transferSelect).select2({ width: "100%" });
                        transferSelect.dataset.select2init = "1";
                    }
                });
        }

        var selected = [];
        var npuCardCount = 0; // 当前服务器可选 NPU 卡组数（过滤公共组 npu 后），用于按需选择校验
        var currentGroups = []; // 最近一次 groups_api 的卡组（NPU 按钮重渲染用）
        var currentIsNpu = false;

        // 渲染设备信息条（CPU/内存/硬盘），显示在目标服务器下方
        function renderDeviceInfo(device) {
            var strip = document.getElementById("device-info-strip");
            if (!strip) return;
            device = device || {};
            var parts = [];
            if (device.cpu) parts.push("CPU：" + device.cpu);
            if (device.memory) parts.push("内存：" + device.memory);
            if (device.disk) parts.push("硬盘：" + device.disk);
            if (parts.length) {
                strip.innerHTML = '<span class="text-muted" style="font-size:12px;">' + parts.join("　|　") + "</span>";
                strip.style.display = "";
            } else {
                // 查询失败或空：显示提示而非静默隐藏（避免看起来像"没加载"）
                var msg = (device && device.msg) ? device.msg : "设备信息获取失败";
                strip.innerHTML = '<span class="text-muted" style="font-size:12px;">' + msg + "</span>";
                strip.style.display = "";
            }
        }

        // 设备信息加载中提示：fetch 前显示，renderDeviceInfo 会覆盖
        function showDeviceLoading() {
            var strip = document.getElementById("device-info-strip");
            if (!strip) return;
            strip.innerHTML = '<span class="text-muted" style="font-size:12px;">正在获取设备信息…</span>';
            strip.style.display = "";
        }

        // 渲染卡组按钮：一行 4 个，选中变色（btn-primary），不渲染公共组 npu。
        // NPU 卡按钮文案：<设备号> <型号> (<内存G>G)，型号/内存来自 device.npu（device_api）。
        function renderGroups(groups, isNpu, device) {
            var wrap = document.getElementById("npu-field");
            if (!isNpu) { if (wrap) wrap.style.display = "none"; return; }
            if (wrap) wrap.style.display = "";
            // 公共组 npu 由后端授权时自动附带，前端不显示
            var cards = groups.filter(function (g) { return g !== "npu"; });
            npuCardCount = cards.length;
            // 设备信息：NPU 卡按设备号匹配（npuN → 型号/内存）
            var npuInfo = {};
            (device && device.npu || []).forEach(function (c) {
                npuInfo["npu" + c.index] = c;
            });
            if (!cards.length) {
                groupsUl.innerHTML = '<p class="text-muted" style="margin:4px 0;">该服务器无可用 NPU 卡组</p>';
                return;
            }
            var html = '<div class="row">';
            cards.forEach(function (g, i) {
                var active = selected.indexOf(g) !== -1;
                var info = npuInfo[g];
                // 按钮文案：<设备号> <型号> <内存G>G（如 0 Ascend910B3 60G）
                var label = info ? info.index + " " + info.soc_name + " " + info.mem_g + "G" : g;
                html += '<div class="col-xs-3" style="padding:2px;">' +
                    '<button type="button" class="btn btn-block npu-card-btn' + (active ? " btn-primary" : " btn-default") +
                    '" data-group="' + g + '" title="卡组 ' + g + '" style="font-size:13px;padding:6px 0;">' + label + "</button></div>";
            });
            html += "</div>";
            groupsUl.innerHTML = html;
        }

        // 按钮点击：切换选中态并变色
        groupsUl.addEventListener("click", function (e) {
            var btn = e.target.closest ? e.target.closest(".npu-card-btn") : null;
            if (!btn) return;
            var g = btn.dataset.group;
            var idx = selected.indexOf(g);
            if (idx >= 0) {
                selected.splice(idx, 1);
                btn.classList.remove("btn-primary");
                btn.classList.add("btn-default");
            } else {
                selected.push(g);
                btn.classList.remove("btn-default");
                btn.classList.add("btn-primary");
            }
        });

        // 设备信息条（CPU/内存/硬盘）由 groups_api 的 device 字段一次返回渲染，
        // 不再单独请求 device_api（详情页仍用 device_api 异步填充）。

        function loadGroups(serverId) {
            if (!serverId) {
                var wrap = document.getElementById("npu-field");
                if (wrap) wrap.style.display = "none";
                renderDeviceInfo(null);
                return;
            }
            // 加载中提示：fetch 期间先占位，拿到结果后由 renderDeviceInfo 覆盖
            showDeviceLoading();
            fetch(apiUrl(groupsApiUrl, serverId))
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    var device = data.device || {};
                    currentGroups = data.extra_groups || [];
                    currentIsNpu = !!data.is_npu;
                    // 设备信息条（CPU/内存/硬盘）：所有服务器都显示
                    renderDeviceInfo(device);
                    // NPU 卡按钮：设备号+型号+内存G 一次渲染
                    renderGroups(currentGroups, currentIsNpu, device);
                    // 非 NPU 服务器：隐藏整个卡组区（含 label）
                    if (!data.is_npu) {
                        var wrap = document.getElementById("npu-field");
                        if (wrap) wrap.style.display = "none";
                    }
                })
                .catch(function () {
                    var wrap = document.getElementById("npu-field");
                    if (wrap) wrap.style.display = "none";
                    renderDeviceInfo(null);
                });
        }
        serverSelect.addEventListener("change", function () {
            selected = [];
            loadGroups(this.value);
            if (applyTypeSelect && applyTypeSelect.value === "transfer") loadTransferUsers(this.value);
        });
        if (applyTypeSelect) applyTypeSelect.addEventListener("change", toggleTypeFields);
        toggleTypeFields();
        loadGroups(serverSelect.value);

        // 服务器下拉 select2 初始化（可搜索）。
        // 注意：select2 隐藏原生 select 后不再触发原生 change 事件，
        // 必须额外监听 select2:select 才能联动加载 NPU 卡组/机器用户。
        if (window.jQuery && window.jQuery.fn && window.jQuery.fn.select2) {
            window.jQuery(serverSelect).select2({ width: "100%" });
            window.jQuery(serverSelect).on("select2:select", function () {
                var val = window.jQuery(serverSelect).val();
                selected = [];
                loadGroups(val);
                if (applyTypeSelect && applyTypeSelect.value === "transfer") loadTransferUsers(val);
            });
        }

        // 提交前：将选中的卡组写入隐藏 input（name=applied_groups）
        // npu 公共组由后端授权时自动附带，前端不写入（不暴露"公共组"概念）
        var appForm = document.getElementById("application-form");
        if (appForm) {
            appForm.addEventListener("submit", function (e) {
                // NPU 按需选择校验：NPU 服务器未选或全选卡组时提示确认
                if (npuCardCount > 0) {
                    var npuFieldEl = document.getElementById("npu-field");
                    var isNpuVisible = npuFieldEl && npuFieldEl.style.display !== "none";
                    if (isNpuVisible) {
                        if (selected.length === 0) {
                            e.preventDefault();
                            window.alert("请按需选择 NPU 卡组后再提交。");
                            return;
                        }
                        if (selected.length >= npuCardCount) {
                            if (!window.confirm("您选择了全部 NPU 卡组，请按需选择。确定继续提交吗？")) {
                                e.preventDefault();
                                return;
                            }
                        }
                    }
                }
                // 清理可能残留的旧隐藏 input
                var olds = appForm.querySelectorAll('input[name="applied_groups"]');
                for (var i = 0; i < olds.length; i++) { olds[i].parentNode.removeChild(olds[i]); }
                if (!selected.length) return;
                selected.forEach(function (v) {
                    var inp = document.createElement("input");
                    inp.type = "hidden";
                    inp.name = "applied_groups";
                    inp.value = v;
                    appForm.appendChild(inp);
                });
            });
        }
    }

    /* ===== 服务器表单：勾选 NPU 服务器时显示卡组输入 ===== */
    var npuCheckbox = document.querySelector('input[name="is_npu"]');
    var npuGroupsField = document.getElementById("npu-groups-field");
    if (npuCheckbox && npuGroupsField) {
        function toggleNpuGroupsField() {
            npuGroupsField.style.display = npuCheckbox.checked ? "" : "none";
        }
        npuCheckbox.addEventListener("change", toggleNpuGroupsField);
        toggleNpuGroupsField();
    }

    /* ===== 邮件通知：发送方式单选（SMTP / 邮件 Webhook）显隐对应设置区 =====
       隐藏区必须禁用（fieldset.disabled）：否则隐藏的 required 控件（如 verify_email）
       仍会拦截表单提交（"An invalid form control ... is not focusable"），导致保存不了。 */
    var sendViaRadios = document.querySelectorAll('input[name="send_via"]');
    var sendViaPanels = document.querySelectorAll("[data-sendvia-panel]");
    if (sendViaRadios.length) {
        function applySendViaPanels() {
            var checked = document.querySelector('input[name="send_via"]:checked');
            var mode = checked ? checked.value : "smtp";
            sendViaPanels.forEach(function (p) {
                var active = p.dataset.sendviaPanel === mode;
                p.style.display = active ? "" : "none";
                // 隐藏区禁用：disabled 控件不参与 HTML5 required 校验，也不提交
                if (p.tagName === "FIELDSET") { p.disabled = !active; }
            });
        }
        sendViaRadios.forEach(function (r) {
            r.addEventListener("change", applySendViaPanels);
        });
        applySendViaPanels();
    }

    /* ===== 系统设置：各 tab 功能开关（切换即时保存 + 禁用/恢复下方配置） ===== */
    document.querySelectorAll(".feature-toggle").forEach(function (cb) {
        var fieldsetId = "fieldset-" + cb.dataset.switch;
        var fieldset = document.getElementById(fieldsetId);

        // 切换时：取消勾选 → 下方全部配置项禁用（变灰）；勾选 → 恢复可编辑
        function applyDisabled() {
            if (!fieldset) return;
            fieldset.disabled = !cb.checked;
        }
        applyDisabled(); // 页面加载即按开关状态生效（模板已渲染初始状态，这里兜底）

        cb.addEventListener("change", function () {
            applyDisabled(); // 先即时禁用/恢复，不等接口返回
            var url = cb.dataset.toggleUrl;
            var switchName = cb.dataset.switch;
            if (!url) return;
            fetch(url, {
                method: "POST",
                headers: {
                    "X-CSRFToken": getCookie("csrftoken"),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                body: "switch=" + encodeURIComponent(switchName) + "&enabled=" + (cb.checked ? "1" : "0"),
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (!data.ok) {
                        cb.checked = !cb.checked; // 失败回滚
                        applyDisabled();
                        window.alert("开关切换失败：" + (data.error || "未知错误"));
                    }
                })
                .catch(function () {
                    cb.checked = !cb.checked; // 网络异常回滚
                    applyDisabled();
                    window.alert("网络异常，开关切换失败。");
                });
        });
    });

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

    /* ===== 表单提交防重复 =====
       不能用 disabled 禁用按钮：disabled 的 submit 按钮不会随表单提交 name，
       会导致后端按按钮 name 分发（save_mail_webhook / save_email_final 等）失效、保存不了。
       改用 dataset 标志拦截重复提交（页面导航后自然重置）。 */
    document.querySelectorAll("form").forEach(function (f) {
        f.addEventListener("submit", function (e) {
            if (f.dataset.submitting === "1") {
                e.preventDefault();
                return;
            }
            f.dataset.submitting = "1";
        });
    });

    /* ===== 危险操作统一确认（复用 Bootstrap Modal，data-confirm 属性触发） ===== */
    var confirmModal = document.getElementById("confirm-modal");
    var pendingConfirmForm = null;
    if (confirmModal && window.jQuery) {
        document.addEventListener("submit", function (e) {
            var form = e.target;
            var msg = form.getAttribute("data-confirm");
            if (msg && !form.dataset.confirmed) {
                e.preventDefault();
                pendingConfirmForm = form;
                document.getElementById("confirm-modal-text").textContent = msg;
                window.jQuery(confirmModal).modal("show");
            }
        });
        document.getElementById("confirm-modal-ok").addEventListener("click", function () {
            if (pendingConfirmForm) {
                pendingConfirmForm.dataset.confirmed = "1";
                pendingConfirmForm.submit();
                pendingConfirmForm = null;
            }
            window.jQuery(confirmModal).modal("hide");
        });
    }

    /* ===== 服务器详情页：设备信息异步加载（device_api，加载中提示，不阻塞渲染） ===== */
    var deviceBody = document.getElementById("device-info-body");
    if (deviceBody) {
        var deviceApiUrl = deviceBody.dataset.deviceUrl;
        var deviceLoading = document.getElementById("device-loading");
        var deviceContent = document.getElementById("device-info-content");
        var deviceError = document.getElementById("device-error");
        var npuList = document.getElementById("device-npu-list");

        function renderNpuCards(cards) {
            if (!npuList) return;
            if (!cards || !cards.length) {
                npuList.innerHTML = '<p class="text-muted" style="font-size:13px;">未检测到 NPU 卡（Ascend 驱动未加载或设备不可达）。</p>';
                return;
            }
            var html = '<table class="table table-striped info-table">' +
                "<thead><tr><th>设备号</th><th>型号</th><th>内存</th></tr></thead><tbody>";
            cards.forEach(function (c) {
                html += "<tr><td>" + c.index + "</td><td>" + c.soc_name + "</td><td>" + c.mem_g + "G</td></tr>";
            });
            html += "</tbody></table>";
            npuList.innerHTML = html;
        }

        function loadDeviceInfo() {
            // 显示加载中，隐藏内容区与错误
            if (deviceLoading) deviceLoading.style.display = "";
            if (deviceContent) deviceContent.style.display = "none";
            if (deviceError) deviceError.style.display = "none";
            if (!deviceApiUrl) {
                if (deviceError) {
                    deviceError.textContent = "设备信息接口不可用。";
                    deviceError.style.display = "";
                }
                return;
            }
            fetch(deviceApiUrl)
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (deviceLoading) deviceLoading.style.display = "none";
                    if (data && data.msg) {
                        // 查询失败（目标机不可达等）：提示 + 保留空值
                        if (deviceError) {
                            deviceError.textContent = data.msg;
                            deviceError.style.display = "";
                        }
                    }
                    if (deviceContent) {
                        var cpu = document.getElementById("device-cpu");
                        var memory = document.getElementById("device-memory");
                        var disk = document.getElementById("device-disk");
                        if (cpu) cpu.textContent = (data && data.cpu) || "未查询到";
                        if (memory) memory.textContent = (data && data.memory) || "未查询到";
                        if (disk) disk.textContent = (data && data.disk) || "未查询到";
                        renderNpuCards(data && data.npu);
                        deviceContent.style.display = "";
                    }
                })
                .catch(function () {
                    if (deviceLoading) deviceLoading.style.display = "none";
                    if (deviceError) {
                        deviceError.textContent = "设备信息获取失败，请稍后重试。";
                        deviceError.style.display = "";
                    }
                });
        }

        loadDeviceInfo();
        var deviceRefreshBtn = document.getElementById("device-refresh-btn");
        if (deviceRefreshBtn) {
            deviceRefreshBtn.addEventListener("click", loadDeviceInfo);
        }
    }

    /* ===== 消息提示自动消失（复用 Bootstrap alert，错误提示保留） ===== */
    if (window.jQuery) {
        window.jQuery(".alert-dismissible").not(".alert-danger").delay(3000).fadeOut(400);
    }

    /* ===== 用户公告：markdown 编辑器（textarea + 快捷按钮插入控制符，
       按钮定义在模板 data-before/data-after/data-placeholder，JS 不写模板标签） ===== */
    var annForm = document.getElementById("announcement-form");
    var annTextarea = document.getElementById("announcement-content");
    if (annForm && annTextarea) {
        annForm.querySelectorAll(".md-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var before = btn.getAttribute("data-before") || "";
                var after = btn.getAttribute("data-after") || "";
                var placeholder = btn.getAttribute("data-placeholder") || "文字";
                var v = annTextarea.value;
                var start = annTextarea.selectionStart;
                var end = annTextarea.selectionEnd;
                // 行首型按钮（data-before 以空格结尾，如 "# "）：插入到当前行首
                if (before.charAt(before.length - 1) === " ") {
                    var lineStart = v.lastIndexOf("\n", start - 1) + 1;
                    annTextarea.value = v.slice(0, lineStart) + before + v.slice(lineStart);
                    start = lineStart + before.length;
                    end = start;
                } else {
                    // 包裹型按钮：选中文字则包裹，未选中插入占位符并选中
                    var selected = v.slice(start, end) || placeholder;
                    annTextarea.value = v.slice(0, start) + before + selected + after + v.slice(end);
                    start = start + before.length;
                    end = start + selected.length;
                }
                annTextarea.focus();
                if (annTextarea.setSelectionRange) {
                    annTextarea.setSelectionRange(start, end);
                }
            });
        });
    }
})();
