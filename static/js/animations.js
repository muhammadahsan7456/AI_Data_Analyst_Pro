/**
 * AI Data Analyst Pro — Shared Animation Engine
 * Three.js 3D constellation, scroll reveals, animated counters, typed text
 */

// ===== 1. THREE.JS 3D NEURAL NETWORK CONSTELLATION =====
(function init3DConstellation() {
    const canvas = document.getElementById('hero-3d-canvas');
    if (!canvas || typeof THREE === 'undefined') return;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 1, 1000);
    camera.position.z = 420;

    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    // Palettes: Subtle, elegant luxury tones for Dark & Light modes
    const darkPalette = [
        [0.984, 0.749, 0.141],  // Champagne Gold (#fbbf24)
        [0.659, 0.333, 0.969],  // Royal Violet (#a855f7)
        [0.220, 0.741, 0.973],  // Diamond Cyan (#38bdf8)
        [0.961, 0.620, 0.043],  // Amber Gold (#f59e0b)
        [0.996, 0.941, 0.541],  // Platinum Light (#fef08a)
    ];

    const lightPalette = [
        [0.850, 0.550, 0.150],  // Warm Amber Gold (#d97706)
        [0.550, 0.400, 0.850],  // Soft Lavender Violet (#8b5cf6)
        [0.200, 0.600, 0.850],  // Soft Diamond Cyan (#38bdf8)
        [0.450, 0.500, 0.600],  // Soft Slate Steel (#64748b)
        [0.780, 0.450, 0.100],  // Warm Champagne Bronze (#b45309)
    ];

    // Particle system (clean & delicate count)
    const particleCount = 110;
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(particleCount * 3);
    const colors = new Float32Array(particleCount * 3);
    const velocities = [];
    const particleColorIndices = [];

    for (let i = 0; i < particleCount; i++) {
        positions[i * 3]     = (Math.random() - 0.5) * 920;
        positions[i * 3 + 1] = (Math.random() - 0.5) * 620;
        positions[i * 3 + 2] = (Math.random() - 0.5) * 500;

        const idx = Math.floor(Math.random() * darkPalette.length);
        particleColorIndices.push(idx);

        velocities.push({
            x: (Math.random() - 0.5) * 0.32,
            y: (Math.random() - 0.5) * 0.32,
            z: (Math.random() - 0.5) * 0.22
        });
    }

    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const pMaterial = new THREE.PointsMaterial({
        size: 3.6,
        transparent: true,
        opacity: 0.85,
        vertexColors: true,
        sizeAttenuation: true
    });

    const pointCloud = new THREE.Points(geometry, pMaterial);
    scene.add(pointCloud);

    // Connection lines
    const lineGeometry = new THREE.BufferGeometry();
    const lineMaterial = new THREE.LineBasicMaterial({
        color: 0xf59e0b,
        transparent: true,
        opacity: 0.20
    });
    const linesMesh = new THREE.LineSegments(lineGeometry, lineMaterial);
    scene.add(linesMesh);

    // Floating data cubes
    const cubeGroup = new THREE.Group();
    const cubeMaterial = new THREE.MeshBasicMaterial({
        color: 0xfbbf24,
        transparent: true,
        opacity: 0.14,
        wireframe: true
    });

    const cubes = [];
    for (let i = 0; i < 6; i++) {
        const size = 15 + Math.random() * 24;
        const cubeGeo = new THREE.BoxGeometry(size, size, size);
        const cube = new THREE.Mesh(cubeGeo, cubeMaterial.clone());
        cube.position.set(
            (Math.random() - 0.5) * 720,
            (Math.random() - 0.5) * 420,
            (Math.random() - 0.5) * 300
        );
        cube.rotation.set(Math.random() * Math.PI, Math.random() * Math.PI, 0);
        cube.userData = {
            rotSpeed: { x: 0.003 + Math.random() * 0.004, y: 0.003 + Math.random() * 0.004 },
            floatPhase: Math.random() * Math.PI * 2,
            floatSpeed: 0.004 + Math.random() * 0.008,
            floatAmp: 14 + Math.random() * 18,
            baseY: cube.position.y
        };
        cubes.push(cube);
        cubeGroup.add(cube);
    }
    scene.add(cubeGroup);

    // Dynamic Theme Adaptation (Soft & subtle matching in both modes)
    function applyTheme(theme) {
        const isLight = (theme === 'light');
        const activePalette = isLight ? lightPalette : darkPalette;

        const cols = pointCloud.geometry.attributes.color.array;
        for (let i = 0; i < particleCount; i++) {
            const c = activePalette[particleColorIndices[i]];
            cols[i * 3]     = c[0];
            cols[i * 3 + 1] = c[1];
            cols[i * 3 + 2] = c[2];
        }
        pointCloud.geometry.attributes.color.needsUpdate = true;

        // Keep delicate, balanced size in both modes
        pMaterial.size = isLight ? 3.8 : 3.6;
        pMaterial.opacity = isLight ? 0.78 : 0.85;
        pMaterial.needsUpdate = true;

        // Subtle, delicate lines that match dark mode's calmness
        lineMaterial.color.setHex(isLight ? 0xd97706 : 0xf59e0b);
        lineMaterial.opacity = isLight ? 0.18 : 0.20;
        lineMaterial.needsUpdate = true;

        // Subtle wireframe cubes
        cubes.forEach(cube => {
            cube.material.color.setHex(isLight ? 0xd97706 : 0xfbbf24);
            cube.material.opacity = isLight ? 0.14 : 0.14;
            cube.material.needsUpdate = true;
        });
    }

    // Apply initial theme
    const initialTheme = document.documentElement.getAttribute('data-theme') || localStorage.getItem('website_theme') || 'dark';
    applyTheme(initialTheme);

    // Listen for theme changes via MutationObserver and custom events
    const observer = new MutationObserver((mutations) => {
        mutations.forEach(m => {
            if (m.attributeName === 'data-theme') {
                const newTheme = document.documentElement.getAttribute('data-theme') || 'dark';
                applyTheme(newTheme);
            }
        });
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    window.addEventListener('websiteThemeChanged', (e) => {
        if (e.detail && e.detail.theme) {
            applyTheme(e.detail.theme);
        }
    });

    let mouseX = 0, mouseY = 0;
    let time = 0;

    document.addEventListener('mousemove', (e) => {
        mouseX = (e.clientX - window.innerWidth / 2) * 0.04;
        mouseY = (e.clientY - window.innerHeight / 2) * 0.04;
    });

    function animate() {
        requestAnimationFrame(animate);
        time += 0.01;

        const pos = pointCloud.geometry.attributes.position.array;
        for (let i = 0; i < particleCount; i++) {
            pos[i * 3]     += velocities[i].x;
            pos[i * 3 + 1] += velocities[i].y;
            pos[i * 3 + 2] += velocities[i].z;

            if (Math.abs(pos[i * 3]) > 470) velocities[i].x *= -1;
            if (Math.abs(pos[i * 3 + 1]) > 320) velocities[i].y *= -1;
            if (Math.abs(pos[i * 3 + 2]) > 260) velocities[i].z *= -1;
        }
        pointCloud.geometry.attributes.position.needsUpdate = true;

        // Build connection lines
        const linePositions = [];
        for (let i = 0; i < particleCount; i++) {
            for (let j = i + 1; j < particleCount; j++) {
                const dx = pos[i * 3] - pos[j * 3];
                const dy = pos[i * 3 + 1] - pos[j * 3 + 1];
                const dz = pos[i * 3 + 2] - pos[j * 3 + 2];
                const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
                if (dist < 112) {
                    linePositions.push(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]);
                    linePositions.push(pos[j * 3], pos[j * 3 + 1], pos[j * 3 + 2]);
                }
            }
        }
        lineGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(linePositions), 3));

        // Animate cubes
        cubeGroup.children.forEach(cube => {
            cube.rotation.x += cube.userData.rotSpeed.x;
            cube.rotation.y += cube.userData.rotSpeed.y;
            cube.position.y = cube.userData.baseY + Math.sin(time * cube.userData.floatSpeed * 60 + cube.userData.floatPhase) * cube.userData.floatAmp;
        });

        // Smooth camera follow mouse
        camera.position.x += (mouseX - camera.position.x) * 0.02;
        camera.position.y += (-mouseY - camera.position.y) * 0.02;
        camera.lookAt(scene.position);

        renderer.render(scene, camera);
    }

    animate();

    window.addEventListener('resize', () => {
        camera.aspect = window.innerWidth / window.innerHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(window.innerWidth, window.innerHeight);
    });
})();


