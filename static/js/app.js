/**
 * AI Data Analyst Pro - Main Client JavaScript & Enterprise Auth Manager
 */

document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    initCopyButtons();
    initDragAndDrop();
    initFavorites();
    initPasswordToggle();
    initPasswordStrengthMeter();
    initLiveAvailabilityCheck();
    initAIAssistant();
    initMobileSidebar();
});

function initMobileSidebar() {
    const toggleBtn = document.getElementById("mobile-sidebar-toggle");
    const sidebar = document.querySelector(".sidebar");
    const backdrop = document.getElementById("sidebar-backdrop");

    if (!toggleBtn || !sidebar) return;

    function openSidebar() {
        sidebar.classList.add("open");
        if (backdrop) backdrop.classList.add("active");
    }

    function closeSidebar() {
        sidebar.classList.remove("open");
        if (backdrop) backdrop.classList.remove("active");
    }

    toggleBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (sidebar.classList.contains("open")) {
            closeSidebar();
        } else {
            openSidebar();
        }
    });

    if (backdrop) {
        backdrop.addEventListener("click", closeSidebar);
    }

    const sidebarLinks = sidebar.querySelectorAll(".sidebar-link");
    sidebarLinks.forEach(link => {
        link.addEventListener("click", () => {
            if (window.innerWidth < 992) {
                closeSidebar();
            }
        });
    });
}

/* ==========================================================================
   Theme Management (Light / Dark Mode with Persistence)
   ========================================================================== */
function initTheme() {
    const themeBtn = document.getElementById("theme-toggle");
    const savedTheme = localStorage.getItem("app-theme") || "light";

    if (savedTheme === "dark") {
        document.documentElement.setAttribute("data-theme", "dark");
        updateThemeBtnIcon(true);
    }

    if (themeBtn) {
        themeBtn.addEventListener("click", () => {
            const currentTheme = document.documentElement.getAttribute("data-theme");
            const isDark = currentTheme === "dark";
            const newTheme = isDark ? "light" : "dark";

            document.documentElement.setAttribute("data-theme", newTheme);
            localStorage.setItem("app-theme", newTheme);
            updateThemeBtnIcon(!isDark);
            showToast(`Switched to ${newTheme.toUpperCase()} mode`, "info");
        });
    }
}

function updateThemeBtnIcon(isDark) {
    const iconSpan = document.getElementById("theme-icon");
    const textSpan = document.getElementById("theme-text");
    if (iconSpan && textSpan) {
        iconSpan.textContent = isDark ? "☀️" : "🌙";
        textSpan.textContent = isDark ? "Light Mode" : "Dark Mode";
    }
}

/* ==========================================================================
   Show / Hide Password Toggle Helper
   ========================================================================== */
function initPasswordToggle() {
    const toggleBtns = document.querySelectorAll(".btn-toggle-pwd");
    toggleBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetId = btn.getAttribute("data-target");
            const input = document.getElementById(targetId);
            if (input) {
                const isPwd = input.type === "password";
                input.type = isPwd ? "text" : "password";
                btn.classList.toggle("fa-eye", !isPwd);
                btn.classList.toggle("fa-eye-slash", isPwd);
            }
        });
    });
}

/* ==========================================================================
   Live Password Strength Meter
   ========================================================================== */
function initPasswordStrengthMeter() {
    const pwdInput = document.getElementById("signup_password");
    const bar = document.getElementById("pwd-strength-bar");
    const text = document.getElementById("pwd-strength-text");

    if (pwdInput && bar && text) {
        pwdInput.addEventListener("input", () => {
            const val = pwdInput.value;
            let score = 0;

            if (val.length >= 8) score += 20;
            if (/[A-Z]/.test(val)) score += 20;
            if (/[a-z]/.test(val)) score += 20;
            if (/[0-9]/.test(val)) score += 20;
            if (/[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]/.test(val)) score += 20;

            bar.style.width = `${score}%`;

            if (score <= 40) {
                bar.style.backgroundColor = "#ef4444"; // Red
                text.textContent = "Weak Password (add uppercase, number, special char)";
                text.style.color = "#ef4444";
            } else if (score <= 80) {
                bar.style.backgroundColor = "#f59e0b"; // Amber
                text.textContent = "Moderate Password (almost secure)";
                text.style.color = "#f59e0b";
            } else {
                bar.style.backgroundColor = "#10b981"; // Green
                text.textContent = "Strong Password (enterprise ready)";
                text.style.color = "#10b981";
            }
        });
    }
}

