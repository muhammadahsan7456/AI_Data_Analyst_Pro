/**
 * AI Data Analyst Pro — Voice AI Assistant Suite (Feature 2, 3, 8)
 * Speech Recognition + Text-to-Speech Synthesis + Voice Upload Commands
 */

(function () {
    'use strict';

    let recognition = null;
    let isListening = false;
    let ttsVoice = null;

    // Initialize Speech Recognition
    function initSpeechRecognition() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            console.warn("Speech Recognition API is not supported in this browser.");
            return null;
        }

        let silenceTimer = null;
        let finalTranscript = "";

        const rec = new SpeechRecognition();
        rec.continuous = true;
        rec.interimResults = true;
        rec.lang = window.navigator.language || "en-US";

        rec.onstart = function () {
            isListening = true;
            finalTranscript = "";
            updateMicUI(true);
            showToast("🎤 Listening... Speak your complete question.", "info");
        };

        rec.onresult = function (event) {
            let interimTranscript = "";
            for (let i = event.resultIndex; i < event.results.length; ++i) {
                if (event.results[i].isFinal) {
                    finalTranscript += event.results[i][0].transcript + " ";
                } else {
                    interimTranscript += event.results[i][0].transcript;
                }
            }

            const currentText = (finalTranscript + interimTranscript).trim();
            const questionInput = document.getElementById("question-input") || document.getElementById("chat-prompt-input");
            if (questionInput && currentText) {
                questionInput.value = currentText;
            }

            // Reset 5.0s silence auto-submit timer (gives user ample time to pause & speak long multi-part prompts)
            if (silenceTimer) clearTimeout(silenceTimer);
            silenceTimer = setTimeout(function () {
                if (isListening && currentText.length > 0) {
                    rec.stop();
                }
            }, 5000);
        };

        rec.onerror = function (event) {
            isListening = false;
            if (silenceTimer) clearTimeout(silenceTimer);
            updateMicUI(false);
            if (event.error !== "no-speech") {
                console.error("Speech Recognition Error:", event.error);
                showToast(describeVoiceError(event.error), "warning");
            }
        };

        rec.onend = function () {
            isListening = false;
            if (silenceTimer) clearTimeout(silenceTimer);
            updateMicUI(false);
            const questionInput = document.getElementById("question-input") || document.getElementById("chat-prompt-input");
            const textToSubmit = questionInput ? questionInput.value.trim() : finalTranscript.trim();
            if (textToSubmit.length >= 2) {
                handleVoiceCommand(textToSubmit);
            }
        };

        return rec;
    }

    // Translate raw Web Speech API error codes into clear, actionable guidance.
    // A browser's microphone permission is a browser/OS-level security decision that no
    // website can override - the best a site can do is explain exactly what to click.
    function describeVoiceError(code) {
        switch (code) {
            case "not-allowed":
            case "service-not-allowed":
                return "🎤 Microphone access is blocked. Click the lock/site-info icon in your browser's address bar, allow Microphone access, then try again.";
            case "audio-capture":
                return "🎤 No microphone was found. Please connect a microphone and try again.";
            case "network":
                return "🎤 Voice recognition needs an internet connection. Please check your connection and try again.";
            case "aborted":
                return "🎤 Voice input was stopped.";
            default:
                return "🎤 Voice recognition couldn't start (" + code + "). You can still type your question instead.";
        }
    }

    // Initialize Text-To-Speech Voices
    function initTTS() {
        if (!("speechSynthesis" in window)) return;
        
        function loadVoices() {
            const voices = window.speechSynthesis.getVoices();
            ttsVoice = voices.find(v => v.lang.includes("en") && (v.name.includes("Natural") || v.name.includes("Google") || v.name.includes("Samantha"))) || voices[0];
        }

        loadVoices();
        if (window.speechSynthesis.onvoiceschanged !== undefined) {
            window.speechSynthesis.onvoiceschanged = loadVoices;
        }
    }

    // Speak Text Output (TTS)
    window.speakText = function (text) {
        if (!("speechSynthesis" in window) || !text) return;
        
        window.speechSynthesis.cancel(); // Stop ongoing speech

        const utterance = new SpeechSynthesisUtterance(text);
        if (ttsVoice) utterance.voice = ttsVoice;
        utterance.rate = 1.0;
        utterance.pitch = 1.0;

        window.speechSynthesis.speak(utterance);
    };

    // Handle Transcribed Speech Commands
    function handleVoiceCommand(transcript) {
        if (!transcript || transcript.trim().length === 0) return;

        const lower = transcript.toLowerCase();

        // 1. Voice Upload Trigger
        if (lower.includes("upload") || lower.includes("add dataset") || lower.includes("upload dataset") || lower.includes("upload file")) {
            speakText("Opening file upload modal.");
            const fileInput = document.getElementById("dataset-file-input") || document.querySelector('input[type="file"]');
            if (fileInput) {
                fileInput.click();
            } else {
                window.location.href = "/upload";
            }
            return;
        }

        // 2. Chat Input Auto-Populate & Instant AJAX Submit
        const questionInput = document.getElementById("question-input") || document.getElementById("chat-prompt-input");
        if (questionInput) {
            questionInput.value = transcript;
            showToast("✨ Voice Captured: '" + transcript + "'", "success");
            executeVoiceQueryAJAX(transcript);
        }
    }

    // Instant Sub-Second AJAX Execution Helper (No Page Reload Lag)
    window.executeVoiceQueryAJAX = function (question) {
        const datasetSelect = document.getElementById("dataset_id");
        const datasetId = datasetSelect ? datasetSelect.value : null;

        if (window.showLoading) {
            window.showLoading("⚡ AI Executing Query Locally...");
        }

        fetch("/chat?ajax=1", {
            method: "POST",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest"
            },
            body: new URLSearchParams({
                question: question,
                dataset_id: datasetId || ""
            })
        })
        .then(r => r.json())
        .then(data => {
            if (window.hideLoading) window.hideLoading();

            const container = document.getElementById("chat-results-container");
            if (!container) {
                const chatForm = document.getElementById("chat-form");
                if (chatForm) chatForm.submit();
                return;
            }

            if (data.is_out_of_domain) {
                container.innerHTML = `
                    <div class="table-card" style="border-left: 4px solid var(--accent-amber); background-color: #fffbeb; margin-top: 24px;">
                        <h3 style="color: #b45309; font-size: 18px; font-weight: 700; margin-bottom: 8px; display: flex; align-items: center; gap: 8px;">
                            <i class="fa-solid fa-shield-halved"></i> AI Context Guardrail
                        </h3>
                        <p style="color: #92400e; font-size: 16px; font-weight: 600;">${data.error}</p>
                    </div>
                `;
                if (data.tts_speech) speakText(data.tts_speech);
                return;
            }

            if (data.success) {
                let html = `
                    <div style="display: flex; gap: 12px; margin-top: 24px; margin-bottom: 24px; flex-wrap: wrap;">
                        <div class="badge badge-info" style="font-size: 13px; padding: 6px 12px;">⚡ Execution Time: <strong>${data.execution_time_ms} ms</strong></div>
                        <div class="badge badge-success" style="font-size: 13px; padding: 6px 12px;">🎯 Confidence Score: <strong>${Math.round(data.confidence * 100)}%</strong></div>
                        <div class="badge badge-info" style="font-size: 13px; padding: 6px 12px;">📊 Rows Returned: <strong>${data.rows_returned}</strong></div>
                    </div>
                `;

                if (data.sql) {
                    html += `
                        <div class="table-card" style="border-left: 4px solid var(--accent-purple);">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                                <h3 style="font-size: 16px; font-weight: 700; color: var(--accent-purple);"><i class="fa-solid fa-code"></i> Local T-SQL Query</h3>
                                <button class="btn btn-secondary btn-sm" onclick="navigator.clipboard.writeText(document.getElementById('sql-text').innerText); showAppToast('SQL Copied!', 'success');"><i class="fa-solid fa-copy"></i> Copy SQL</button>
                            </div>
                            <pre id="sql-text" style="background: var(--bg-sidebar); color: #38bdf8; padding: 16px; border-radius: var(--radius-md); font-family: monospace; font-size: 14px; overflow-x: auto;">${data.sql}</pre>
                        </div>
                    `;
                }

                if (data.result_html) {
                    html += `
                        <div class="table-card">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                                <h3 style="font-size: 16px; font-weight: 700;"><i class="fa-solid fa-table"></i> Query Results</h3>
                                <div style="display: flex; gap: 8px;">
                                    <a href="/query/export/csv" class="btn btn-secondary btn-sm"><i class="fa-solid fa-file-csv"></i> CSV</a>
                                    <a href="/query/export/excel" class="btn btn-secondary btn-sm"><i class="fa-solid fa-file-excel"></i> Excel</a>
                                    <a href="/query/export/pdf" class="btn btn-secondary btn-sm"><i class="fa-solid fa-file-pdf"></i> PDF</a>
                                </div>
                            </div>
                            <div class="table-container">${data.result_html}</div>
                        </div>
                    `;
                }

                if (data.explanation) {
                    const formattedExplanation = formatAIExplanationJS(data.explanation);
                    html += `
                        <div class="table-card" style="border-top: 4px solid var(--accent-blue); background: var(--bg-surface); padding: 24px; box-shadow: var(--shadow-md);">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--border-color);">
                                <h3 style="font-size: 18px; font-weight: 800; color: var(--accent-blue); display: flex; align-items: center; gap: 10px;">
                                    <i class="fa-solid fa-wand-magic-sparkles"></i> Executive AI Summary & Business Insights
                                </h3>
                                <span class="badge badge-info" style="font-size: 11px; padding: 4px 10px;">PRO ANALYSIS ENGINE</span>
                            </div>
                            <div class="ai-explanation-content">${formattedExplanation}</div>
                        </div>
                    `;
                }

                if (data.chart_spec || data.chart) {
                    window.currentChartSpec = data.chart_spec || {};
                    const chartSrc = data.chart ? `/static/${data.chart.replace(/^static\//, '')}` : '#';
                    html += `
                        <div class="table-card glass-card" style="border-top: 4px solid var(--accent-purple); background: var(--bg-surface); padding: 24px; box-shadow: var(--shadow-lg); border-radius: var(--radius-lg); margin-top: 24px;">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; padding-bottom: 14px; border-bottom: 1px solid var(--border-color); flex-wrap: wrap; gap: 12px;">
                                <h3 style="font-size: 18px; font-weight: 800; color: var(--accent-purple); display: flex; align-items: center; gap: 10px; margin: 0;">
                                    <i class="fa-solid fa-chart-column" style="font-size: 20px;"></i> Executive AI Interactive Analytics & Chart Visualization
                                </h3>
                                <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
                                    <span class="badge badge-success" style="font-size: 11px; padding: 6px 12px; background: rgba(52,211,153,0.15); border: 1px solid #34d399; color: #34d399;">
                                        <i class="fa-solid fa-wand-magic-sparkles"></i> Hover Data Active
                                    </span>
                                    <a href="${chartSrc}" download="AI_Chart_Analysis.png" class="btn btn-secondary btn-sm" style="font-size: 12px; font-weight: 700; padding: 6px 14px;">
                                        <i class="fa-solid fa-download"></i> Download HD Chart
                                    </a>
                                </div>
                            </div>

                            <div class="chart-type-switcher-bar" style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; padding: 10px 14px; background: rgba(15, 23, 42, 0.8); border-radius: 12px; border: 1px solid rgba(255,255,255,0.08); align-items: center;">
                                <span style="font-size: 12px; font-weight: 800; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.5px; margin-right: 6px; display: flex; align-items: center; gap: 6px;">
                                    <i class="fa-solid fa-sliders"></i> Switch Chart Format:
                                </span>
                                <button type="button" class="chart-mode-btn active" data-mode="pie" onclick="selectVisualChartMode('pie', this)" style="padding: 6px 14px; font-size: 12px; font-weight: 700; border-radius: 20px; border: 1px solid #38bdf8; background: linear-gradient(135deg, #2563eb, #7c3aed); color: #ffffff; cursor: pointer; transition: all 0.2s ease;">
                                    🍕 Pie Chart (Gol)
                                </button>
                                <button type="button" class="chart-mode-btn" data-mode="donut" onclick="selectVisualChartMode('donut', this)" style="padding: 6px 14px; font-size: 12px; font-weight: 700; border-radius: 20px; border: 1px solid var(--border-color); background: rgba(30, 41, 59, 0.7); color: #cbd5e1; cursor: pointer; transition: all 0.2s ease;">
                                    🍩 Donut Ring
                                </button>
                                <button type="button" class="chart-mode-btn" data-mode="bar" onclick="selectVisualChartMode('bar', this)" style="padding: 6px 14px; font-size: 12px; font-weight: 700; border-radius: 20px; border: 1px solid var(--border-color); background: rgba(30, 41, 59, 0.7); color: #cbd5e1; cursor: pointer; transition: all 0.2s ease;">
                                    📊 Vertical Bar
                                </button>
                                <button type="button" class="chart-mode-btn" data-mode="horizontal_bar" onclick="selectVisualChartMode('horizontal_bar', this)" style="padding: 6px 14px; font-size: 12px; font-weight: 700; border-radius: 20px; border: 1px solid var(--border-color); background: rgba(30, 41, 59, 0.7); color: #cbd5e1; cursor: pointer; transition: all 0.2s ease;">
                                    📈 Horizontal Bar
                                </button>
                                <button type="button" class="chart-mode-btn" data-mode="line" onclick="selectVisualChartMode('line', this)" style="padding: 6px 14px; font-size: 12px; font-weight: 700; border-radius: 20px; border: 1px solid var(--border-color); background: rgba(30, 41, 59, 0.7); color: #cbd5e1; cursor: pointer; transition: all 0.2s ease;">
                                    📉 Line Chart (Trend)
                                </button>
                                <button type="button" class="chart-mode-btn" data-mode="area" onclick="selectVisualChartMode('area', this)" style="padding: 6px 14px; font-size: 12px; font-weight: 700; border-radius: 20px; border: 1px solid var(--border-color); background: rgba(30, 41, 59, 0.7); color: #cbd5e1; cursor: pointer; transition: all 0.2s ease;">
                                    🌊 Area Chart
                                </button>
                                <button type="button" class="chart-mode-btn" data-mode="scatter" onclick="selectVisualChartMode('scatter', this)" style="padding: 6px 14px; font-size: 12px; font-weight: 700; border-radius: 20px; border: 1px solid var(--border-color); background: rgba(30, 41, 59, 0.7); color: #cbd5e1; cursor: pointer; transition: all 0.2s ease;">
                                    🎯 Scatter Plot
                                </button>
                                <button type="button" class="chart-mode-btn" data-mode="heatmap" onclick="selectVisualChartMode('heatmap', this)" style="padding: 6px 14px; font-size: 12px; font-weight: 700; border-radius: 20px; border: 1px solid var(--border-color); background: rgba(30, 41, 59, 0.7); color: #cbd5e1; cursor: pointer; transition: all 0.2s ease;">
                                    🔥 Heatmap
                                </button>
                                <button type="button" class="chart-mode-btn" data-mode="treemap" onclick="selectVisualChartMode('treemap', this)" style="padding: 6px 14px; font-size: 12px; font-weight: 700; border-radius: 20px; border: 1px solid var(--border-color); background: rgba(30, 41, 59, 0.7); color: #cbd5e1; cursor: pointer; transition: all 0.2s ease;">
                                    🧱 Treemap
                                </button>
                                <button type="button" class="chart-mode-btn" data-mode="funnel" onclick="selectVisualChartMode('funnel', this)" style="padding: 6px 14px; font-size: 12px; font-weight: 700; border-radius: 20px; border: 1px solid var(--border-color); background: rgba(30, 41, 59, 0.7); color: #cbd5e1; cursor: pointer; transition: all 0.2s ease;">
                                    🔻 Funnel Flow
                                </button>
                            </div>

                            <div id="interactive-plotly-chart" style="width: 100%; min-height: 480px; height: 500px; display: block; border-radius: var(--radius-md); background: rgba(15, 23, 42, 0.6); padding: 12px; box-shadow: inset 0 2px 10px rgba(0,0,0,0.5);"></div>
                            <img id="static-chart-image" src="${chartSrc}" alt="Generated Chart" style="max-width: 100%; height: auto; border-radius: var(--radius-md); box-shadow: var(--shadow-md); display: none; margin: 0 auto;">
                        </div>
                    `;
                }

                container.innerHTML = html;

                if (data.chart_spec && data.chart_spec.labels && data.chart_spec.labels.length > 0) {
                    setTimeout(() => {
                        if (window.updateCompatibleChartDropdown) window.updateCompatibleChartDropdown(data.chart_spec);
                        if (window.renderInteractivePlotlyChart) window.renderInteractivePlotlyChart(data.chart_spec, data.chart_spec.chart_type || "pie");
                    }, 50);
                }

                if (data.tts_speech) {
                    speakText(data.tts_speech);
                }
            } else {
                showToast("⚠️ " + (data.error || "Query execution failed."), "error");
            }
        })
        .catch(err => {
            if (window.hideLoading) window.hideLoading();
            console.error("Voice AJAX execution error:", err);
            const chatForm = document.getElementById("chat-form");
            if (chatForm) chatForm.submit();
        });
    };

    // Toggle Mic Listening
    window.toggleVoiceRecognition = function () {
        if (!recognition) {
            recognition = initSpeechRecognition();
        }

        if (!recognition) {
            alert("Speech recognition is not supported on your current browser. Please use Chrome, Edge, or Safari.");
            return;
        }

        if (isListening) {
            recognition.stop();
            return;
        }

        function startRecognition() {
            try {
                recognition.start();
            } catch (err) {
                console.error(err);
            }
        }

        // Check known permission state first (not supported in every browser, and some
        // browsers throw synchronously rather than rejecting the promise for an
        // unrecognized permission name - wrapped so any failure here still falls back
        // to just starting recognition instead of silently breaking the button).
        try {
            if (navigator.permissions && navigator.permissions.query) {
                navigator.permissions.query({ name: "microphone" })
                    .then(status => {
                        if (status.state === "denied") {
                            showToast(describeVoiceError("not-allowed"), "warning");
                        } else {
                            startRecognition();
                        }
                    })
                    .catch(startRecognition);
            } else {
                startRecognition();
            }
        } catch (permErr) {
            startRecognition();
        }
    };

    // Update Microphone Button Visual State
    function updateMicUI(listening) {
        const micBtns = document.querySelectorAll(".btn-voice-mic, #mic-btn");
        micBtns.forEach(btn => {
            const span = btn.querySelector("span");
            const icon = btn.querySelector("i");
            if (listening) {
                btn.classList.add("listening", "pulse-animation");
                btn.style.background = "linear-gradient(135deg, #ef4444, #dc2626)";
                btn.style.boxShadow = "0 0 0 10px rgba(239, 68, 68, 0.3)";
                if (span) span.textContent = "Listening... Speak Now!";
                if (icon) icon.className = "fa-solid fa-microphone-slash";
            } else {
                btn.classList.remove("listening", "pulse-animation");
                btn.style.background = "linear-gradient(135deg, var(--accent-blue), var(--accent-purple))";
                btn.style.boxShadow = "0 4px 15px rgba(37, 99, 235, 0.4)";
                if (span) span.textContent = "Click to Speak (Voice AI)";
                if (icon) icon.className = "fa-solid fa-microphone-lines";
            }
        });
    }

    // Toast Notification Utility
    function showToast(message, type) {
        if (window.showAppToast) {
            window.showAppToast(message, type);
        } else {
            console.log(`[Toast ${type}]: ${message}`);
        }
    }

    // Auto-initialize on DOM load
    document.addEventListener("DOMContentLoaded", function () {
        recognition = initSpeechRecognition();
        initTTS();

        // Attach listener to any element with data-voice-trigger or #mic-btn
        document.querySelectorAll(".btn-voice-mic, #mic-btn").forEach(btn => {
            btn.addEventListener("click", function (e) {
                e.preventDefault();
                e.stopPropagation();
                toggleVoiceRecognition();
            });
        });

        // Intercept Chat Form Submit to run via AJAX without page reload lag
        const chatForm = document.getElementById("chat-form");
        if (chatForm) {
            chatForm.addEventListener("submit", function (e) {
                e.preventDefault();
                e.stopPropagation();
                const questionInput = document.getElementById("question-input");
                const question = questionInput ? questionInput.value.trim() : "";
                if (question) {
                    executeVoiceQueryAJAX(question);
                }
            });
        }

        // If AI response text exists on chat page, speak natural TTS answer
        const aiSpeechElement = document.getElementById("ai-speech-text");
        if (aiSpeechElement && aiSpeechElement.dataset.speech) {
            speakText(aiSpeechElement.dataset.speech);
        }
    });

})();

