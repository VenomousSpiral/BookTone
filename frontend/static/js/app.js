// Main application logic
const API_BASE = '/api';

// ========== CONSOLIDATED STATE ==========
const appState = {
    models: {},
    editModel: null,
    audiobooks: {
        current: '',
        all: [],
        moveMenu: { visible: false, source: '', isDirectory: false, dest: '' },
        updateMenu: { visible: false, id: null, title: '', path: '' }
    },
    // Track generation start times for ETA calculation
    generationTracking: {}
};

// ========== HELPER FUNCTIONS ==========
const Helpers = {
    // Breadcrumb renderer
    renderBreadcrumb(container, path, onNavigate, rootLabel = '🏠 Home') {
        container.innerHTML = '';
        const homeLink = document.createElement('span');
        homeLink.className = 'breadcrumb-item';
        homeLink.textContent = rootLabel;
        homeLink.onclick = () => onNavigate('');
        container.appendChild(homeLink);

        if (path) {
            const parts = path.split('/').filter(p => p);
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
    },

    // Modal control
    showModal(id) {
        document.getElementById(id).classList.add('active');
    },

    hideModal(id, formId = null) {
        document.getElementById(id).classList.remove('active');
        if (formId) document.getElementById(formId).reset();
    },

    // Create option element
    createOption(value, text) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = text;
        return option;
    }
};

// ========== RECENTLY READ TRACKING ==========
async function trackAsRecentlyRead(audiobookId) {
    try {
        const prefs = await fetch(API_BASE + '/audiobooks/preferences/get').then(r => r.json());
        if (!prefs.audiobooks) prefs.audiobooks = {};
        prefs.audiobooks[audiobookId] = { last_played: Date.now() };

        await fetch(API_BASE + '/audiobooks/preferences/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(prefs)
        });
        console.log(`[TRACKING] Marked as recently read: ${audiobookId}`);
    } catch (error) {
        console.error('[TRACKING] Failed to track as recently read:', error);
    }
}

// ========== API HELPER ==========
async function apiCall(endpoint, options = {}) {
    try {
        console.log('API Call:', endpoint, options);
        const response = await fetch(API_BASE + endpoint, options);
        console.log('API Response status:', response.status);

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

        const data = await response.json();
        console.log('API Response data:', data);
        return data;
    } catch (error) {
        console.error('API Error:', error);
        alert('Error: ' + error.message);
        throw error;
    }
}

// ========== TAB SWITCHING ==========
document.getElementById('filesTab').addEventListener('click', () => switchTab('files'));
document.getElementById('audiobooksTab').addEventListener('click', () => switchTab('audiobooks'));
document.getElementById('modelsTab').addEventListener('click', () => switchTab('models'));

function switchTab(tab) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));

    const tabs = {
        files: ['filesSection', 'filesTab', refreshFiles],
        audiobooks: ['audiobooksSection', 'audiobooksTab', refreshAudiobooks],
        models: ['modelsSection', 'modelsTab', refreshModels]
    };

    if (tabs[tab]) {
        document.getElementById(tabs[tab][0]).classList.add('active');
        document.getElementById(tabs[tab][1]).classList.add('active');
        tabs[tab][2]();
    }
}

// ========== INITIALIZATION ==========
document.addEventListener('DOMContentLoaded', () => {
    const urlParams = new URLSearchParams(window.location.search);
    const tab = urlParams.get('tab');
    const path = urlParams.get('path');

    if (tab === 'files' && path !== null) {
        fileState.current = path;
        refreshFiles();
        document.getElementById('filesTab').click();
    } else {
        refreshFiles();
    }

    refreshModels();

    document.getElementById('fileUpload').addEventListener('change', handleFileUpload);
    document.getElementById('generateForm').addEventListener('submit', handleGenerateSubmit);
    document.getElementById('addModelForm').addEventListener('submit', handleAddModelSubmit);
    document.getElementById('modelSelect').addEventListener('change', updateVoiceOptions);
    document.getElementById('editModelForm').addEventListener('submit', handleEditModelSubmit);
});

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

    Helpers.showModal('editModelModal');
    document.getElementById('editModelName').value = model.name;
    document.getElementById('editApiModel').value = model.api_model || '';
    document.getElementById('editVoicesList').value = model.voices.join(', ');
    document.getElementById('editBaseUrl').value = model.base_url || '';
    document.getElementById('editApiKey').value = model.api_key || '';
}

function closeEditModelModal() {
    Helpers.hideModal('editModelModal', 'editModelForm');
    appState.editModel = null;
}

async function handleEditModelSubmit(e) {
    e.preventDefault();
    if (!appState.editModel) return;

    const data = {
        model_name: document.getElementById('editModelName').value,
        api_model: document.getElementById('editApiModel').value,
        voices: document.getElementById('editVoicesList').value.split(',').map(v => v.trim()).filter(v => v),
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
    } catch (error) { }
}

function showAddModelDialog() {
    Helpers.showModal('addModelModal');
}

function closeAddModelModal() {
    Helpers.hideModal('addModelModal', 'addModelForm');
}

async function handleAddModelSubmit(e) {
    e.preventDefault();

    const data = {
        model_name: document.getElementById('modelName').value,
        api_model: document.getElementById('apiModel').value,
        voices: document.getElementById('voicesList').value.split(',').map(v => v.trim()).filter(v => v)
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
    } catch (error) { }
}

async function deleteModel(modelName) {
    if (!confirm(`Delete model "${modelName}"?`)) return;

    try {
        await apiCall(`/openai/models/${modelName}`, { method: 'DELETE' });
        refreshModels();
    } catch (error) { }
}

