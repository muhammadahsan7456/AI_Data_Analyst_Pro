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

    let hideTimer = null;
    let suppressNext = false;

    function startProgress() {
        const bar = ensureBar();
        bar.classList.remove("page-progress-done");
        // Force reflow so the width transition restarts cleanly if triggered twice.
        void bar.offsetWidth;
        bar.classList.add("page-progress-active");

        ensureSpinner().classList.add("page-progress-spinner-active");

        // Safety net: a same-page file download (Content-Disposition: attachment) can
        // fire beforeunload in some browsers even though no real navigation happens, and
        // since the page never actually unloads, pageshow - the only other thing that
        // clears this bar - never fires either. Without this, the bar/spinner was left
        // visible indefinitely until the user manually refreshed, which is exactly the
        // "stuck loading after a download finishes" symptom. No real page navigation on
        // this app legitimately takes this long, so auto-clearing here is always safe.
        if (hideTimer) clearTimeout(hideTimer);
        hideTimer = setTimeout(stopProgress, 8000);
    }

    function stopProgress() {
        if (hideTimer) {
            clearTimeout(hideTimer);
            hideTimer = null;
        }
        const bar = document.getElementById("page-progress-bar");
        if (bar) bar.classList.remove("page-progress-active");
        const spinner = document.getElementById("page-progress-spinner");
        if (spinner) spinner.classList.remove("page-progress-spinner-active");
    }

    // Root-cause fix: don't even start the bar for a click that's going to trigger a
    // file download rather than a real page navigation - an <a> with a `download`
    // attribute, or pointing at one of this app's known file-response routes. These
    // never navigate the page away, so the bar had nothing real to represent for them.
    const DOWNLOAD_HREF_PATTERN = /\/(dataset\/export|query\/export|download-chart|receipts\/\d+\/download|admin\/(users|payments)\/export|admin\/payment\/invoice\/\d+\/download)\b/;

    function isDownloadLink(el) {
        const link = el && el.closest ? el.closest("a[href]") : null;
        if (!link) return false;
        if (link.hasAttribute("download")) return true;
        return DOWNLOAD_HREF_PATTERN.test(link.getAttribute("href") || "");
    }

    document.addEventListener("DOMContentLoaded", function () {
        // Capture phase, so this runs before the browser's own navigation handling and
        // before any other click listener on the link itself.
        document.addEventListener("click", function (e) {
            if (isDownloadLink(e.target)) {
                suppressNext = true;
                // The window never actually unloads for a download, so there's nothing to
                // un-suppress on pageshow for this path - clear the flag shortly after
                // instead, well past when a real competing navigation would have started.
                setTimeout(function () { suppressNext = false; }, 1000);
            }
        }, true);

        window.addEventListener("beforeunload", function () {
            if (suppressNext) return;
            startProgress();
        });

        // The new page has already finished loading by the time this fires, so clear
        // any bar/spinner left over from the navigation that just completed, and also
        // from a cancelled navigation restored via the back/forward cache.
        window.addEventListener("pageshow", stopProgress);
    });
})();
