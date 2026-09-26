/**
 * Unified Theme Manager (Light / Dark Mode)
 * Single source of truth for theme state across the entire site. Previously there were
 * four separate, independent implementations (main app, public pages, admin console,
 * admin login) each using a different localStorage key, so the theme silently disagreed
 * between sections of the site and only applied after the page had already rendered.
 */
(function () {
    "use strict";

    var STORAGE_KEY = "theme";
    var LEGACY_KEYS = ["app-theme", "website_theme"];

    function getStoredTheme() {
        try {
            var saved = localStorage.getItem(STORAGE_KEY);
            if (saved === "light" || saved === "dark") return saved;
            for (var i = 0; i < LEGACY_KEYS.length; i++) {
                var legacy = localStorage.getItem(LEGACY_KEYS[i]);
                if (legacy === "light" || legacy === "dark") return legacy;
            }
        } catch (e) {
            /* localStorage unavailable (private mode, etc.) - fall back to server default */
        }
        return null;
    }

    function updateIcons(theme) {
        var isDark = theme === "dark";

        var iconSpan = document.getElementById("theme-icon");
        var textSpan = document.getElementById("theme-text");
        if (iconSpan && textSpan) {
            iconSpan.textContent = isDark ? "☀️" : "🌙";
            textSpan.textContent = isDark ? "Light Mode" : "Dark Mode";
        }

        var faIcon = document.getElementById("theme-toggle-icon");
        if (faIcon) {
            faIcon.className = isDark ? "fa-solid fa-sun" : "fa-solid fa-moon";
            faIcon.style.color = isDark ? "#f59e0b" : "#60a5fa";
        }
    }

    window.toggleWebsiteTheme = function () {
        var current = document.documentElement.getAttribute("data-theme") || "light";
        var next = current === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", next);
        try {
            localStorage.setItem(STORAGE_KEY, next);
        } catch (e) {}
        updateIcons(next);
        window.dispatchEvent(new CustomEvent("websiteThemeChanged", { detail: { theme: next } }));
        if (typeof showToast === "function") {
            showToast("Switched to " + next.toUpperCase() + " mode", "info");
        }
    };

    document.addEventListener("DOMContentLoaded", function () {
        var themeBtn = document.getElementById("theme-toggle");
        if (themeBtn) {
            themeBtn.addEventListener("click", window.toggleWebsiteTheme);
        }
        // The <head> bootstrap script (see base templates) already applied the correct
        // data-theme attribute before first paint - this just syncs the icon/label to match.
        updateIcons(document.documentElement.getAttribute("data-theme") || "light");
    });

    // Exposed so pages can read the resolved theme without re-implementing the fallback logic.
    window.getStoredTheme = getStoredTheme;
})();