// ========== AUDIOBOOKS MANAGEMENT ==========
async function refreshAudiobooks() {
    const container = document.getElementById('audiobookList');
    const prevScroll = container.scrollTop;
    console.log('[Audiobook] Scroll position before refresh:', prevScroll);

    // Save which settings menus are currently open
    const openMenus = [];
    document.querySelectorAll('.settings-menu').forEach(menu => {
        if (menu.style.display !== 'none') {
            openMenus.push(menu.id);
        }
    });

    try {
        const audiobooks = await apiCall(`/audiobooks/list?path=${encodeURIComponent(appState.audiobooks.current)}`);
        appState.audiobooks.all = audiobooks.filter(a => a.title !== '.gitkeep');

        await sortAudiobooks();
        updateAudiobookBreadcrumb();

        setTimeout(() => {
            container.scrollTop = prevScroll;
            console.log('[Audiobook] Scroll position after refresh:', container.scrollTop);

            // Restore open settings menus
            openMenus.forEach(menuId => {
                const menu = document.getElementById(menuId);
                if (menu) {
                    menu.style.display = 'block';
                }
            });
        }, 0);

        if (audiobooks.some(a => a.status === 'in_progress')) {
            setTimeout(refreshAudiobooks, 3000);
        }
    } catch (error) {
        container.innerHTML = '<div class="loading">Error loading audiobooks</div>';
    }
}

function filterAudiobooks() {
    const searchTerm = document.getElementById('audiobookSearch').value.toLowerCase();
    const filtered = appState.audiobooks.all.filter(ab => ab.title.toLowerCase().includes(searchTerm));
    displayAudiobooks(filtered);
}

async function sortAudiobooks() {
    const sortBy = document.getElementById('audiobookSort').value;
    const searchTerm = document.getElementById('audiobookSearch').value.toLowerCase();
    let filtered = appState.audiobooks.all.filter(ab => ab.title.toLowerCase().includes(searchTerm));

    if (sortBy === 'recent') {
        try {
            const prefs = await apiCall('/audiobooks/preferences/get');
            filtered = applySortToAudiobooks(filtered, sortBy, prefs);
        } catch (error) {
            console.log('No preferences found yet, using alphabetical order');
            filtered = applySortToAudiobooks(filtered, 'name');
        }
    } else {
        filtered = applySortToAudiobooks(filtered, sortBy);
    }

    displayAudiobooks(filtered);
}

function applySortToAudiobooks(audiobooks, sortBy, userPrefs = null) {
    const sorted = [...audiobooks];
    const directories = sorted.filter(a => a.is_directory);
    const files = sorted.filter(a => !a.is_directory);

    files.sort((a, b) => {
        switch (sortBy) {
            case 'recent':
                if (userPrefs && userPrefs.audiobooks) {
                    const aTime = userPrefs.audiobooks[a.id]?.last_played || 0;
                    const bTime = userPrefs.audiobooks[b.id]?.last_played || 0;
                    if (aTime === 0 && bTime === 0) return b.modified - a.modified; // Sort unplayed by date added (newest first)
                    return bTime - aTime;
                }
                return a.title.localeCompare(b.title);
            case 'name':
                return a.title.localeCompare(b.title);
            case 'modified':
            case 'added':
                return b.modified - a.modified;
            default:
                return a.title.localeCompare(b.title);
        }
    });

    directories.sort((a, b) => a.title.localeCompare(b.title));
    return [...directories, ...files];
}