// ===== 2. SCROLL REVEAL OBSERVER =====
(function initScrollReveal() {
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('revealed');
                // Don't unobserve so elements don't reset
            }
        });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });

    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('.reveal, .reveal-left, .reveal-right, .reveal-scale').forEach(el => {
            observer.observe(el);
        });
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

    document.addEventListener('DOMContentLoaded', () => {
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


// ===== 5. VANILLA TILT INITIALIZATION =====
document.addEventListener('DOMContentLoaded', () => {
    if (typeof VanillaTilt !== 'undefined') {
        document.querySelectorAll('[data-tilt]').forEach(el => {
            VanillaTilt.init(el, {
                max: 8,
                speed: 400,
                glare: true,
                'max-glare': 0.15,
                gyroscope: true
            });
        });
    }
});


// ===== 6. SMOOTH SCROLL FOR ANCHOR LINKS =====
document.addEventListener('DOMContentLoaded', () => {
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
                if (nav) nav.classList.remove('mobile-open');
            }
        });
    });
});


// ===== 7. PAGE LOAD FADE-IN =====
document.addEventListener('DOMContentLoaded', () => {
    document.body.style.opacity = '0';
    document.body.style.transition = 'opacity 0.5s ease';
    requestAnimationFrame(() => {
        document.body.style.opacity = '1';
    });
});
