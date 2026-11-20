/**
 * Working day bar: the one date control for every tab (per person; stored in the session).
 * Changing it calls setGlobalShiftDate() (pipeline.js), which saves it and fires "shiftDateChanged";
 * every tab listens to that event and reloads for the new day.
 */
"use strict";

function workingDayIso() {
    const info = window.activeShiftDateInfo || {};
    if (info.date && /^\d{4}-\d{2}-\d{2}$/.test(info.date)) return info.date;
    const s = info.suffix || "";
    if (/^\d{6}$/.test(s)) return `20${s.slice(4, 6)}-${s.slice(2, 4)}-${s.slice(0, 2)}`;
    return "";
}

function workingDaySuffix() {
    const info = window.activeShiftDateInfo || {};
    if (info.suffix && /^\d{6}$/.test(info.suffix)) return info.suffix;
    const iso = workingDayIso();
    return iso ? `${iso.slice(8, 10)}${iso.slice(5, 7)}${iso.slice(2, 4)}` : "";
}

function setWorkingDay(value) {
    if (typeof window.setGlobalShiftDate === "function") void window.setGlobalShiftDate(value);
}

function shiftWorkingDay(deltaDays) {
    const iso = workingDayIso();
    const d = iso ? new Date(`${iso}T12:00:00`) : new Date();
    d.setDate(d.getDate() + deltaDays);
    const pad = n => String(n).padStart(2, "0");
    setWorkingDay(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`);
}

function renderDayBar() {
    const iso = workingDayIso();
    const input = document.getElementById("day-bar-input");
    const label = document.getElementById("day-bar-label");
    const todayBtn = document.getElementById("day-bar-today");
    if (input && iso) input.value = iso;
    const info = window.activeShiftDateInfo || {};
    if (label) {
        let text = "";
        if (iso) {
            const d = new Date(`${iso}T12:00:00`);
            text = d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
        }
        label.textContent = text ? (info.is_today ? `${text} · Today` : text) : "";
    }
    if (todayBtn) todayBtn.classList.toggle("is-today", !!info.is_today);
    const bar = document.getElementById("day-bar");
    if (bar) bar.classList.toggle("not-today", !info.is_today);
}

window.addEventListener("shiftDateChanged", renderDayBar);
document.addEventListener("DOMContentLoaded", () => {
    renderDayBar();
    // The initial day arrives with initGlobalShiftDate(); re-render once it has loaded.
    let tries = 0;
    const timer = setInterval(() => {
        tries += 1;
        if (workingDayIso() || tries > 40) {
            renderDayBar();
            clearInterval(timer);
        }
    }, 150);
    const input = document.getElementById("day-bar-input");
    if (input) {
        input.addEventListener("click", () => {
            try { if (typeof input.showPicker === "function") input.showPicker(); } catch (e) { /* not supported */ }
        });
    }
});

window.workingDayIso = workingDayIso;
window.workingDaySuffix = workingDaySuffix;
window.setWorkingDay = setWorkingDay;
window.shiftWorkingDay = shiftWorkingDay;
window.renderDayBar = renderDayBar;
