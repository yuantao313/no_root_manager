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

    function escapeHtml(value) {
        return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
        });
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
    document.querySelectorAll(".field-cancel").forEach(function (button) {
        button.addEventListener("click", function () { cancelEdit(this.dataset.field); });
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

    /* ===== 申请页：按申请类型显示对应字段 ===== */
    var serverSelect = document.querySelector('select[name="target_server"]');
    var applyTypeSelect = document.getElementById("id_apply_type");
    var userGroupsField = document.getElementById("user-groups-field");
    var transferField = document.getElementById("transfer-field");
    var adminField = document.getElementById("admin-field");

    if (serverSelect) {
        function toggleTypeFields() {
            var t = applyTypeSelect && applyTypeSelect.value;
            if (userGroupsField) userGroupsField.style.display = t === "group" ? "" : "none";
            if (transferField) transferField.style.display = t === "transfer" ? "" : "none";
            if (adminField) adminField.style.display = t === "admin" ? "" : "none";
        }
        if (applyTypeSelect) applyTypeSelect.addEventListener("change", toggleTypeFields);
        toggleTypeFields();

        if (window.jQuery && window.jQuery.fn && window.jQuery.fn.select2) {
            window.jQuery(serverSelect).select2({ width: "100%" });
        }

        var appForm = document.getElementById("application-form");
        if (appForm) {
            appForm.addEventListener("submit", function (e) {
                if (!serverSelect.value) {
                    e.preventDefault();
                    window.alert("请先选择目标服务器。");
                }
            });
        }
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

    /* ===== 只读配置值：一键复制（如 GitCode 回调地址） ===== */
    document.querySelectorAll("[data-copy-target]").forEach(function (button) {
        button.addEventListener("click", function () {
            var target = document.querySelector(button.dataset.copyTarget);
            if (!target) return;
            var originalLabel = button.textContent;
            function showCopied() {
                button.textContent = "已复制";
                window.setTimeout(function () { button.textContent = originalLabel; }, 1500);
            }
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(target.value).then(showCopied).catch(function () {
                    target.select();
                    if (document.execCommand("copy")) showCopied();
                });
            } else {
                target.select();
                if (document.execCommand("copy")) showCopied();
            }
        });
    });

    /* ===== 服务器表单：填入已核对的候选 SSH 主机指纹 ===== */
    var fillHostKey = document.querySelector("[data-fill-host-key]");
    if (fillHostKey) {
        fillHostKey.addEventListener("click", function () {
            var field = document.getElementById("id_ssh_host_key_fingerprint");
            if (field) {
                field.value = fillHostKey.dataset.fillHostKey;
                field.focus();
            }
        });
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

    /* ===== 表单提交防重复 =====
       不能用 disabled 禁用按钮：disabled 的 submit 按钮不会随表单提交 name，
       会导致后端按按钮 name 分发（save_mail_webhook / save_email_final 等）失效、保存不了。
       改用 dataset 标志拦截重复提交（页面导航后自然重置）。 */
    function initializeSubmitGuards(root) {
        root.querySelectorAll("form:not([data-submit-guard])").forEach(function (form) {
            form.setAttribute("data-submit-guard", "1");
            form.addEventListener("submit", function (event) {
                // 带确认框的表单先交给下方委托监听器；取消确认后仍应可再次提交。
                if (form.getAttribute("data-confirm") && !form.dataset.confirmed) return;
                if (form.dataset.submitting === "1") {
                    event.preventDefault();
                    return;
                }
                form.dataset.submitting = "1";
            });
        });
    }
    initializeSubmitGuards(document);

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

    /* ===== 服务器详情页：用户组状态编辑 =====
       现有组点击后标记待删除；虚线按钮录入的新组标记待添加；最后统一确认。 */
    function initializeGroupEditors(root) {
        root.querySelectorAll("[data-group-form]").forEach(function (form) {
        var row = form.closest("tr");
        var saveBtn = form.querySelector("[data-save-groups]");
        var editor = row && row.querySelector("[data-group-editor]");
        if (!row || !saveBtn || !editor) return;
        var chips = editor.querySelector(".nrm-group-chips");
        var addTrigger = editor.querySelector("[data-group-add]");
        var addEntry = editor.querySelector("[data-group-entry]");
        var addInput = editor.querySelector("[data-group-input]");
        var addConfirm = editor.querySelector("[data-group-add-confirm]");
        var addCancel = editor.querySelector("[data-group-add-cancel]");
        var error = editor.querySelector("[data-group-error]");

        function setError(message) {
            if (error) error.textContent = message || "";
        }

        function refreshDirtyState() {
            var dirty = false;
            chips.querySelectorAll("[data-group]").forEach(function (chip) {
                var original = chip.getAttribute("data-original") === "1";
                var active = chip.getAttribute("data-active") === "1";
                if ((original && !active) || (!original && active)) dirty = true;
            });
            saveBtn.disabled = !dirty;
        }

        function setExistingState(chip, active) {
            chip.setAttribute("data-active", active ? "1" : "0");
            chip.setAttribute("aria-pressed", active ? "true" : "false");
            chip.classList.toggle("nrm-group-chip-current", active);
            chip.classList.toggle("nrm-group-chip-remove", !active);
            chip.classList.toggle("btn-primary", active);
            chip.classList.toggle("btn-danger", !active);
            chip.title = active
                ? "点击标记删除 " + chip.getAttribute("data-group")
                : "待删除；再次点击可撤销";
        }

        function closeAddEntry() {
            addEntry.hidden = true;
            addTrigger.hidden = false;
            addInput.value = "";
        }

        function findGroup(name) {
            var matched = null;
            chips.querySelectorAll("[data-group]").forEach(function (chip) {
                if (chip.getAttribute("data-group") === name) matched = chip;
            });
            return matched;
        }

        function addPendingGroup() {
            var name = addInput.value.trim();
            if (!/^[a-zA-Z_][a-zA-Z0-9_-]{0,31}$/.test(name)) {
                setError("组名需以英文字母或下划线开头，最长 32 位。");
                addInput.focus();
                return;
            }
            var existing = findGroup(name);
            if (existing) {
                if (existing.getAttribute("data-original") === "1" && existing.getAttribute("data-active") !== "1") {
                    setExistingState(existing, true);
                    setError("");
                    closeAddEntry();
                    refreshDirtyState();
                    return;
                }
                setError("该用户组已在列表中。");
                addInput.focus();
                return;
            }
            var chip = document.createElement("button");
            chip.type = "button";
            chip.className = "btn btn-success btn-xs nrm-group-chip nrm-group-chip-add group-toggle";
            chip.textContent = name;
            chip.setAttribute("data-group", name);
            chip.setAttribute("data-active", "1");
            chip.setAttribute("data-original", "0");
            chip.setAttribute("aria-pressed", "true");
            chip.title = "待添加；点击可取消";
            chips.insertBefore(chip, addTrigger);
            setError("");
            closeAddEntry();
            refreshDirtyState();
        }

        chips.addEventListener("click", function (event) {
            var chip = event.target.closest(".group-toggle");
            if (!chip || !chips.contains(chip)) return;
            if (chip.getAttribute("data-original") === "0") {
                chip.remove();
            } else {
                setExistingState(chip, chip.getAttribute("data-active") !== "1");
            }
            setError("");
            refreshDirtyState();
        });

        addTrigger.addEventListener("click", function () {
            addTrigger.hidden = true;
            addEntry.hidden = false;
            setError("");
            addInput.focus();
        });
        addConfirm.addEventListener("click", addPendingGroup);
        addCancel.addEventListener("click", function () {
            setError("");
            closeAddEntry();
        });
        addInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                addPendingGroup();
            } else if (event.key === "Escape") {
                event.preventDefault();
                setError("");
                closeAddEntry();
            }
        });

        // 提交前收集所有未标记删除的组，交给后端批量计算差异。
        form.addEventListener("submit", function () {
            var groups = [];
            chips.querySelectorAll("[data-group][data-active='1']").forEach(function (chip) {
                groups.push(chip.getAttribute("data-group"));
            });
            form.querySelector('input[name="groups"]').value = groups.join(",");
        });
            refreshDirtyState();
        });
    }

    initializeGroupEditors(document);

    /* ===== 服务器详情页：用户区异步加载 =====
       页面外壳不等待 SSH；设备与用户快照由浏览器并行请求。 */
    var userManagement = document.getElementById("server-user-management");
    if (userManagement) {
        function loadUserManagement() {
            fetch(userManagement.dataset.userManagementUrl, {
                headers: { "X-Requested-With": "XMLHttpRequest" },
            }).then(function (response) {
                if (response.redirected) {
                    window.location.href = response.url;
                    throw new Error("redirected");
                }
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.text();
            }).then(function (html) {
                userManagement.innerHTML = html;
                initializeSubmitGuards(userManagement);
                initializeGroupEditors(userManagement);
            }).catch(function (error) {
                if (error.message === "redirected") return;
                userManagement.innerHTML = '<div class="alert alert-danger">读取用户状态失败。' +
                    '<button type="button" class="btn btn-danger btn-xs" data-user-retry>重试</button></div>';
                userManagement.querySelector("[data-user-retry]").addEventListener("click", loadUserManagement);
            });
        }
        loadUserManagement();
    }
})();
