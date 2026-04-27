// Theme Manager - Loads themes from JSON files, persists via server preferences
const ThemeManager = {
    THEMES_DIR: '/static/themes',
    currentTheme: 'default',
    _themeCache: {},

    async init() {
        // Load all theme definitions once
        const themeNames = ['default', 'vscode-dark', 'secrets'];
        for (const name of themeNames) {
            try {
                const response = await fetch(`${this.THEMES_DIR}/${name}.json`);
                if (response.ok) {
                    this._themeCache[name] = await response.json();
                }
            } catch (e) {
                console.warn(`[Theme] Failed to load theme: ${name}`);
            }
        }

        // Populate all theme selector dropdowns
        for (const selectId of ['themeSelector', 'streamThemeSelector']) {
            this._populateSelect(selectId);
        }

        // Load saved theme from server preferences
        const prefs = await this._loadServerPrefs();
        const themeName = prefs.theme || 'default';
        await this.applyTheme(themeName);
    },

    async _loadServerPrefs() {
        try {
            const resp = await fetch('/api/audiobooks/preferences/get');
            return resp.ok ? await resp.json() : {};
        } catch (e) {
            console.error('[Theme] Failed to load preferences:', e);
            return {};
        }
    },

    async _saveServerPrefs(prefs) {
        try {
            await fetch('/api/audiobooks/preferences/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(prefs)
            });
        } catch (e) {
            console.error('[Theme] Failed to save preferences:', e);
        }
    },

    async applyTheme(themeName) {
        const theme = this._themeCache[themeName];
        if (!theme) {
            console.error(`[Theme] Theme not cached: ${themeName}`);
            return;
        }

        this.currentTheme = themeName;

        const root = document.documentElement;
        for (const [prop, value] of Object.entries(theme.variables)) {
            root.style.setProperty(prop, value);
        }

        // Update all theme selector dropdowns
        for (const selectId of ['themeSelector', 'streamThemeSelector']) {
            const select = document.getElementById(selectId);
            if (select) select.value = themeName;
        }

        // Save to server preferences
        const prefs = await this._loadServerPrefs();
        prefs.theme = themeName;
        await this._saveServerPrefs(prefs);

        console.log(`[Theme] Applied: ${theme.name} (${themeName})`);
    },

    _populateSelect(selectId) {
        const select = document.getElementById(selectId);
        if (!select) return;

        select.innerHTML = '';
        for (const [name, theme] of Object.entries(this._themeCache)) {
            const option = document.createElement('option');
            option.value = name;
            option.textContent = theme.name;
            select.appendChild(option);
        }
    },

    getAvailableThemes() {
        return Object.keys(this._themeCache);
    },

    getCurrentTheme() {
        return this.currentTheme;
    }
};

// Auto-init on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    ThemeManager.init();
});