function formatAIExplanationJS(text) {
    if (!text) return "";
    if (text.includes("border-left:") || text.includes("<div style=")) {
        return text;
    }

    let html = "";
    const sections = text.split("\n\n").map(s => s.strip ? s.strip() : s.trim()).filter(s => s);

    sections.forEach(sec => {
        let lines = sec.split("\n").map(l => l.trim()).filter(l => l);
        if (!lines.length) return;

        let head = lines[0];
        let bodyLines = lines.slice(1);

        if (head.includes("📊") || head.includes("Data Scope")) {
            let cleanHead = head.replace(/[\*\#📊]/g, "").replace(/Data Scope & Executive Overview:?/g, "").replace(/Data Scope & Business Purpose:?/g, "").trim();
            let cleanBody = bodyLines.map(l => l.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')).join("<br>");
            let fullText = (cleanHead ? cleanHead + " " : "") + cleanBody;
            html += `
                <div style="background: rgba(37,99,235,0.06); border: 1px solid rgba(37,99,235,0.2); border-left: 4px solid var(--accent-blue); padding: 16px 20px; border-radius: var(--radius-md); margin-bottom: 16px; box-shadow: var(--shadow-sm);">
                    <div style="font-weight: 800; font-size: 15px; color: var(--accent-blue); margin-bottom: 6px; display: flex; align-items: center; gap: 8px;">
                        <i class="fa-solid fa-chart-pie" style="font-size: 16px;"></i> Data Scope & Executive Overview
                    </div>
                    <div style="font-size: 14px; color: var(--text-primary); line-height: 1.6;">${fullText}</div>
                </div>
            `;
        } else if (head.includes("📝") || head.includes("Record Synthesis") || head.includes("Key Findings")) {
            let cleanHead = head.replace(/[\*\#📝]/g, "").replace(/Detailed Record Synthesis & Key Findings:?/g, "").trim();
            let bullets = bodyLines.map(l => {
                let cleanL = l.replace(/^[•\-\s]+/, "").replace(/\*\*(.*?)\*\*/g, '<strong style="color: #6366f1;">$1</strong>');
                return `
                    <div style="display: flex; align-items: flex-start; gap: 10px; margin-bottom: 6px; padding: 8px 12px; background: rgba(99,102,241,0.05); border-radius: var(--radius-sm); border: 1px solid rgba(99,102,241,0.2);">
                        <i class="fa-solid fa-list-check" style="color: #6366f1; margin-top: 4px; font-size: 12px;"></i>
                        <div style="font-size: 13.5px; color: var(--text-primary); flex: 1;">${cleanL}</div>
                    </div>
                `;
            }).join("");

            html += `
                <div style="background: var(--bg-surface); border: 1px solid var(--border-color); border-left: 4px solid #6366f1; padding: 16px 20px; border-radius: var(--radius-md); margin-bottom: 16px;">
                    <div style="font-weight: 800; font-size: 15px; color: #6366f1; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">
                        <i class="fa-solid fa-square-poll-vertical" style="font-size: 16px;"></i> ${cleanHead || "Detailed Record Synthesis & Key Findings"}
                    </div>
                    ${bullets}
                </div>
            `;
        } else if (head.includes("🔍") || head.includes("Dimensions")) {
            let cleanHead = head.replace(/[\*\#🔍]/g, "").replace(/Category Breakdown & Top Performers:?/g, "").replace(/Key Dimensions & Sample Values:?/g, "").trim();
            let bullets = bodyLines.map(l => {
                let cleanL = l.replace(/^[•\-\s]+/, "").replace(/\*\*(.*?)\*\*/g, '<strong style="color: var(--accent-purple);">$1</strong>');
                return `
                    <div style="display: flex; align-items: flex-start; gap: 10px; margin-bottom: 6px; padding: 8px 12px; background: var(--bg-surface); border-radius: var(--radius-sm); border: 1px solid var(--border-color);">
                        <i class="fa-solid fa-magnifying-glass" style="color: var(--accent-purple); margin-top: 4px; font-size: 11px;"></i>
                        <div style="font-size: 13.5px; color: var(--text-primary); flex: 1;">${cleanL}</div>
                    </div>
                `;
            }).join("");

            html += `
                <div style="background: var(--bg-primary); border: 1px solid var(--border-color); border-left: 4px solid var(--accent-purple); padding: 16px 20px; border-radius: var(--radius-md); margin-bottom: 16px;">
                    <div style="font-weight: 800; font-size: 15px; color: var(--accent-purple); margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">
                        <i class="fa-solid fa-layer-group" style="font-size: 16px;"></i> ${cleanHead || "Category Breakdown & Top Performers"}
                    </div>
                    ${bullets}
                </div>
            `;
        } else if (head.includes("📈") || head.includes("Metrics")) {
            let cleanHead = head.replace(/[\*\#📈]/g, "").replace(/Aggregated Financial & Operational Metrics:?/g, "").replace(/Aggregated Business Metrics:?/g, "").trim();
            let pills = bodyLines.map(l => {
                let cleanL = l.replace(/^[•\-\s]+/, "").replace(/\*\*(.*?)\*\*/g, '<strong style="color: var(--accent-green);">$1</strong>');
                return `
                    <div style="display: flex; align-items: flex-start; gap: 10px; margin-bottom: 6px; padding: 8px 14px; background: rgba(16,185,129,0.06); border-radius: var(--radius-sm); border: 1px solid rgba(16,185,129,0.2);">
                        <i class="fa-solid fa-arrow-trend-up" style="color: var(--accent-green); margin-top: 4px; font-size: 12px;"></i>
                        <div style="font-size: 13.5px; color: var(--text-primary); flex: 1;">${cleanL}</div>
                    </div>
                `;
            }).join("");

            html += `
                <div style="background: var(--bg-surface); border: 1px solid var(--border-color); border-left: 4px solid var(--accent-green); padding: 16px 20px; border-radius: var(--radius-md); margin-bottom: 16px;">
                    <div style="font-weight: 800; font-size: 15px; color: var(--accent-green); margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">
                        <i class="fa-solid fa-calculator" style="font-size: 16px;"></i> ${cleanHead || "Aggregated Business Metrics"}
                    </div>
                    ${pills}
                </div>
            `;
        } else if (head.includes("💡") || head.includes("Insight")) {
            let cleanHead = head.replace(/[\*\#💡]/g, "").replace(/Operational Insight:?/g, "").trim();
            let cleanBody = bodyLines.map(l => l.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')).join("<br>");
            let fullText = (cleanHead ? cleanHead + " " : "") + cleanBody;
            html += `
                <div style="background: rgba(245,158,11,0.06); border: 1px solid rgba(245,158,11,0.25); border-left: 4px solid var(--accent-amber); padding: 16px 20px; border-radius: var(--radius-md); margin-bottom: 16px;">
                    <div style="font-weight: 800; font-size: 15px; color: var(--accent-amber); margin-bottom: 6px; display: flex; align-items: center; gap: 8px;">
                        <i class="fa-solid fa-lightbulb" style="font-size: 16px;"></i> Operational Insight & Strategy
                    </div>
                    <div style="font-size: 14px; color: var(--text-primary); line-height: 1.6;">${fullText}</div>
                </div>
            `;
        } else {
            let cleanL = sec.replace(/\*\*(.*?)\*\*/g, '<strong style="color: var(--accent-blue);">$1</strong>');
            html += `<div style="margin-bottom: 10px; font-size: 14px; line-height: 1.6;">${cleanL}</div>`;
        }
    });

    return html;
}