function displayAudiobooks(audiobooks) {
    const container = document.getElementById('audiobookList');

    if (audiobooks.length === 0) {
        container.innerHTML = '<div class="loading">No audiobooks yet. Generate one from the Files tab!</div>';
        return;
    }

    container.innerHTML = '';
    const directories = audiobooks.filter(a => a.is_directory);
    const files = audiobooks.filter(a => !a.is_directory);

    directories.forEach(dir => {
        const item = document.createElement('div');
        item.className = 'audiobook-item';
        item.style.cursor = 'pointer';

        item.onclick = (e) => {
            if (!e.target.closest('button') && !e.target.closest('.settings-menu')) {
                navigateToAudiobookDirectory(dir.path);
            }
        };

        item.innerHTML = `
            <div class="audiobook-info">
                <div class="audiobook-title">📁 ${dir.title}</div>
                <div class="audiobook-meta">${new Date(dir.modified * 1000).toLocaleDateString()}</div>
            </div>
            <div class="audiobook-actions">
                <button class="btn-small settings-btn" onclick="event.stopPropagation(); toggleSettingsMenu(event, 'dir-${dir.path.replace(/[^a-zA-Z0-9]/g, '_')}')">⚙️</button>
                <div id="settings-dir-${dir.path.replace(/[^a-zA-Z0-9]/g, '_')}" class="settings-menu" style="display: none;">
                    <button class="settings-menu-item" onclick="event.stopPropagation(); moveAudiobookDialog('${dir.path}', true); closeAllSettingsMenus();">↔️ Move</button>
                    <button class="settings-menu-item btn-danger" onclick="event.stopPropagation(); deleteAudiobookDir('${dir.path}'); closeAllSettingsMenus();">🗑️ Delete</button>
                </div>
            </div>
        `;
        container.appendChild(item);
    });

    files.forEach(audiobook => {
        const item = document.createElement('div');
        item.className = 'audiobook-item';

        const progress = audiobook.status === 'in_progress' ?
            `<div class="progress-bar"><div class="progress-fill" style="width: ${audiobook.progress * 100}%"></div></div>` : '';

        const hasChunks = audiobook.audio_chunks && audiobook.audio_chunks.length > 0;
        const isPlayable = (audiobook.status === 'completed' || audiobook.status === 'in_progress' || audiobook.status === 'paused') && hasChunks;

        if (isPlayable) {
            item.style.cursor = 'pointer';
            item.onclick = (e) => {
                if (!e.target.closest('button') && !e.target.closest('.settings-menu')) {
                    playAudiobook(audiobook.id);
                }
            };
        }

        // Calculate ETA for in-progress audiobooks
        let etaText = '';
        if (audiobook.status === 'in_progress' && audiobook.completed_chunks > 0) {
            if (!appState.generationTracking[audiobook.id]) {
                // First time seeing this audiobook generating
                appState.generationTracking[audiobook.id] = {
                    startTime: Date.now(),
                    startChunks: audiobook.completed_chunks
                };
            }

            const tracking = appState.generationTracking[audiobook.id];
            const elapsedSeconds = (Date.now() - tracking.startTime) / 1000;
            const chunksProcessed = audiobook.completed_chunks - tracking.startChunks;

            // Need at least 2 chunks processed to get a good estimate
            if (chunksProcessed >= 2 && elapsedSeconds > 5) {
                const chunksPerSecond = chunksProcessed / elapsedSeconds;
                const remainingChunks = audiobook.total_chunks - audiobook.completed_chunks;
                const estimatedSecondsRemaining = remainingChunks / chunksPerSecond;

                // Format time
                if (estimatedSecondsRemaining < 60) {
                    etaText = `~${Math.round(estimatedSecondsRemaining)} sec remaining`;
                } else if (estimatedSecondsRemaining < 3600) {
                    etaText = `~${Math.round(estimatedSecondsRemaining / 60)} min remaining`;
                } else {
                    const hours = Math.floor(estimatedSecondsRemaining / 3600);
                    const minutes = Math.round((estimatedSecondsRemaining % 3600) / 60);
                    etaText = `~${hours} hr ${minutes} min remaining`;
                }
            } else {
                etaText = 'Calculating ETA...';
            }
        } else if (audiobook.status !== 'in_progress' && appState.generationTracking[audiobook.id]) {
            // Clean up tracking for completed/failed audiobooks
            delete appState.generationTracking[audiobook.id];
        }

        // Build download button based on status
        let downloadButton = '';
        if (audiobook.status === 'completed') {
            const safeId = audiobook.id.replace(/'/g, "\\'");
            const safeTitle = audiobook.title.replace(/'/g, "\\'");
            // Use a data attribute to track status and update dynamically
            downloadButton = `<button class="settings-menu-item download-btn" data-audiobook-id="${audiobook.id}" onclick="event.stopPropagation(); downloadAudiobook('${safeId}', '${safeTitle}');">⬇️ Download MP3</button>`;
        }

        const buttons = [
            audiobook.status === 'in_progress' ? `<button class="settings-menu-item" onclick="event.stopPropagation(); pauseAudiobook('${audiobook.id}'); closeAllSettingsMenus();">⏸️ Pause Generation</button>` : '',
            (audiobook.status === 'paused' || audiobook.status === 'failed' || audiobook.status === 'error') ? `<button class="settings-menu-item" onclick="event.stopPropagation(); resumeAudiobook('${audiobook.id}'); closeAllSettingsMenus();">▶️ Resume Generation</button>` : '',
            (audiobook.status === 'completed' || audiobook.status === 'paused' || audiobook.status === 'failed' || audiobook.status === 'error') ? `<button class="settings-menu-item" onclick="event.stopPropagation(); showUpdateAudiobookDialog('${audiobook.id}', '${audiobook.title.replace(/'/g, "\\'")}'); closeAllSettingsMenus();">🔄 Update</button>` : '',
            downloadButton
        ].filter(b => b).join('');

        item.innerHTML = `
            <div class="audiobook-info">
                <div class="audiobook-title">${audiobook.title}</div>
                <div class="audiobook-meta">
                    <span class="status-badge status-${audiobook.status}">${audiobook.status}</span>
                    Model: ${audiobook.model} | Voice: ${audiobook.voice}
                    ${audiobook.status === 'in_progress' ? `<br>Progress: ${audiobook.completed_chunks}/${audiobook.total_chunks} chunks (${Math.round(audiobook.progress * 100)}%)` : ''}
                    ${etaText ? `<br><span style="color: var(--accent);">${etaText}</span>` : ''}
                    ${audiobook.error ? `<br>Error: ${audiobook.error}` : ''}
                </div>
                ${progress}
            </div>
            <div class="audiobook-actions">
                <button class="btn-small settings-btn" onclick="event.stopPropagation(); toggleSettingsMenu(event, 'ab-${audiobook.id.replace(/[^a-zA-Z0-9]/g, '_')}')">⚙️</button>
                <div id="settings-ab-${audiobook.id.replace(/[^a-zA-Z0-9]/g, '_')}" class="settings-menu" style="display: none;">
                    ${buttons}
                    <button class="settings-menu-item" onclick="event.stopPropagation(); moveAudiobookDialog('${audiobook.id}', false, '${audiobook.title.replace(/'/g, "\\'")}'); closeAllSettingsMenus();">↔️ Move</button>
                    <button class="settings-menu-item btn-danger" onclick="event.stopPropagation(); deleteAudiobook('${audiobook.id}'); closeAllSettingsMenus();">🗑️ Delete</button>
                </div>
            </div>
        `;
        container.appendChild(item);
    });
}

function updateAudiobookBreadcrumb() {
    const breadcrumb = document.getElementById('audiobookBreadcrumb');
    if (!breadcrumb) return;
    Helpers.renderBreadcrumb(breadcrumb, appState.audiobooks.current, navigateToAudiobookDirectory);
}

function navigateToAudiobookDirectory(path) {
    appState.audiobooks.current = path;
    refreshAudiobooks();
}

function showGenerateModal(filePath) {
    document.getElementById('generateFilePath').value = filePath;
    Helpers.showModal('generateModal');

    const modelSelect = document.getElementById('modelSelect');
    modelSelect.innerHTML = '';

    for (const [name, model] of Object.entries(appState.models)) {
        modelSelect.appendChild(Helpers.createOption(name, model.name));
    }

    if (modelSelect.options.length > 0) updateVoiceOptions();
}

function closeGenerateModal() {
    Helpers.hideModal('generateModal', 'generateForm');
}

function updateVoiceOptions() {
    const modelSelect = document.getElementById('modelSelect');
    const voiceSelect = document.getElementById('voiceSelect');
    const selectedModel = modelSelect.value;

    voiceSelect.innerHTML = '';

    if (selectedModel && appState.models[selectedModel]) {
        const voices = appState.models[selectedModel].voices;
        voices.forEach(voice => voiceSelect.appendChild(Helpers.createOption(voice, voice)));
    }
}

async function handleGenerateSubmit(e) {
    e.preventDefault();

    const data = {
        ebook_path: document.getElementById('generateFilePath').value,
        model: document.getElementById('modelSelect').value,
        voice: document.getElementById('voiceSelect').value
    };

    const instructions = document.getElementById('instructions').value;
    if (instructions) data.instructions = instructions;

    try {
        const result = await apiCall('/audiobooks/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        // Track as recently read when generating audiobook
        if (result && result.audiobook_id) {
            await trackAsRecentlyRead(result.audiobook_id);
        }
        closeGenerateModal();
        switchTab('audiobooks');
    } catch (error) { }
}

function createAudiobookDirectory() {
    const name = prompt('Enter folder name:');
    if (!name) return;
    const path = appState.audiobooks.current ? `${appState.audiobooks.current}/${name}` : name;
    createAudiobookDir(path);
}

async function createAudiobookDir(path) {
    try {
        await apiCall('/audiobooks/create-directory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path })
        });
        // Track as recently read when creating directory
        await trackAsRecentlyRead(path);
        refreshAudiobooks();
    } catch (error) {
        alert('Error creating directory');
    }
}

function moveAudiobookDialog(source, isDirectory, displayName) {
    if (!displayName) displayName = source.split('/').pop();
    showAudiobookMoveMenu(source, isDirectory, displayName);
}

function showAudiobookMoveMenu(sourcePath, isDirectory, displayName) {
    appState.audiobooks.moveMenu = { visible: true, source: sourcePath, isDirectory, dest: '' };

    let menu = document.getElementById('audiobookMoveMenu');
    if (!menu) {
        menu = document.createElement('div');
        menu.id = 'audiobookMoveMenu';
        menu.className = 'modal';
        document.body.appendChild(menu);
    }

    menu.style.display = 'flex';
    menu.classList.add('active');
    menu.innerHTML = `
        <div class="modal-content" style="max-width:600px;">
            <h3>Move ${isDirectory ? 'Folder' : 'Audiobook'}: <span id="audiobookMoveSourceName"></span></h3>
            <div id="audiobookMoveNavBreadcrumb" class="breadcrumb"></div>
            <div id="audiobookMoveNavList" style="max-height:300px;overflow-y:auto;margin-bottom:15px;"></div>
            <div class="modal-buttons">
                <button id="audiobookMoveNewFolderBtn" class="btn">📁 New Folder</button>
                <button id="audiobookMoveHereBtn" class="btn btn-primary">Move Here</button>
                <button id="audiobookMoveCancelBtn" class="btn btn-danger">Cancel</button>
            </div>
        </div>
    `;
    menu.querySelector('#audiobookMoveSourceName').textContent = displayName;
    renderAudiobookMoveNav();

    menu.querySelector('#audiobookMoveHereBtn').onclick = async () => {
        await moveAudiobook(appState.audiobooks.moveMenu.source, appState.audiobooks.moveMenu.dest, appState.audiobooks.moveMenu.isDirectory);
        hideAudiobookMoveMenu();
    };
    menu.querySelector('#audiobookMoveCancelBtn').onclick = hideAudiobookMoveMenu;
    menu.querySelector('#audiobookMoveNewFolderBtn').onclick = async () => {
        const name = prompt('Enter new folder name:');
        if (!name) return;
        const path = appState.audiobooks.moveMenu.dest ? `${appState.audiobooks.moveMenu.dest}/${name}` : name;
        await createAudiobookDir(path);
        renderAudiobookMoveNav();
    };
}

function hideAudiobookMoveMenu() {
    appState.audiobooks.moveMenu.visible = false;
    const menu = document.getElementById('audiobookMoveMenu');
    if (menu) {
        menu.style.display = 'none';
        menu.classList.remove('active');
    }
}

async function renderAudiobookMoveNav() {
    const navList = document.getElementById('audiobookMoveNavList');
    const breadcrumb = document.getElementById('audiobookMoveNavBreadcrumb');
    if (!navList || !breadcrumb) return;

    Helpers.renderBreadcrumb(breadcrumb, appState.audiobooks.moveMenu.dest, (path) => {
        appState.audiobooks.moveMenu.dest = path;
        renderAudiobookMoveNav();
    });

    navList.innerHTML = '';
    try {
        const audiobooks = await apiCall(`/audiobooks/list?path=${encodeURIComponent(appState.audiobooks.moveMenu.dest)}`);
        const dirs = audiobooks.filter(a => a.is_directory);

        if (dirs.length === 0) {
            navList.innerHTML = '<div class="loading">No folders found.</div>';
        } else {
            dirs.forEach(dir => {
                const item = document.createElement('div');
                item.className = 'file-item';
                item.innerHTML = `<div class="file-info"><div class="file-name">📁 ${dir.title}</div></div>`;
                item.onclick = () => {
                    appState.audiobooks.moveMenu.dest = dir.path;
                    renderAudiobookMoveNav();
                };
                navList.appendChild(item);
            });
        }
    } catch (error) {
        navList.innerHTML = '<div class="loading">Error loading folders</div>';
    }
}

async function moveAudiobook(source, destination, isDirectory) {
    try {
        await apiCall('/audiobooks/move', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source, destination, is_directory: isDirectory })
        });
        // Track as recently read when moving audiobook
        if (!isDirectory) {
            const newId = destination ? `${destination}/${source.split('/').pop()}` : source.split('/').pop();
            await trackAsRecentlyRead(newId);
        }
        refreshAudiobooks();
    } catch (error) {
        alert('Error moving item');
    }
}

