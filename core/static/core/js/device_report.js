/**
 * Device report: once per browser session, tells the server what this device is
 * (screen, time zone, languages, CPU cores, memory, GPU, touch, network, OS details).
 * Used by the administrator's device list; never blocks the page.
 */
(function () {
    "use strict";
    const FLAG = "close_device_reported_v1";
    try { if (sessionStorage.getItem(FLAG)) return; } catch (e) { /* storage blocked: report anyway */ }

    function gpu() {
        try {
            const c = document.createElement("canvas");
            const gl = c.getContext("webgl") || c.getContext("experimental-webgl");
            if (!gl) return "";
            const ext = gl.getExtension("WEBGL_debug_renderer_info");
            return ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : String(gl.getParameter(gl.RENDERER));
        } catch (e) { return ""; }
    }

    async function collect() {
        const n = navigator, s = window.screen || {};
        const conn = n.connection || n.mozConnection || n.webkitConnection;
        const report = {
            screen: `${s.width || 0}x${s.height || 0}`,
            viewport: `${window.innerWidth}x${window.innerHeight}`,
            pixel_ratio: window.devicePixelRatio || 1,
            color_depth: s.colorDepth || "",
            orientation: (s.orientation && s.orientation.type) || "",
            timezone: (Intl.DateTimeFormat().resolvedOptions().timeZone) || "",
            utc_offset_minutes: -new Date().getTimezoneOffset(),
            languages: (n.languages || [n.language]).join(","),
            platform: n.platform || "",
            hardware_concurrency: n.hardwareConcurrency || "",
            device_memory_gb: n.deviceMemory || "",
            max_touch_points: n.maxTouchPoints || 0,
            gpu: gpu(),
            connection: conn ? (conn.effectiveType || conn.type || "") : "",
            downlink_mbps: conn && conn.downlink ? conn.downlink : "",
            standalone_pwa: window.matchMedia && window.matchMedia("(display-mode: standalone)").matches ? "1" : "0",
            cookies_enabled: n.cookieEnabled ? "1" : "0",
        };
        if (n.userAgentData && n.userAgentData.getHighEntropyValues) {
            try {
                const h = await n.userAgentData.getHighEntropyValues(["platform", "platformVersion", "model", "architecture", "bitness"]);
                report.ua_platform = h.platform || "";
                report.ua_platform_version = h.platformVersion || "";
                report.ua_model = h.model || "";
                report.ua_arch = h.architecture || "";
                report.ua_bitness = h.bitness || "";
            } catch (e) { /* not available */ }
        }
        return report;
    }

    function csrf() {
        const m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.content : "";
    }

    async function send() {
        try {
            const res = await fetch("/api/me/device-info/", {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
                body: JSON.stringify(await collect()),
            });
            if (res.ok) { try { sessionStorage.setItem(FLAG, "1"); } catch (e) { /* ignore */ } }
        } catch (e) { /* offline or blocked: try again next page load */ }
    }

    if (document.readyState === "complete") setTimeout(send, 1500);
    else window.addEventListener("load", () => setTimeout(send, 1500));
})();
