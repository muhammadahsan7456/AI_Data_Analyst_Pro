/**
 * Automatically attaches the CSRF token (from the <meta name="csrf-token"> tag) to every
 * same-origin state-changing fetch()/XMLHttpRequest call on the page, so existing AJAX code
 * doesn't need to be individually rewritten to work with Flask-WTF's CSRF protection.
 * GET/HEAD/OPTIONS requests and requests that already set the header are left untouched.
 */
(function () {
    "use strict";

    function getCsrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") : null;
    }

    var UNSAFE_METHODS = ["POST", "PUT", "PATCH", "DELETE"];

    if (window.fetch) {
        var originalFetch = window.fetch;
        window.fetch = function (input, init) {
            var token = getCsrfToken();
            init = init || {};
            var method = (init.method || "GET").toUpperCase();

            if (token && UNSAFE_METHODS.indexOf(method) !== -1) {
                if (init.headers instanceof Headers) {
                    if (!init.headers.has("X-CSRFToken")) {
                        init.headers.set("X-CSRFToken", token);
                    }
                } else {
                    init.headers = Object.assign({}, init.headers);
                    if (!("X-CSRFToken" in init.headers)) {
                        init.headers["X-CSRFToken"] = token;
                    }
                }
            }
            return originalFetch(input, init);
        };
    }

    if (window.XMLHttpRequest) {
        var originalOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function (method) {
            this.__csrfMethod = (method || "GET").toUpperCase();
            return originalOpen.apply(this, arguments);
        };

        var originalSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function () {
            var token = getCsrfToken();
            if (token && UNSAFE_METHODS.indexOf(this.__csrfMethod) !== -1) {
                try {
                    this.setRequestHeader("X-CSRFToken", token);
                } catch (e) {
                    /* header already set, or request not yet opened - ignore */
                }
            }
            return originalSend.apply(this, arguments);
        };
    }
})();