async function deleteAudiobookDir(path) {
    if (!confirm('Delete this folder and all its contents?')) return;
    try {
        await apiCall(`/audiobooks/delete-directory?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
        refreshAudiobooks();
    } catch (error) {
        alert('Error deleting folder');
    }
}

async function pauseAudiobook(id) {
    try {
        await apiCall(`/audiobooks/${id}/pause`, { method: 'POST' });
        refreshAudiobooks();
    } catch (error) { }
}

async function resumeAudiobook(id) {
    try {
        await apiCall(`/audiobooks/${id}/resume`, { method: 'POST' });
        refreshAudiobooks();
    } catch (error) { }
}

function showUpdateAudiobookDialog(id, title) {
    appState.audiobooks.updateMenu = { visible: true, id, title, path: '', mode: 'continue', newTitle: '', searchQuery: '' };

    let menu = document.getElementById('updateAudiobookMenu');
    if (!menu) {
        menu = document.createElement('div');
        menu.id = 'updateAudiobookMenu';
        menu.className = 'modal';
        document.body.appendChild(menu);
    }

    menu.style.display = 'flex';
    menu.classList.add('active');
    menu.innerHTML = `
        <div class="modal-content" style="max-width:650px; max-height:85vh; overflow:hidden; display:flex; flex-direction:column;">
            <h3 style="margin-bottom:10px;">🔄 Update Audiobook</h3>
            
            <div style="background:rgba(255,255,255,0.05); padding:10px; border-radius:8px; margin-bottom:15px;">
                <div style="font-weight:bold; margin-bottom:5px;">${title}</div>
                <div style="font-size:0.85em; color:#888;">Select how to update this audiobook</div>
            </div>
            
            <!-- Update Options -->
            <div style="margin-bottom:15px;">
                <label style="display:block; margin-bottom:8px; font-weight:bold;">Update Mode:</label>
                <div style="display:flex; gap:10px; flex-wrap:wrap;">
                    <label style="flex:1; min-width:150px; padding:12px; background:rgba(76,175,80,0.2); border:2px solid #4CAF50; border-radius:8px; cursor:pointer; display:flex; align-items:flex-start; gap:8px;">
                        <input type="radio" name="updateMode" value="continue" checked style="margin-top:3px;">
                        <div>
                            <div style="font-weight:bold;">Continue</div>
                            <div style="font-size:0.8em; color:#aaa;">Update source and continue from current position</div>
                        </div>
                    </label>
                    <label style="flex:1; min-width:150px; padding:12px; background:rgba(33,150,243,0.1); border:2px solid transparent; border-radius:8px; cursor:pointer; display:flex; align-items:flex-start; gap:8px;">
                        <input type="radio" name="updateMode" value="append" style="margin-top:3px;">
                        <div>
                            <div style="font-weight:bold;">Append</div>
                            <div style="font-size:0.8em; color:#aaa;">Keep existing audio, add new file as chapters at end</div>
                        </div>
                    </label>
                </div>
            </div>
            
            <!-- Optional: New Title -->
            <div style="margin-bottom:15px;">
                <label style="display:block; margin-bottom:5px; font-size:0.9em;">New Title (optional):</label>
                <input type="text" id="updateNewTitle" placeholder="Leave empty to keep current title" 
                       style="width:100%; padding:8px; border-radius:6px; border:1px solid #444; background:#222; color:#fff; box-sizing:border-box;">
            </div>
            
            <!-- Search -->
            <div style="margin-bottom:10px;">
                <input type="text" id="updateEbookSearch" placeholder="🔍 Search ebooks..." 
                       style="width:100%; padding:10px; border-radius:6px; border:1px solid #444; background:#222; color:#fff; box-sizing:border-box;">
            </div>
            
            <!-- Breadcrumb -->
            <div id="updateEbookNavBreadcrumb" class="breadcrumb" style="margin-bottom:8px;"></div>
            
            <!-- File List -->
            <div id="updateEbookNavList" style="flex:1; min-height:200px; max-height:300px; overflow-y:auto; margin-bottom:15px; border:1px solid #333; border-radius:8px;"></div>
            
            <div class="modal-buttons" style="display:flex; gap:10px;">
                <button id="updateEbookCancelBtn" class="btn btn-danger" style="flex:1;">Cancel</button>
            </div>
        </div>
    `;
    
    // Set up event listeners
    menu.querySelectorAll('input[name="updateMode"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            appState.audiobooks.updateMenu.mode = e.target.value;
            // Update visual selection
            menu.querySelectorAll('input[name="updateMode"]').forEach(r => {
                const label = r.closest('label');
                if (r.checked) {
                    label.style.borderColor = r.value === 'continue' ? '#4CAF50' : '#2196F3';
                    label.style.background = r.value === 'continue' ? 'rgba(76,175,80,0.2)' : 'rgba(33,150,243,0.2)';
                } else {
                    label.style.borderColor = 'transparent';
                    label.style.background = 'rgba(255,255,255,0.05)';
                }
            });
        });
    });
    
    const searchInput = menu.querySelector('#updateEbookSearch');
    let searchTimeout;
    searchInput.addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            appState.audiobooks.updateMenu.searchQuery = e.target.value;
            renderUpdateEbookNav();
        }, 300);
    });
    
    menu.querySelector('#updateNewTitle').addEventListener('input', (e) => {
        appState.audiobooks.updateMenu.newTitle = e.target.value;
    });
    
    renderUpdateEbookNav();
    menu.querySelector('#updateEbookCancelBtn').onclick = closeUpdateAudiobookModal;
}

function closeUpdateAudiobookModal() {
    appState.audiobooks.updateMenu.visible = false;
    const menu = document.getElementById('updateAudiobookMenu');
    if (menu) {
        menu.style.display = 'none';
        menu.classList.remove('active');
    }
}

async function renderUpdateEbookNav() {
    const navList = document.getElementById('updateEbookNavList');
    const breadcrumb = document.getElementById('updateEbookNavBreadcrumb');
    if (!navList || !breadcrumb) return;

    Helpers.renderBreadcrumb(breadcrumb, appState.audiobooks.updateMenu.path, (path) => {
        appState.audiobooks.updateMenu.path = path;
        renderUpdateEbookNav();
    }, '🏠 Ebooks');

    navList.innerHTML = '<div class="loading" style="padding:20px; text-align:center;">Loading...</div>';
    
    try {
        const data = await apiCall(`/files/list?path=${encodeURIComponent(appState.audiobooks.updateMenu.path)}`);
        const files = data.files || [];

        navList.innerHTML = '';

        const searchQuery = (appState.audiobooks.updateMenu.searchQuery || '').toLowerCase().trim();

        // Filter files
        let directories = files.filter(f => f.is_directory);
        let ebookFiles = files.filter(f => !f.is_directory &&
            (f.name.toLowerCase().endsWith('.epub') ||
                f.name.toLowerCase().endsWith('.txt') ||
                f.name.toLowerCase().endsWith('.pdf')));

        // Apply search filter
        if (searchQuery) {
            directories = directories.filter(d => d.name.toLowerCase().includes(searchQuery));
            ebookFiles = ebookFiles.filter(f => f.name.toLowerCase().includes(searchQuery));
        }

        if (directories.length === 0 && ebookFiles.length === 0) {
            navList.innerHTML = `<div class="loading" style="padding:20px; text-align:center; color:#888;">
                ${searchQuery ? 'No matches found' : 'No ebook files or folders found'}
            </div>`;
            return;
        }

        // Render directories
        directories.forEach(dir => {
            const item = document.createElement('div');
            item.className = 'file-item';
            item.style.cssText = 'cursor:pointer; padding:12px 15px; border-bottom:1px solid #333; display:flex; align-items:center; gap:10px; transition:background 0.2s;';
            item.onmouseenter = () => item.style.background = 'rgba(255,255,255,0.05)';
            item.onmouseleave = () => item.style.background = '';
            item.onclick = () => {
                appState.audiobooks.updateMenu.path = dir.path;
                appState.audiobooks.updateMenu.searchQuery = '';
                const searchInput = document.getElementById('updateEbookSearch');
                if (searchInput) searchInput.value = '';
                renderUpdateEbookNav();
            };
            item.innerHTML = `
                <span style="font-size:1.3em;">📁</span>
                <div style="flex:1; min-width:0;">
                    <div style="font-weight:bold; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${dir.name}</div>
                    <div style="font-size:0.8em; color:#666;">${new Date(dir.modified * 1000).toLocaleDateString()}</div>
                </div>
                <span style="color:#666;">›</span>
            `;
            navList.appendChild(item);
        });

        // Render ebook files
        ebookFiles.forEach(file => {
            const item = document.createElement('div');
            item.className = 'file-item';
            item.style.cssText = 'cursor:pointer; padding:12px 15px; border-bottom:1px solid #333; display:flex; align-items:center; gap:10px; transition:background 0.2s;';
            item.onmouseenter = () => item.style.background = 'rgba(76,175,80,0.1)';
            item.onmouseleave = () => item.style.background = '';
            
            const ext = file.name.split('.').pop().toLowerCase();
            const icon = ext === 'epub' ? '📕' : ext === 'pdf' ? '📄' : '📃';
            const sizeStr = file.size > 1024 * 1024 
                ? `${(file.size / 1024 / 1024).toFixed(2)} MB`
                : `${(file.size / 1024).toFixed(1)} KB`;
            
            item.onclick = async () => {
                const mode = appState.audiobooks.updateMenu.mode || 'continue';
                const newTitle = appState.audiobooks.updateMenu.newTitle || '';
                const modeText = mode === 'append' ? 'append to' : 'update';
                
                const confirmMsg = newTitle 
                    ? `${modeText.charAt(0).toUpperCase() + modeText.slice(1)} audiobook "${appState.audiobooks.updateMenu.title}" with "${file.name}"?\n\nNew title: ${newTitle}`
                    : `${modeText.charAt(0).toUpperCase() + modeText.slice(1)} audiobook "${appState.audiobooks.updateMenu.title}" with "${file.name}"?`;
                
                if (confirm(confirmMsg)) {
                    try {
                        const body = { 
                            ebook_path: file.path,
                            mode: mode
                        };
                        if (newTitle) {
                            body.new_title = newTitle;
                        }
                        
                        await apiCall(`/audiobooks/${appState.audiobooks.updateMenu.id}/update`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(body)
                        });
                        await trackAsRecentlyRead(appState.audiobooks.updateMenu.id);
                        closeUpdateAudiobookModal();
                        refreshAudiobooks();
                        
                        // Show success toast
                        const toast = document.createElement('div');
                        toast.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:rgba(76,175,80,0.95);color:#fff;padding:15px 25px;border-radius:10px;z-index:10000;';
                        toast.textContent = mode === 'append' ? '✅ Appending new content...' : '✅ Update started...';
                        document.body.appendChild(toast);
                        setTimeout(() => toast.remove(), 3000);
                    } catch (error) {
                        alert('Error updating audiobook: ' + (error.message || 'Unknown error'));
                    }
                }
            };
            
            item.innerHTML = `
                <span style="font-size:1.3em;">${icon}</span>
                <div style="flex:1; min-width:0;">
                    <div style="font-weight:bold; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${file.name}</div>
                    <div style="font-size:0.8em; color:#666;">${sizeStr} · ${new Date(file.modified * 1000).toLocaleDateString()}</div>
                </div>
                <span style="color:#4CAF50; font-size:0.9em;">Select</span>
            `;
            navList.appendChild(item);
        });

    } catch (error) {
        navList.innerHTML = '<div class="loading" style="padding:20px; text-align:center; color:#f44336;">Error loading files</div>';
    }
}

async function deleteAudiobook(id) {
    if (!confirm('Delete this audiobook?')) return;
    try {
        await apiCall(`/audiobooks/${id}`, { method: 'DELETE' });
        refreshAudiobooks();
    } catch (error) { }
}

// Track ongoing downloads
const downloadTracking = {};

async function downloadAudiobook(id, title) {
    // Check if already preparing
    if (downloadTracking[id]?.preparing) {
        showDownloadProgress(id, title);
        return;
    }
    
    try {
        // First check current status
        const statusResponse = await apiCall(`/audiobooks/${encodeURIComponent(id)}/download-status`);
        
        if (statusResponse.status === 'ready') {
            // Already prepared, download directly
            triggerDownload(id, title);
            return;
        }
        
        if (statusResponse.status === 'combining') {
            // Already in progress, show progress
            downloadTracking[id] = { preparing: true, title };
            showDownloadProgress(id, title);
            return;
        }
        
        // Start preparation
        await apiCall(`/audiobooks/${encodeURIComponent(id)}/prepare-download`, { method: 'POST' });
        downloadTracking[id] = { preparing: true, title };
        showDownloadProgress(id, title);
        
    } catch (error) {
        console.error('Download error:', error);
        showDownloadToast(`❌ Failed to start download: ${error.message}`, 'error');
    }
}

function showDownloadProgress(id, title) {
    // Create or update progress modal
    let modal = document.getElementById('downloadProgressModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'downloadProgressModal';
        modal.className = 'modal';
        modal.style.cssText = 'display: flex; z-index: 10001;';
        document.body.appendChild(modal);
    }
    
    modal.innerHTML = `
        <div class="modal-content" style="max-width: 400px; text-align: center;">
            <h3 style="margin-bottom: 15px;">📥 Preparing Download</h3>
            <p style="margin-bottom: 10px; font-weight: bold;">${title}</p>
            <div id="downloadProgressBar" style="
                width: 100%; height: 20px; background: #333; border-radius: 10px; 
                overflow: hidden; margin-bottom: 10px;
            ">
                <div id="downloadProgressFill" style="
                    width: 0%; height: 100%; background: linear-gradient(90deg, #4CAF50, #8BC34A);
                    transition: width 0.3s ease;
                "></div>
            </div>
            <p id="downloadProgressText" style="margin-bottom: 15px; color: #aaa;">Starting...</p>
            <p id="downloadProgressDetail" style="margin-bottom: 15px; font-size: 12px; color: #888;"></p>
            <button class="btn" onclick="closeDownloadProgressModal()" style="margin-right: 10px;">Hide (continues in background)</button>
        </div>
    `;
    
    modal.style.display = 'flex';
    modal.classList.add('active');
    
    // Start polling for progress
    pollDownloadProgress(id, title);
}

async function pollDownloadProgress(id, title) {
    const progressFill = document.getElementById('downloadProgressFill');
    const progressText = document.getElementById('downloadProgressText');
    const progressDetail = document.getElementById('downloadProgressDetail');
    
    const poll = async () => {
        try {
            const status = await apiCall(`/audiobooks/${encodeURIComponent(id)}/download-status`);
            
            if (progressFill) progressFill.style.width = `${status.progress || 0}%`;
            if (progressText) progressText.textContent = status.message || 'Processing...';
            
            if (status.status === 'combining') {
                if (progressDetail) {
                    progressDetail.textContent = `Chunk ${status.current_chunk || 0} of ${status.total_chunks || '?'}`;
                }
                // Continue polling
                setTimeout(poll, 500);
            } else if (status.status === 'ready') {
                // Ready to download
                if (progressFill) progressFill.style.width = '100%';
                if (progressText) progressText.textContent = status.message || 'Ready!';
                if (progressDetail) progressDetail.textContent = '';
                
                delete downloadTracking[id];
                
                // Update modal with download button
                const modal = document.getElementById('downloadProgressModal');
                if (modal) {
                    modal.innerHTML = `
                        <div class="modal-content" style="max-width: 400px; text-align: center;">
                            <h3 style="margin-bottom: 15px;">✅ Download Ready</h3>
                            <p style="margin-bottom: 10px; font-weight: bold;">${title}</p>
                            <p style="margin-bottom: 15px; color: #4CAF50;">${status.message}</p>
                            <button class="btn btn-primary" onclick="triggerDownload('${id}', '${title.replace(/'/g, "\\'")}'); closeDownloadProgressModal();">
                                ⬇️ Download Now
                            </button>
                            <button class="btn" onclick="closeDownloadProgressModal()" style="margin-left: 10px;">Close</button>
                        </div>
                    `;
                }
                
                // Refresh audiobook list to update UI
                refreshAudiobooks();
                
            } else if (status.status === 'error') {
                delete downloadTracking[id];
                if (progressText) progressText.textContent = `Error: ${status.error}`;
                if (progressFill) progressFill.style.background = '#f44336';
                
                const modal = document.getElementById('downloadProgressModal');
                if (modal) {
                    modal.innerHTML = `
                        <div class="modal-content" style="max-width: 400px; text-align: center;">
                            <h3 style="margin-bottom: 15px; color: #f44336;">❌ Download Failed</h3>
                            <p style="margin-bottom: 10px; font-weight: bold;">${title}</p>
                            <p style="margin-bottom: 15px; color: #f44336;">${status.error}</p>
                            <button class="btn" onclick="closeDownloadProgressModal()">Close</button>
                            <button class="btn btn-primary" onclick="downloadAudiobook('${id}', '${title.replace(/'/g, "\\'")}');" style="margin-left: 10px;">Retry</button>
                        </div>
                    `;
                }
            } else {
                // Not started or unknown, continue polling
                setTimeout(poll, 1000);
            }
        } catch (error) {
            console.error('Progress poll error:', error);
            setTimeout(poll, 2000);
        }
    };
    
    poll();
}

function closeDownloadProgressModal() {
    const modal = document.getElementById('downloadProgressModal');
    if (modal) {
        modal.style.display = 'none';
        modal.classList.remove('active');
    }
}

async function triggerDownload(id, title) {
    showDownloadToast(`⬇️ Starting download for "${title}"...`, 'info');
    
    try {
        const downloadUrl = `${API_BASE}/audiobooks/${encodeURIComponent(id)}/download`;
        
        // For large files, use direct link download instead of fetch+blob
        // This lets the browser handle the download natively without memory limits
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = `${title.replace(/[^a-zA-Z0-9\s-]/g, '_')}.mp3`;
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        
        showDownloadToast(`✅ Download started for "${title}" - check your browser's downloads`, 'success');
        
    } catch (error) {
        console.error('Download error:', error);
        showDownloadToast(`❌ Download failed: ${error.message || 'Unknown error'}`, 'error');
    }
}

