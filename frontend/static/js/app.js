// app.js — Main app: models management, tab switching, settings, generate flow
// Depends on: theme-manager.js (loaded first)

const API_BASE = '/api';

// ========== CONSOLIDATED STATE ==========
const appState = {
    models: {},
    editModel: null
};

// ========== HELPERS ==========

/** Render breadcrumb navigation */
function renderBreadcrumb(container, path, onNavigate, rootLabel = '\uD83C\uDFE0 Home') {
    container.innerHTML = '';
    const homeLink = document.createElement('span');
    homeLink.className = 'breadcrumb-item';
    homeLink.textContent = rootLabel;
    homeLink.onclick = () => onNavigate('');
    container.appendChild(homeLink);

    if (path) {
        const parts = path.split('/').filter(Boolean);
        let accumulated = '';
        parts.forEach(part => {
            container.appendChild(document.createTextNode(' / '));
            accumulated += (accumulated ? '/' : '') + part;
            const link = document.createElement('span');
            link.className = 'breadcrumb-item';
            link.textContent = part;
            const p = accumulated;
            link.onclick = () => onNavigate(p);
            container.appendChild(link);
        });
    }
}

/** Show a modal by ID */
function showModal(id) {
    document.getElementById(id).classList.add('active');
}

/** Hide a modal and optionally reset a form */
function hideModal(id, formId = null) {
    document.getElementById(id).classList.remove('active');
    if (formId) document.getElementById(formId).reset();
}

/** Create a <select> option element */
function createOption(value, text) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = text;
    return option;
}

// ========== API HELPERS ==========

/** Generic API call that parses JSON and handles errors */
async function apiCall(endpoint, options = {}) {
    try {
        const response = await fetch(API_BASE + endpoint, options);
        if (!response.ok) {
            let errorMessage = 'API request failed';
            try {
                const error = await response.json();
                errorMessage = error.detail || errorMessage;
            } catch (e) {
                const text = await response.text();
                errorMessage = text || errorMessage;
            }
            throw new Error(errorMessage);
        }
        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        alert('Error: ' + error.message);
        throw error;
    }
}

/** Track a file as recently read in user preferences */
async function trackAsRecentlyRead(filePath) {
    try {
        const prefs = await fetch(API_BASE + '/audiobooks/preferences/get').then(r => r.json());
        if (!prefs.audiobooks) prefs.audiobooks = {};
        prefs.audiobooks[filePath] = { last_played: Date.now() };
        await fetch(API_BASE + '/audiobooks/preferences/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(prefs)
        });
    } catch (error) {
        console.error('[TRACKING] Failed to track as recently read:', error);
    }
}

// ========== TAB SWITCHING ==========

document.getElementById('filesTab').addEventListener('click', () => switchTab('files'));
document.getElementById('modelsTab').addEventListener('click', () => switchTab('models'));

function switchTab(tab) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));

    const tabs = {
        files: ['filesSection', 'filesTab', refreshFiles],
        models: ['modelsSection', 'modelsTab', refreshModels]
    };

    if (tabs[tab]) {
        document.getElementById(tabs[tab][0]).classList.add('active');
        document.getElementById(tabs[tab][1]).classList.add('active');
        tabs[tab][2]();
    }
}

// ========== MODELS MANAGEMENT ==========

async function refreshModels() {
    const container = document.getElementById('modelsList');
    container.innerHTML = '<div class="loading">Loading models...</div>';

    try {
        appState.models = await apiCall('/openai/models');
        displayModels(appState.models);
    } catch (error) {
        container.innerHTML = '<div class="loading">Error loading models</div>';
    }
}

function displayModels(models) {
    const container = document.getElementById('modelsList');
    if (Object.keys(models).length === 0) {
        container.innerHTML = '<div class="loading">No models configured. Click "Add Model" to get started.</div>';
        return;
    }

    container.innerHTML = '';
    for (const [name, model] of Object.entries(models)) {
        const item = document.createElement('div');
        item.className = 'model-item';
        item.innerHTML = `
            <div class="model-info">
                <div class="model-name">${model.name}</div>
                <div class="model-meta">
                    API Model: ${model.api_model || model.name}<br>
                    Voices: ${model.voices.join(', ')}
                    ${model.base_url ? `<br>Base URL: ${model.base_url}` : ''}
                </div>
            </div>
            <div class="model-actions">
                <button class="btn-small" onclick="showEditModelDialog('${name}')">✏️ Edit</button>
                <button class="btn-small btn-danger" onclick="deleteModel('${name}')">🗑️ Delete</button>
            </div>
        `;
        container.appendChild(item);
    }
}

