/**
 * AI Data Analyst Pro — Shared Animation Engine
 * Three.js 3D constellation, scroll reveals, animated counters, typed text
 */

// This file is now loaded dynamically after window.load (see landing_base.html), which
// fires well after DOMContentLoaded - every document.addEventListener('DOMContentLoaded',
// ...) below used to run immediately once this file parsed, since the browser fired
// DOMContentLoaded right away in the normal <script defer> load order. A listener added
// for an event that has already fired never runs (confirmed live), so those callbacks were
// silently dead: .reveal/.reveal-left/.reveal-right/.reveal-scale elements (opacity: 0 by
// default in CSS, only made visible by the scroll-reveal observer below) stayed invisible
// forever, counters never animated, tilt/anchor-scroll never initialized. This runs the
// callback immediately if the DOM is already ready, exactly like DOMContentLoaded would
// have for a listener registered in time.
function onDomReady(fn) {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fn);
    } else {
        fn();
    }
}

// ===== 1. (removed in R-Phase 9.9) =====
// The Three.js constellation background (about 600 KB of script from a CDN on every
// public page except home/auth) was decorative and is gone. Depth now comes from CSS:
// layered shadows, the perspective tilt on the home preview (section 5) and hover lift.

// ===== 2. SCROLL REVEAL OBSERVER =====
(function initScrollReveal() {
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('revealed');
                // Don't unobserve so elements don't reset
            }
        });
    // threshold/rootMargin tuned conservative (0.12, -40px) read fine on a slow, deliberate
    // desktop scroll, but on a real phone - faster flick-scrolling, smaller viewport so more
    // of a long section is off-screen at once - entire sections stayed at opacity:0 for a
    // noticeable stretch, reading as "half the page is blank/broken" rather than a fade-in.
    // Triggering as soon as any sliver of the element is visible keeps the fade-in effect
    // for a deliberate scroll while eliminating that blank window on mobile.
    }, { threshold: 0.01, rootMargin: '0px 0px 0px 0px' });

    onDomReady(() => {
        const els = document.querySelectorAll('.reveal, .reveal-left, .reveal-right, .reveal-scale');
        els.forEach(el => observer.observe(el));

        // Safety net: if IntersectionObserver never fires for some element (a slow/low-end
        // device, a browser quirk, a tab that was backgrounded during load), force every
        // reveal element visible after 3s regardless. No real page content on this app ever
        // needs longer than that to genuinely appear - the alternative is content that stays
        // invisible indefinitely, which is a worse failure than skipping the fade-in.
        setTimeout(() => {
            els.forEach(el => el.classList.add('revealed'));
        }, 3000);
    });
})();


// ===== 3. ANIMATED NUMBER COUNTERS =====
(function initCounters() {
    function animateCounter(el) {
        const target = parseFloat(el.getAttribute('data-target'));
        const suffix = el.getAttribute('data-suffix') || '';
        const prefix = el.getAttribute('data-prefix') || '';
        const decimals = parseInt(el.getAttribute('data-decimals') || '0');
        const duration = parseInt(el.getAttribute('data-duration') || '2000');
        const startTime = performance.now();

        function step(now) {
            const elapsed = now - startTime;
            const progress = Math.min(elapsed / duration, 1);
            // Ease out cubic
            const eased = 1 - Math.pow(1 - progress, 3);
            const current = target * eased;

            el.textContent = prefix + current.toFixed(decimals).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + suffix;

            if (progress < 1) {
                requestAnimationFrame(step);
            }
        }

        requestAnimationFrame(step);
    }

    const counterObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting && !entry.target.dataset.counted) {
                entry.target.dataset.counted = 'true';
                animateCounter(entry.target);
            }
        });
    }, { threshold: 0.5 });

    onDomReady(() => {
        document.querySelectorAll('[data-counter]').forEach(el => {
            counterObserver.observe(el);
        });
    });
})();


// ===== 4. TYPED TEXT EFFECT =====
function initTypedText(elementId, texts, typeSpeed, deleteSpeed, pauseTime) {
    const el = document.getElementById(elementId);
    if (!el) return;

    let textIndex = 0;
    let charIndex = 0;
    let isDeleting = false;

    function type() {
        const currentText = texts[textIndex];

        if (isDeleting) {
            el.textContent = currentText.substring(0, charIndex - 1);
            charIndex--;
        } else {
            el.textContent = currentText.substring(0, charIndex + 1);
            charIndex++;
        }

        let delay = isDeleting ? deleteSpeed : typeSpeed;

        if (!isDeleting && charIndex === currentText.length) {
            delay = pauseTime;
            isDeleting = true;
        } else if (isDeleting && charIndex === 0) {
            isDeleting = false;
            textIndex = (textIndex + 1) % texts.length;
            delay = 400;
        }

        setTimeout(type, delay);
    }

    type();
}


// ===== 5. HERO PERSPECTIVE TILT (CSS variables, no library) =====
// Desktop with a fine pointer only, and never with prefers-reduced-motion. The CSS in
// mono.css owns the resting angle; this only nudges --tilt-x/--tilt-y toward the pointer
// (max about 6 degrees), one rAF per frame, transform only.
onDomReady(() => {
    const card = document.querySelector('.home-workspace-preview');
    if (!card || !window.matchMedia) return;
    if (!window.matchMedia('(min-width: 1025px) and (hover: hover) and (pointer: fine)').matches) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const zone = card.closest('.home-hero-layout') || card;
    let frame = 0;
    zone.addEventListener('pointermove', (e) => {
        if (frame) return;
        frame = requestAnimationFrame(() => {
            frame = 0;
            const r = card.getBoundingClientRect();
            const dx = (e.clientX - (r.left + r.width / 2)) / r.width;
            const dy = (e.clientY - (r.top + r.height / 2)) / r.height;
            card.style.setProperty('--tilt-y', (-4 + dx * 6).toFixed(2) + 'deg');
            card.style.setProperty('--tilt-x', (2 - dy * 4).toFixed(2) + 'deg');
        });
    });
    zone.addEventListener('pointerleave', () => {
        card.style.removeProperty('--tilt-y');
        card.style.removeProperty('--tilt-x');
    });
});


// ===== 6. SMOOTH SCROLL FOR ANCHOR LINKS =====
onDomReady(() => {
    document.querySelectorAll('a[href^="#"]').forEach(link => {
        link.addEventListener('click', (e) => {
            const id = link.getAttribute('href');
            if (id === '#') return;
            const target = document.querySelector(id);
            if (target) {
                e.preventDefault();
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                // Close mobile nav if open
                const nav = document.getElementById('nav-menu');
                if (nav && nav.classList.contains('mobile-open') && typeof window.toggleMobileNav === 'function') {
                    window.toggleMobileNav(false);
                }
            }
        });
    });
});


// ===== 7. (removed in R-Phase 9.9) =====
// The whole-page fade-in set <body> to opacity 0 after the page had already painted (this
// file loads after window.load), which made every public page blink once. Removed.