function showDownloadToast(message, type = 'info') {
    let toast = document.getElementById('downloadToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'downloadToast';
        document.body.appendChild(toast);
    }
    
    const bgColor = type === 'error' ? 'rgba(244,67,54,0.95)' : 
                    type === 'success' ? 'rgba(76,175,80,0.95)' : 
                    'rgba(255,255,255,0.95)';
    const textColor = type === 'info' ? '#000' : '#fff';
    
    toast.style.cssText = `
        position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%);
        background: ${bgColor}; color: ${textColor}; padding: 15px 25px;
        border-radius: 10px; z-index: 10000; box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        transition: opacity 0.3s;
    `;
    toast.textContent = message;
    toast.style.opacity = '1';
    
    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, type === 'error' ? 5000 : 3000);
}

// Check download status for audiobooks (for settings menu display)
async function getDownloadStatus(id) {
    try {
        return await apiCall(`/audiobooks/${encodeURIComponent(id)}/download-status`);
    } catch {
        return { status: 'not_started' };
    }
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
    
    // When opening, check download status for any download buttons
    if (isOpening) {
        const downloadBtn = menu.querySelector('.download-btn');
        if (downloadBtn) {
            const audiobookId = downloadBtn.dataset.audiobookId;
            if (audiobookId) {
                updateDownloadButtonStatus(downloadBtn, audiobookId);
            }
        }
    }
}