function showEditModelDialog(modelKey) {
    appState.editModel = modelKey;
    const model = appState.models[modelKey];
    if (!model) return;

    showModal('editModelModal');
    document.getElementById('editModelName').value = model.name;
    document.getElementById('editApiModel').value = model.api_model || '';
    document.getElementById('editVoicesList').value = model.voices.join(', ');
    document.getElementById('editBaseUrl').value = model.base_url || '';
    document.getElementById('editApiKey').value = model.api_key || '';
}

function closeEditModelModal() {
    hideModal('editModelModal', 'editModelForm');
    appState.editModel = null;
}

async function handleEditModelSubmit(e) {
    e.preventDefault();
    if (!appState.editModel) return;

    const data = {
        model_name: document.getElementById('editModelName').value,
        api_model: document.getElementById('editApiModel').value,
        voices: document.getElementById('editVoicesList').value.split(',').map(v => v.trim()).filter(Boolean),
        original_name: appState.editModel
    };

    const baseUrl = document.getElementById('editBaseUrl').value;
    const apiKey = document.getElementById('editApiKey').value;
    if (baseUrl) data.base_url = baseUrl;
    if (apiKey) data.api_key = apiKey;

    try {
        await apiCall('/openai/models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        closeEditModelModal();
        refreshModels();
    } catch (error) { /* error already shown in apiCall */ }
}

function showAddModelDialog() {
    showModal('addModelModal');
}

function closeAddModelModal() {
    hideModal('addModelModal', 'addModelForm');
}

async function handleAddModelSubmit(e) {
    e.preventDefault();

    const data = {
        model_name: document.getElementById('modelName').value,
        api_model: document.getElementById('apiModel').value,
        voices: document.getElementById('voicesList').value.split(',').map(v => v.trim()).filter(Boolean)
    };

    const baseUrl = document.getElementById('baseUrl').value;
    const apiKey = document.getElementById('apiKey').value;
    if (baseUrl) data.base_url = baseUrl;
    if (apiKey) data.api_key = apiKey;

    try {
        await apiCall('/openai/models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        closeAddModelModal();
        refreshModels();
    } catch (error) { /* error already shown in apiCall */ }
}

async function deleteModel(modelName) {
    if (!confirm(`Delete model "${modelName}"?`)) return;
    try {
        await apiCall(`/openai/models/${modelName}`, { method: 'DELETE' });
        refreshModels();
    } catch (error) { /* error already shown in apiCall */ }
}

// ========== GENERATE MODAL ==========

function showGenerateModal(filePath) {
    document.getElementById('generateFilePath').value = filePath;
    showModal('generateModal');

    const modelSelect = document.getElementById('modelSelect');
    modelSelect.innerHTML = '';
    for (const [name, model] of Object.entries(appState.models)) {
        modelSelect.appendChild(createOption(name, model.name));
    }
    if (modelSelect.options.length > 0) updateVoiceOptions();
}

function closeGenerateModal() {
    hideModal('generateModal', 'generateForm');
}

function updateVoiceOptions() {
    const modelSelect = document.getElementById('modelSelect');
    const voiceSelect = document.getElementById('voiceSelect');
    const selectedModel = modelSelect.value;
    voiceSelect.innerHTML = '';
    if (selectedModel && appState.models[selectedModel]) {
        appState.models[selectedModel].voices.forEach(voice =>
            voiceSelect.appendChild(createOption(voice, voice))
        );
    }
}

async function handleGenerateSubmit(e) {
    e.preventDefault();
    const ebookPath = document.getElementById('generateFilePath').value;
    // Track as recently read before navigating away
    try { await trackAsRecentlyRead(ebookPath); } catch(err) {}
    window.location.href = `/stream?ebook_path=${encodeURIComponent(ebookPath)}`;
}

// ========== SETTINGS MENU ==========

async function toggleSettingsMenu(event, menuId) {
    event.stopPropagation();
    const menu = document.getElementById('settings-' + menuId);
    if (!menu) return;

    document.querySelectorAll('.settings-menu').forEach(m => {
        if (m.id !== 'settings-' + menuId) m.style.display = 'none';
    });

    const isOpening = menu.style.display === 'none' || !menu.style.display;
    menu.style.display = isOpening ? 'block' : 'none';
}

function closeAllSettingsMenus() {
    document.querySelectorAll('.settings-menu').forEach(menu => menu.style.display = 'none');
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('.settings-menu') && !e.target.classList.contains('settings-btn')) {
        closeAllSettingsMenus();
    }
});

