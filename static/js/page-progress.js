/**
 * Loading feedback for full page navigations (link clicks, form submits, back/forward):
 * a thin top-of-page bar (GitHub/YouTube pattern) plus a small round spinner badge next
 * to it, so the "something is happening" signal is unmistakable, not just a thin line
 * easy to miss. Distinct from the upload/AJAX-specific loaders elsewhere (showLoading()/
 * hideLoading() in app.js): this one is universal and needs no per-page wiring.
 */
(function () {
    "use strict";

    function ensureBar() {
        let bar = document.getElementById("page-progress-bar");
        if (!bar) {
            bar = document.createElement("div");
            bar.id = "page-progress-bar";
            document.body.appendChild(bar);
        }
        return bar;
    }

    function ensureSpinner() {
        let spinner = document.getElementById("page-progress-spinner");
        if (!spinner) {
            spinner = document.createElement("div");
            spinner.id = "page-progress-spinner";
            spinner.innerHTML = '<div class="page-progress-spinner-ring"></div>';
            document.body.appendChild(spinner);
        }
        return spinner;
    }

    function startProgress() {
        const bar = ensureBar();
        bar.classList.remove("page-progress-done");
        // Force reflow so the width transition restarts cleanly if triggered twice.
        void bar.offsetWidth;
        bar.classList.add("page-progress-active");

        ensureSpinner().classList.add("page-progress-spinner-active");
    }

    function stopProgress() {
        const bar = document.getElementById("page-progress-bar");
        if (bar) bar.classList.remove("page-progress-active");
        const spinner = document.getElementById("page-progress-spinner");
        if (spinner) spinner.classList.remove("page-progress-spinner-active");
    }

    document.addEventListener("DOMContentLoaded", function () {
        // Any real page navigation (link click, non-AJAX form submit, back/forward,
        // closing the tab) fires beforeunload - AJAX/fetch calls never do, so this
        // never fires for the app's existing XHR-based upload/chat flows.
        window.addEventListener("beforeunload", startProgress);

        // The new page has already finished loading by the time this fires, so clear
        // any bar/spinner left over from the navigation that just completed, and also
        // from a cancelled navigation restored via the back/forward cache.
        window.addEventListener("pageshow", stopProgress);
    });
})();