async function updateDownloadButtonStatus(button, audiobookId) {
    try {
        const status = await apiCall(`/audiobooks/${encodeURIComponent(audiobookId)}/download-status`);
        
        if (status.status === 'combining') {
            button.innerHTML = `⏳ Preparing (${status.progress || 0}%)`;
            button.style.background = 'linear-gradient(90deg, #4CAF50 ' + (status.progress || 0) + '%, transparent ' + (status.progress || 0) + '%)';
        } else if (status.status === 'ready') {
            button.innerHTML = `✅ Download (${status.file_size_mb || '?'} MB)`;
            button.style.background = '';
        } else {
            button.innerHTML = '⬇️ Download MP3';
            button.style.background = '';
        }
    } catch (error) {
        // Ignore errors, keep default text
    }
}

function closeAllSettingsMenus() {
    document.querySelectorAll('.settings-menu').forEach(menu => menu.style.display = 'none');
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('.settings-menu') && !e.target.classList.contains('settings-btn')) {
        closeAllSettingsMenus();
    }
});

// ========== GLOBAL EXPORTS ==========
window.showUpdateAudiobookDialog = showUpdateAudiobookDialog;
window.deleteAudiobook = deleteAudiobook;
window.downloadAudiobook = downloadAudiobook;
window.triggerDownload = triggerDownload;
window.closeDownloadProgressModal = closeDownloadProgressModal;
window.pauseAudiobook = pauseAudiobook;
window.resumeAudiobook = resumeAudiobook;
window.closeUpdateAudiobookModal = closeUpdateAudiobookModal;
window.toggleSettingsMenu = toggleSettingsMenu;
window.closeAllSettingsMenus = closeAllSettingsMenus;
window.navigateToAudiobookDirectory = navigateToAudiobookDirectory;
window.createAudiobookDirectory = createAudiobookDirectory;
window.moveAudiobookDialog = moveAudiobookDialog;
window.deleteAudiobookDir = deleteAudiobookDir;