// ========== SETTINGS PANEL (Files tab) ==========

function openFilesSettings() {
    showSettingsModal();
    switchSettingsTab('display');
    loadGenerationModels();
    loadGenerationQueueStatus();
}

function switchSettingsTab(tab) {
    const displayTab = document.getElementById('settingsTabDisplay');
    const genTab = document.getElementById('settingsTabGeneration');
    const displayContent = document.getElementById('settingsTabContentDisplay');
    const genContent = document.getElementById('settingsTabContentGeneration');

    if (tab === 'display') {
        displayTab.style.borderBottomColor = 'var(--primary-color)';
        genTab.style.borderBottomColor = 'transparent';
        displayContent.style.display = 'block';
        genContent.style.display = 'none';
    } else {
        genTab.style.borderBottomColor = 'var(--primary-color)';
        displayTab.style.borderBottomColor = 'transparent';
        genContent.style.display = 'block';
        displayContent.style.display = 'none';
    }
}

async function loadGenerationModels() {
    const modelSelect = document.getElementById('genModelSelect');
    if (!modelSelect) return;

    modelSelect.innerHTML = '';
    if (Object.keys(appState.models).length === 0) {
        try {
            appState.models = await apiCall('/openai/models');
        } catch (e) {
            modelSelect.innerHTML = '<option value="">No models configured</option>';
            return;
        }
    }

    for (const [name, model] of Object.entries(appState.models)) {
        modelSelect.appendChild(createOption(name, model.name));
    }
    if (modelSelect.options.length > 0) updateGenVoiceOptions();
}

function updateGenVoiceOptions() {
    const modelSelect = document.getElementById('genModelSelect');
    const voiceSelect = document.getElementById('genVoiceSelect');
    const selectedModel = modelSelect.value;
    voiceSelect.innerHTML = '';
    if (selectedModel && appState.models[selectedModel]) {
        appState.models[selectedModel].voices.forEach(voice =>
            voiceSelect.appendChild(createOption(voice, voice))
        );
    }
}

function loadGenerationQueueStatus() {
    const content = document.getElementById('genStatusContent');
    if (content) {
        content.innerHTML = 'Use the file settings panel for generation status';
    }
}

// ========== GLOBAL EXPORTS (only what HTML onclick needs) ==========

window.toggleSettingsMenu = toggleSettingsMenu;
window.closeAllSettingsMenus = closeAllSettingsMenus;
window.showGenerateModal = showGenerateModal;
window.closeGenerateModal = closeGenerateModal;
window.openFilesSettings = openFilesSettings;
window.switchSettingsTab = switchSettingsTab;
window.updateGenVoiceOptions = updateGenVoiceOptions;

// ========== INITIALIZATION ==========

document.addEventListener('DOMContentLoaded', () => {
    const urlParams = new URLSearchParams(window.location.search);
    const tab = urlParams.get('tab');
    const path = urlParams.get('path');

    if (path !== null) {
        fileState.current = path;
        refreshFiles();
    }
    if (tab === 'files') {
        document.getElementById('filesTab').click();
    }
    if (!fileState.current) {
        refreshFiles();
    }

    refreshModels();

    document.getElementById('fileUpload').addEventListener('change', handleFileUpload);
    document.getElementById('generateForm').addEventListener('submit', handleGenerateSubmit);
    document.getElementById('addModelForm').addEventListener('submit', handleAddModelSubmit);
    document.getElementById('modelSelect').addEventListener('change', updateVoiceOptions);
    document.getElementById('editModelForm').addEventListener('submit', handleEditModelSubmit);
});