/* ==========================================================================
   Real-Time Live Username & Email Availability Checks
   ========================================================================== */
function initLiveAvailabilityCheck() {
    const usernameInput = document.getElementById("username");
    const usernameFeedback = document.getElementById("username-feedback");
    const emailInput = document.getElementById("email");
    const emailFeedback = document.getElementById("email-feedback");

    if (usernameInput && usernameFeedback) {
        usernameInput.addEventListener("blur", async () => {
            const val = usernameInput.value.trim();
            if (val.length < 3) return;

            try {
                const res = await fetch("/auth/check-availability", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ field: "username", value: val })
                });
                const data = await res.json();
                usernameFeedback.textContent = data.message;
                usernameFeedback.style.color = data.available ? "#10b981" : "#ef4444";
            } catch (err) { }
        });
    }

    if (emailInput && emailFeedback) {
        emailInput.addEventListener("blur", async () => {
            const val = emailInput.value.trim();
            if (!val.includes("@")) return;

            try {
                const res = await fetch("/auth/check-availability", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ field: "email", value: val })
                });
                const data = await res.json();
                emailFeedback.textContent = data.message;
                emailFeedback.style.color = data.available ? "#10b981" : "#ef4444";
            } catch (err) { }
        });
    }
}

/* ==========================================================================
   Favorite Star Toggle API Handler
   ========================================================================== */
function initFavorites() {
    const favBtns = document.querySelectorAll(".btn-fav-toggle");
    favBtns.forEach(btn => {
        btn.addEventListener("click", async () => {
            const datasetId = btn.getAttribute("data-id");
            try {
                const res = await fetch(`/dataset/favorite/${datasetId}`, { method: "POST" });
                const data = await res.json();
                if (data.success) {
                    btn.textContent = btn.textContent.trim() === "⭐" ? "☆" : "⭐";
                    showToast("Favorite status updated!", "success");
                }
            } catch (err) {
                showToast("Failed to update favorite status", "error");
            }
        });
    });
}

/* ==========================================================================
   Toast Notification Manager
   ========================================================================== */
