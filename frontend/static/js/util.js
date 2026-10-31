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

/**
 * Bind declarative data-action controls without putting behavior in templates.
 * Maps receive the action name and the handler is called with the event target.
 */
function bindActionHandlers({ click = {}, change = {}, input = {} } = {}) {
    const bind = (eventName, handlers) => {
        document.addEventListener(eventName, (event) => {
            const target = event.target instanceof Element
                ? event.target.closest('[data-action]')
                : null;
            const handler = target && handlers[target.dataset.action];
            if (handler) handler(event, target);
        });
    };

    bind('click', click);
    bind('change', change);
    bind('input', input);
}
