// util.js — shared escaping helpers. Load first on both pages.

/** HTML-escape a value for safe interpolation into text content / innerHTML. */
function esc(value) {
    return String(value == null ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

/**
 * Escape a value for use inside a single-quoted JS string literal that itself
 * lives in a double-quoted HTML attribute (e.g. onclick="f('...')").
 */
function escAttr(value) {
    return String(value == null ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/\\/g, "\\\\")
        .replace(/'/g, "\\'")
        .replace(/"/g, "&quot;")
        .replace(/\n/g, "\\n")
        .replace(/\r/g, "\\r");
}