function showToast(message, type = "info") {
    let container = document.getElementById("toast-container");
    if (!container) {
        container = document.createElement("div");
        container.id = "toast-container";
        document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    
    let icon = "ℹ️";
    if (type === "success") icon = "✅";
    if (type === "error") icon = "❌";
    if (type === "warning") icon = "⚠️";

    toast.innerHTML = `<span>${icon}</span> <span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

window.showAppToast = showToast;


/* ==========================================================================
   Copy SQL / Code Snippet Helper
   ========================================================================== */
function initCopyButtons() {
    const copyBtns = document.querySelectorAll(".btn-copy-sql");
    copyBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetId = btn.getAttribute("data-target");
            const targetElem = document.getElementById(targetId);
            if (targetElem) {
                const text = targetElem.textContent.trim();
                navigator.clipboard.writeText(text).then(() => {
                    showToast("SQL copied to clipboard!", "success");
                });
            }
        });
    });
}

/* ==========================================================================
   Drag & Drop File Uploader & Spinner Overlay
   ========================================================================== */
function initDragAndDrop() {
    const dropZone = document.getElementById("drop-zone");
    const fileInput = document.getElementById("file-input");
    const uploadForm = document.getElementById("upload-form");

    if (dropZone && fileInput) {
        ["dragenter", "dragover"].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                dropZone.classList.add("dragover");
            }, false);
        });

        ["dragleave", "drop"].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                dropZone.classList.remove("dragover");
            }, false);
        });

        dropZone.addEventListener("drop", (e) => {
            const dt = e.dataTransfer;
            const files = dt.files;
            if (files.length > 0) {
                fileInput.files = files;
                updateFileNameDisplay(files[0].name);
            }
        });

        fileInput.addEventListener("change", () => {
            if (fileInput.files.length > 0) {
                updateFileNameDisplay(fileInput.files[0].name);
            }
        });
    }

    // Note: upload.html handles its own in-card progress tracker and error displays
}

function updateFileNameDisplay(name) {
    const nameDisplay = document.getElementById("file-name-display");
    if (nameDisplay) {
        nameDisplay.textContent = `Selected File: ${name}`;
    }
}

function showLoading(msg = "AI is processing your request...") {
    let overlay = document.getElementById("loading-overlay");
    if (overlay) {
        const textElem = overlay.querySelector(".loading-text");
        if (textElem) textElem.textContent = msg;
        overlay.style.display = "flex";
    }
}

function hideLoading() {
    let overlay = document.getElementById("loading-overlay");
    if (overlay) {
        overlay.style.display = "none";
    }
}

/* ==========================================================================
   FLOATING AI ASSISTANT & NOTIFICATION CENTER CONTROLLER
   ========================================================================== */
function initAIAssistant() {
    const widget = document.getElementById("ai-assistant-widget");
    const toggleBtn = document.getElementById("ai-widget-toggle-btn");
    const closeBtn = document.getElementById("ai-widget-close-btn");
    const historyBtn = document.getElementById("ai-widget-history-btn");

    const notifBtn = document.getElementById("btn-notification-center");
    const notifDrawer = document.getElementById("notif-drawer");
    const notifBackdrop = document.getElementById("notif-drawer-backdrop");
    const notifCloseBtn = document.getElementById("notif-drawer-close");
    const markReadBtn = document.getElementById("btn-mark-all-read");
    const clearNotifBtn = document.getElementById("btn-clear-notifications");

    // Check saved minimize state
    const isMinimized = localStorage.getItem("ai-widget-minimized") === "true";
    if (widget && isMinimized) {
        widget.classList.add("minimized");
        if (toggleBtn) toggleBtn.innerHTML = '<i class="fa-solid fa-plus"></i>';
    }

    // Toggle minimize
    if (toggleBtn && widget) {
        toggleBtn.addEventListener("click", () => {
            const min = widget.classList.toggle("minimized");
            localStorage.setItem("ai-widget-minimized", min);
            toggleBtn.innerHTML = min ? '<i class="fa-solid fa-plus"></i>' : '<i class="fa-solid fa-minus"></i>';
        });
    }

    // Voice button -> replay AI Voice speech or toggle mute
    const voiceBtn = document.getElementById("ai-widget-voice-btn");
    if (voiceBtn) {
        voiceBtn.addEventListener("click", () => {
            const isMuted = localStorage.getItem("ai-voice-muted") === "true";
            if (isMuted) {
                localStorage.setItem("ai-voice-muted", "false");
                voiceBtn.innerHTML = '<i class="fa-solid fa-volume-high"></i>';
                if (window.currentAIGreetingText && window.speakText) {
                    window.speakText(window.currentAIGreetingText);
                }
                if (window.showAppToast) window.showAppToast("🔊 AI Voice Speech Enabled", "info");
            } else {
                localStorage.setItem("ai-voice-muted", "true");
                voiceBtn.innerHTML = '<i class="fa-solid fa-volume-xmark" style="color:#ef4444;"></i>';
                if (window.speechSynthesis) window.speechSynthesis.cancel();
                if (window.showAppToast) window.showAppToast("🔇 AI Voice Speech Muted", "warning");
            }
        });

        if (localStorage.getItem("ai-voice-muted") === "true") {
            voiceBtn.innerHTML = '<i class="fa-solid fa-volume-xmark" style="color:#ef4444;"></i>';
        }
    }

    // Close widget
    if (closeBtn && widget) {
        closeBtn.addEventListener("click", () => {
            widget.style.display = "none";
            if (window.speechSynthesis) window.speechSynthesis.cancel();
        });
    }

    // History button -> opens notification drawer
    if (historyBtn) {
        historyBtn.addEventListener("click", openNotificationDrawer);
    }

    // Top bar bell button -> opens notification drawer
    if (notifBtn) {
        notifBtn.addEventListener("click", openNotificationDrawer);
    }

    if (notifCloseBtn) notifCloseBtn.addEventListener("click", closeNotificationDrawer);
    if (notifBackdrop) notifBackdrop.addEventListener("click", closeNotificationDrawer);

    if (markReadBtn) {
        markReadBtn.addEventListener("click", async () => {
            try {
                await fetch("/api/ai-notifications/mark-read", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ notification_id: 0 })
                });
                updateNotificationBadge(0);
                loadNotificationHistory();
            } catch (err) { }
        });
    }

    if (clearNotifBtn) {
        clearNotifBtn.addEventListener("click", async () => {
            if (!confirm("Are you sure you want to clear your AI notification history?")) return;
            try {
                await fetch("/api/ai-notifications/clear", { method: "POST" });
                updateNotificationBadge(0);
                loadNotificationHistory();
            } catch (err) { }
        });
    }

    // Fetch active AI card & unread count on page load
    fetchCurrentAICard();
}

async function fetchCurrentAICard() {
    try {
        const res = await fetch("/api/ai-assistant/current");
        const data = await res.json();

        if (data.unread_count !== undefined) {
            updateNotificationBadge(data.unread_count);
        }

        if (data.card) {
            displayAICard(data.card);
        }
    } catch (err) { }
}

function updateNotificationBadge(count) {
    const badge = document.getElementById("notif-badge-count");
    if (badge) {
        if (count > 0) {
            badge.textContent = count > 99 ? "99+" : count;
            badge.style.display = "block";
        } else {
            badge.style.display = "none";
        }
    }
}

function displayAICard(card) {
    if (!card) return;

    // Determine speech text & toast message
    let speechText = card.speech || "";
    if (!speechText && card.lines && card.lines.length > 0) {
        speechText = card.lines.filter(l => !l.includes("🤖 AI Assistant")).join(". ");
    }

    window.currentAIGreetingText = speechText;

    // Speak AI Voice greeting out loud using Web Speech Synthesis API
    if (window.speakText && speechText && localStorage.getItem("ai-voice-muted") !== "true") {
        setTimeout(() => {
            window.speakText(speechText);
        }, 400);
    }

    // Display clean, professional toast notification
    const toastMsg = card.lines && card.lines.length > 0 ? card.lines.join(" ") : card.title;
    let toastType = "info";
    if (card.category === "login" || card.category === "signup" || card.category === "email_verified" || card.category === "upload") {
        toastType = "success";
    } else if (card.category === "failed_login" || card.category === "security_alert" || card.category === "account_locked") {
        toastType = "error";
    }

    showToast(toastMsg, toastType);
}

function openNotificationDrawer() {
    const drawer = document.getElementById("notif-drawer");
    const backdrop = document.getElementById("notif-drawer-backdrop");
    if (drawer && backdrop) {
        drawer.style.display = "flex";
        backdrop.style.display = "block";
        loadNotificationHistory();
    }
}

function closeNotificationDrawer() {
    const drawer = document.getElementById("notif-drawer");
    const backdrop = document.getElementById("notif-drawer-backdrop");
    if (drawer && backdrop) {
        drawer.style.display = "none";
        backdrop.style.display = "none";
    }
}

function getNotifIcon(cat) {
    switch (cat) {
        case "login": return '<i class="fa-solid fa-right-to-bracket" style="color: var(--accent-green);"></i>';
        case "signup": return '<i class="fa-solid fa-user-plus" style="color: var(--accent-blue);"></i>';
        case "email_verified": return '<i class="fa-solid fa-circle-check" style="color: var(--accent-green);"></i>';
        case "upload": return '<i class="fa-solid fa-cloud-arrow-up" style="color: var(--accent-blue);"></i>';
        case "failed_login": return '<i class="fa-solid fa-triangle-exclamation" style="color: var(--accent-red);"></i>';
        case "security_alert": return '<i class="fa-solid fa-shield-halved" style="color: var(--accent-amber);"></i>';
        case "account_locked": return '<i class="fa-solid fa-lock" style="color: var(--accent-red);"></i>';
        default: return '<i class="fa-solid fa-bell" style="color: var(--accent-blue);"></i>';
    }
}

function formatNotificationBody(msg, metadata) {
    if (!msg) return "";

    let bodyText = msg;
    let metricsObj = metadata && metadata.metrics ? metadata.metrics : null;

    // Check if JSON object string was embedded inside message text
    try {
        const jsonMatch = bodyText.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
            if (!metricsObj) {
                metricsObj = JSON.parse(jsonMatch[0]);
            }
            bodyText = bodyText.replace(jsonMatch[0], "").trim();
        }
    } catch (e) { }

    let html = "";
    const lines = bodyText.split("\n").map(l => l.trim()).filter(l => l);

    lines.forEach(line => {
        if (line.startsWith("• ")) {
            html += `<div style="font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-top: 3px;">${escapeHtml(line)}</div>`;
        } else {
            html += `<div style="margin-bottom: 4px; font-size: 13px;">${escapeHtml(line)}</div>`;
        }
    });

    if (metricsObj && typeof metricsObj === "object") {
        html += '<div style="display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px;">';
        for (let [k, v] of Object.entries(metricsObj)) {
            html += `<span style="background: var(--bg-primary); border: 1px solid var(--border-color); padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 700; color: var(--text-primary);"><i class="fa-solid fa-chart-simple" style="color: var(--accent-blue); margin-right: 4px;"></i>${escapeHtml(k)}: <span style="color: var(--accent-blue);">${escapeHtml(String(v))}</span></span>`;
        }
        html += '</div>';
    }

    return html;
}

async function loadNotificationHistory() {
    const listElem = document.getElementById("notif-drawer-list");
    if (!listElem) return;

    listElem.innerHTML = '<div style="text-align: center; padding: 20px; color: var(--text-muted);">Loading notifications...</div>';

    try {
        const res = await fetch("/api/ai-notifications");
        const data = await res.json();
        const notifs = data.notifications || [];

        if (notifs.length === 0) {
            listElem.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-muted);"><i class="fa-solid fa-inbox" style="font-size: 32px; margin-bottom: 10px;"></i><p>No previous notifications found.</p></div>';
            return;
        }

        let html = "";
        notifs.forEach(n => {
            const icon = getNotifIcon(n.category);
            const bodyHtml = formatNotificationBody(n.message, n.metadata);

            html += `
                <div class="notif-item ${n.is_read ? '' : 'unread'}" style="padding: 14px; margin-bottom: 10px; border-radius: var(--radius-md); background: var(--bg-surface); border: 1px solid var(--border-color); border-left: 4px solid var(--accent-blue);">
                    <div class="notif-item-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <span class="notif-item-title" style="font-weight: 700; font-size: 14px; display: flex; align-items: center; gap: 8px;">
                            ${icon} ${escapeHtml(n.title)}
                        </span>
                        <span class="notif-item-time" style="font-size: 11px; color: var(--text-muted);">${escapeHtml(n.created_at)}</span>
                    </div>
                    <div class="notif-item-msg" style="color: var(--text-secondary); font-size: 13px; line-height: 1.5;">${bodyHtml}</div>
                </div>
            `;
        });

        listElem.innerHTML = html;
    } catch (err) {
        listElem.innerHTML = '<div style="text-align: center; padding: 20px; color: #ef4444;">Failed to load notification history.</div>';
    }
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function showToast(message, type = "info", title = "") {
    let container = document.getElementById("toast-container");
    if (!container) {
        container = document.createElement("div");
        container.id = "toast-container";
        document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.className = `toast toast-${type} animated-toast`;
    toast.style.cssText = "position: fixed; top: 24px; right: 24px; z-index: 99999; min-width: 320px; max-width: 440px; padding: 16px 20px; border-radius: 12px; background: var(--bg-card, #ffffff); color: var(--text-color, #1e293b); box-shadow: 0 10px 30px rgba(0,0,0,0.18); border-left: 5px solid #2563eb; animation: toastSlideDown 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards; transition: all 0.3s ease;";

    if (type === "success") toast.style.borderLeftColor = "#10b981";
    if (type === "error") toast.style.borderLeftColor = "#ef4444";
    if (type === "warning") toast.style.borderLeftColor = "#f59e0b";

    let icon = "✅";
    if (type === "error") icon = "❌";
    if (type === "warning") icon = "⚠️";
    if (type === "info") icon = "ℹ️";

    let titleHtml = title ? `<strong style="display:block; font-size: 14px; font-weight: 700; margin-bottom: 2px;">${escapeHtml(title)}</strong>` : "";

    toast.innerHTML = `
        <div style="display: flex; align-items: flex-start; gap: 12px; width: 100%;">
            <span style="font-size: 20px; line-height: 1;">${icon}</span>
            <div style="flex: 1;">
                ${titleHtml}
                <div style="font-size: 13px; line-height: 1.4;">${message}</div>
            </div>
            <button onclick="this.closest('.animated-toast').remove()" style="background: none; border: none; color: inherit; opacity: 0.6; cursor: pointer; font-size: 16px;">&times;</button>
        </div>
    `;

    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateY(-12px)";
        setTimeout(() => toast.remove(), 350);
    }, 4000);
}
