// file-manager.js — File browsing, upload, folders, move, delete, caching
// Depends on: app.js (apiCall, Helpers, showGenerateModal, etc.)

// ========== STATE ==========

const fileState = {
    current: '',
    all: [],
    moveMenu: { visible: false, source: '', isDirectory: false, dest: '' },
    activeEbookPath: null,
    pollInterval: null
};

// ========== CORE FILE OPERATIONS ==========

async function refreshFiles() {
    const container = document.getElementById('fileList');
    container.innerHTML = '<div class="loading">Loading files...</div>';

    try {
        const data = await apiCall(`/files/list?path=${encodeURIComponent(fileState.current)}`);
        fileState.all = data.files.filter(file => file.name !== '.gitkeep');
        await sortFiles();
        updateBreadcrumb();
    } catch (error) {
        container.innerHTML = '<div class="loading">Error loading files</div>';
    }
}

function navigateToDirectory(path) {
    fileState.current = path;
    refreshFiles();
}

function updateBreadcrumb() {
    const breadcrumb = document.getElementById('breadcrumb');
    if (!breadcrumb) return;
    renderBreadcrumb(breadcrumb, fileState.current, navigateToDirectory, '\uD83C\uDFE0 Home');
}

// ========== SEARCH ==========

/** Called on every keystroke in the search box — delegates to sortFiles */
function filterFiles() {
    sortFiles();
}

// ========== FILTERING & SORTING ==========

async function sortFiles() {
    const sortBy = document.getElementById('fileSort').value;
    const searchTerm = document.getElementById('fileSearch').value.toLowerCase();
    let filtered = fileState.all.filter(file => file.name.toLowerCase().includes(searchTerm));

    if (sortBy === 'recent') {
        try {
            const prefs = await fetch('/api/audiobooks/preferences/get').then(r => r.json());
            filtered = applySortToFiles(filtered, 'recent', prefs);
        } catch (error) {
            filtered = applySortToFiles(filtered, 'name');
        }
    } else {
        filtered = applySortToFiles(filtered, sortBy);
    }

    displayFiles(filtered);
}

function applySortToFiles(files, sortBy, userPrefs = null) {
    const sorted = [...files];
    sorted.sort((a, b) => {
        if (a.is_directory && !b.is_directory) return -1;
        if (!a.is_directory && b.is_directory) return 1;

        switch (sortBy) {
            case 'recent':
                if (userPrefs?.audiobooks) {
                    const aTime = userPrefs.audiobooks[a.path]?.last_played || 0;
                    const bTime = userPrefs.audiobooks[b.path]?.last_played || 0;
                    if (aTime === 0 && bTime === 0) return b.modified - a.modified;
                    return bTime - aTime;
                }
                return a.name.localeCompare(b.name);
            case 'name':
                return a.name.localeCompare(b.name);
            case 'modified':
            case 'added':
                return b.modified - a.modified;
            default:
                return a.name.localeCompare(b.name);
        }
    });
    return sorted;
}

// ========== DISPLAY ==========

function displayFiles(files) {
    const container = document.getElementById('fileList');
    if (files.length === 0) {
        container.innerHTML = '<div class="loading">No files found. Upload some ebooks to get started!</div>';
        return;
    }
    container.innerHTML = '';
    files.forEach(file => container.appendChild(createFileItem(file)));
}

function createFileItem(file) {
    const item = document.createElement('div');
    item.className = 'file-item';

    const icon = file.is_directory ? '\uD83D\uDCC1' : '\uD83D\uDCC4';
    const size = file.is_directory ? '' : formatBytes(file.size);
    const date = new Date(file.modified * 1000).toLocaleDateString();
    const isEbook = !file.is_directory && /\.(epub|txt|pdf)$/i.test(file.name);

    // File info section
    const fileInfo = document.createElement('div');
    fileInfo.className = 'file-info';

    if (file.is_directory || isEbook) {
        fileInfo.style.cursor = 'pointer';
        fileInfo.addEventListener('click', () =>
            file.is_directory ? navigateToDirectory(file.path) : openStreamMode(file.path)
        );
    }

    fileInfo.innerHTML = `
        <div class="file-name">${icon} ${file.name}</div>
        <div class="file-meta">${size} ${size && date ? '\u2022' : ''} ${date}</div>
    `;

    // Actions section
    const fileActions = document.createElement('div');
    fileActions.className = 'file-actions';

    if (!file.is_directory) {
        const genBtn = createButton('btn-small', '\uD83C\uDFB5', (e) => {
            e.stopPropagation();
            showGenerateModal(file.path);
        });
        fileActions.appendChild(genBtn);
    }

    const menuId = `file-${file.path.replace(/[^a-zA-Z0-9]/g, '_')}`;
    const settingsBtn = createButton('btn-small settings-btn', '\u2699\uFE0F', (e) => {
        e.stopPropagation();
        openFileSettingsPanel(file);
    });
    fileActions.appendChild(settingsBtn);

    item.appendChild(fileInfo);
    item.appendChild(fileActions);
    return item;
}

// ========== FILE SETTINGS PANEL ==========

function openFileSettingsPanel(file) {
    const existing = document.getElementById('fileSettingsPanel');
    if (existing) existing.remove();

    const panel = document.createElement('div');
    panel.id = 'fileSettingsPanel';
    panel.className = 'modal';
    panel.style.cssText = 'display: flex; z-index: 10001;';
    document.body.appendChild(panel);

    const isEbook = !file.is_directory && /\.(epub|txt|pdf)$/i.test(file.name);
    const bookTitle = file.name;

    panel.innerHTML = `
        <div class="modal-content" style="max-width: 800px; width: 95%; max-height: 90vh; overflow-y: auto; margin: auto;">
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; padding-bottom: 15px; border-bottom: 2px solid var(--border-color);">
                <div style="min-width: 0; /* allow title to shrink below content width */">
                    <h2 style="margin: 0 0 5px 0; font-size: 24px; overflow-wrap: break-word;">${isEbook ? '\uD83C\uDFB5' : '\uD83D\uDCC1'} ${bookTitle}</h2>
                    <p style="margin: 0; color: var(--text-secondary); font-size: 14px;">${file.is_directory ? 'Folder' : 'File'} \u00B7 ${file.is_directory ? '' : formatBytes(file.size)}</p>
                </div>
                <button onclick="closeFileSettingsPanel()" style="background: none; border: none; font-size: 28px; cursor: pointer; color: var(--text-secondary); padding: 5px 10px;">\u2715</button>
            </div>

            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 25px;">
                ${!file.is_directory ? `
                    <button onclick="downloadFile('${file.path}'); closeFileSettingsPanel();" class="btn" style="padding: 15px; font-size: 14px; display: flex; flex-direction: column; align-items: center; gap: 5px;">
                        <span style="font-size: 24px;">\u2B07\uFE0F</span><span>Download</span>
                    </button>
                ` : ''}
                ${isEbook ? `
                    <button onclick="showGenerateCacheModal('${file.path}')" class="btn btn-primary" style="padding: 15px; font-size: 14px; display: flex; flex-direction: column; align-items: center; gap: 5px;">
                        <span style="font-size: 24px;">\uD83C\uDFB5</span><span>Generate Audiobook</span>
                    </button>
                ` : ''}
                <button onclick="showMoveMenu('${file.path}', ${file.is_directory}); closeFileSettingsPanel();" class="btn" style="padding: 15px; font-size: 14px; display: flex; flex-direction: column; align-items: center; gap: 5px;">
                    <span style="font-size: 24px;">\u2194\uFE0F</span><span>Move</span>
                </button>
                <button onclick="deleteFile('${file.path}', ${file.is_directory}); closeFileSettingsPanel();" class="btn btn-danger" style="padding: 15px; font-size: 14px; display: flex; flex-direction: column; align-items: center; gap: 5px;">
                    <span style="font-size: 24px;">\uD83D\uDDD1\uFE0F</span><span>Delete</span>
                </button>
            </div>

            ${isEbook ? `
                <div id="audiobookManagementSection">
                    <h3 style="margin: 0 0 15px 0; font-size: 18px;">\uD83D\udcDA Audiobook Cache Management</h3>
                    <div id="audiobookContent" style="padding: 15px; background: rgba(255,255,255,0.03); border-radius: 8px; overflow-wrap: break-word; word-break: break-all;">
                        <div style="text-align: center; padding: 20px; color: var(--text-secondary);">Loading cache info...</div>
                    </div>
                </div>
            ` : ''}
        </div>
    `;

    panel.style.display = 'flex';
    panel.classList.add('active');

    if (isEbook) {
        loadAndRenderAudiobookStatus(file.path);
    }
}

function closeFileSettingsPanel() {
    if (fileState.pollInterval) {
        clearInterval(fileState.pollInterval);
        fileState.pollInterval = null;
    }
    fileState.activeEbookPath = null;

    const panel = document.getElementById('fileSettingsPanel');
    if (panel) {
        panel.style.display = 'none';
        panel.classList.remove('active');
        setTimeout(() => panel.remove(), 300);
    }
}

// ========== AUDIOWBOOK CACHE STATUS (rendered in settings panel) ==========

function loadAndRenderAudiobookStatus(ebookPath) {
    const contentEl = document.getElementById('audiobookContent');
    if (!contentEl) return;

    fetch(`${API_BASE}/stream/cache-info?ebook_path=${encodeURIComponent(ebookPath)}`)
        .then(r => r.json())
        .then(info => {
            if (!info || !info.has_cache || info.caches.length === 0) {
                contentEl.innerHTML = `
                    <div style="text-align: center; padding: 20px;">
                        <p style="color: var(--text-secondary); margin-bottom: 15px;">No audiobook cache found for this ebook</p>
                        <p style="color: var(--text-secondary); margin-bottom: 15px; font-size: 13px;">${info?.total_chunks || '?'} chunks available for generation</p>
                        <button class="btn btn-primary" style="padding: 10px 20px;" onclick="showGenerateCacheModal('${ebookPath}')">
                            \uD83C\uDFB5 Generate Audiobook
                        </button>
                    </div>
                `;
                return;
            }

            let html = '';
            const totalCacheSize = info.caches.reduce((sum, c) => sum + c.size_mb, 0);
            html += `
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; padding: 8px 12px; background: rgba(255,255,255,0.03); border-radius: 6px; font-size: 13px; color: var(--text-secondary);"><span style="min-width: 0; overflow-wrap: break-word;">\uD83D\udcDA ${info.title}</span>
                    <span>${info.total_chunks} chunks \u00B7 ${totalCacheSize.toFixed(1)} MB total</span>
                </div>
            `;

            info.caches.forEach(cache => {
                const statusIcon = cache.status === 'completed' ? '\u2705' :
                                   cache.status === 'in_progress' ? '\u23F3' :
                                   cache.status === 'paused' ? '\u23F8\uFE0F' : '\u274C';

                let progressHtml = '';
                if (cache.status === 'in_progress' && cache.total_chunks > 0) {
                    const progressPct = Math.round(cache.completed_chunks / cache.total_chunks * 100);
                    progressHtml = `
                        <div style="margin-top: 6px;">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                <span style="font-size: 11px; color: var(--text-secondary);">${cache.completed_chunks}/${cache.total_chunks} chunks \u00B7 ${progressPct}%</span>
                                <span style="font-size: 11px; color: #4fc3f7;">${cache.missing_count} remaining</span>
                            </div>
                            <div style="height: 4px; background: rgba(255,255,255,0.1); border-radius: 2px; overflow: hidden;">
                                <div style="height: 100%; width: ${progressPct}%; background: linear-gradient(90deg, #4fc3f7, #29b6f6); border-radius: 2px; transition: width 0.3s;"></div>
                            </div>
                        </div>
                    `;
                } else if (cache.status === 'completed') {
                    progressHtml = `<div style="margin-top: 6px;"><span style="font-size: 11px; color: #66bb6a;">\u2705 All ${cache.completed_chunks} chunks complete \u00B7 ${cache.size_mb} MB</span></div>`;
                } else if (cache.status === 'not_started') {
                    progressHtml = `<div style="margin-top: 6px;"><span style="font-size: 11px; color: var(--text-secondary);">\u23F8\uFE0F Not started yet</span></div>`;
                }

                let actionButtons = '';
                if (cache.status === 'completed') {
                    actionButtons += `
                        <button class="btn" style="padding: 6px 12px; font-size: 12px;" onclick="showDownloadFormatModal('${ebookPath}', '${cache.model}', '${cache.voice}')">\u2B07\uFE0F Download...</button>
                        <button class="btn" style="padding: 6px 12px; font-size: 12px;" onclick="handleCacheRegenerate('${ebookPath}', '${cache.model}', '${cache.voice}')">\uD83D\uDD04 Regenerate</button>
                    `;
                } else if (cache.status === 'in_progress') {
                    actionButtons += `<button class="btn" style="padding: 6px 12px; font-size: 12px;" onclick="handleCachePause('${ebookPath}', '${cache.model}', '${cache.voice}')">\u23F8\uFE0F Pause</button>`;
                } else if (cache.status === 'paused' || cache.status === 'failed') {
                    actionButtons += `<button class="btn" style="padding: 6px 12px; font-size: 12px;" onclick="handleCacheResume('${ebookPath}', '${cache.model}', '${cache.voice}')">\u25B6\uFE0F Resume Generation</button>`;
                }
                actionButtons += `<button class="btn btn-danger" style="padding: 6px 12px; font-size: 12px;" onclick="handleCacheDelete('${ebookPath}', '${cache.model}', '${cache.voice}')">\uD83D\uDDD1\uFE0F Delete Cache</button>`;

                html += `
                    <div style="margin-bottom: 15px; padding: 15px; background: rgba(255,255,255,0.05); border-radius: 8px; border-left: 3px solid ${cache.status === 'completed' ? '#66bb6a' : cache.status === 'in_progress' ? '#4fc3f7' : '#f44'};">
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;"><div style="min-width: 0;">
                                <span style="font-size: 16px; font-weight: bold;">${statusIcon} ${cache.model}</span>
                                <span style="color: var(--text-secondary); margin-left: 8px;">/ ${cache.voice}</span>
                            </div>
                            <span style="font-size: 12px; color: var(--text-secondary);">${cache.size_mb} MB</span>
                        </div>
                        ${progressHtml}
                        <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px;">${actionButtons}</div>
                    </div>
                `;
            });

            html += `
                <div style="text-align: center; margin-top: 15px; padding-top: 15px; border-top: 1px solid rgba(255,255,255,0.1);">
                    <button class="btn btn-primary" style="padding: 10px 20px;" onclick="showGenerateCacheModal('${ebookPath}')">
                        \u2795 Generate New Audiobook
                    </button>
                </div>
            `;

            contentEl.innerHTML = html;

            const hasInProgress = info.caches.some(c => c.status === 'in_progress');
            if (hasInProgress) {
                if (fileState.pollInterval) clearInterval(fileState.pollInterval);
                fileState.activeEbookPath = ebookPath;
                fileState.pollInterval = setInterval(() => loadAndRenderAudiobookStatus(ebookPath), 1500);
            } else {
                if (fileState.pollInterval) {
                    clearInterval(fileState.pollInterval);
                    fileState.pollInterval = null;
                }
                fileState.activeEbookPath = null;
            }
        })
        .catch(() => {
            contentEl.innerHTML = '<div style="text-align: center; padding: 20px; color: #f44;">Failed to load cache info</div>';
        });
}

// ========== CACHE-FIRST GENERATION MODAL (consolidated from 2 duplicates) ==========

function showGenerateCacheModal(ebookPath) {
    let modal = document.getElementById('generateCacheModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'generateCacheModal';
        modal.className = 'modal';
        modal.style.display = 'none';
        document.body.appendChild(modal);
    }

    modal.style.display = 'flex';
    modal.classList.add('active');
    modal.style.zIndex = '10003';

    modal.innerHTML = `
        <div class="modal-content" style="max-width: 500px; overflow-x: hidden; box-sizing: border-box;">
            <h2>\uD83C\uDFB5 Generate Audiobook</h2>
            <p style="margin-bottom: 15px; color: var(--text-secondary); word-break: break-all;">${ebookPath}</p>

            <label for="genCacheModel">Model:</label>
            <select id="genCacheModel" required onchange="updateGenCacheVoices()" style="width: 100%; padding: 8px; margin-bottom: 12px; border: 1px solid var(--border-color); border-radius: 4px; background: var(--bg-primary); color: var(--text-primary);">
                <option value="">Loading...</option>
            </select>

            <label for="genCacheVoice">Voice:</label>
            <select id="genCacheVoice" required style="width: 100%; padding: 8px; margin-bottom: 12px; border: 1px solid var(--border-color); border-radius: 4px; background: var(--bg-primary); color: var(--text-primary);">
                <option value="">Select model first</option>
            </select>

            <label for="genCacheInstructions">Instructions (optional):</label>
            <textarea id="genCacheInstructions" rows="3" placeholder="e.g., Speak in a cheerful tone." style="width: 100%; padding: 8px; margin-bottom: 15px; border: 1px solid var(--border-color); border-radius: 4px; font-family: inherit; font-size: inherit; resize: vertical;"></textarea>

            <div class="modal-buttons">
                <button type="button" onclick="handleCacheGenerate('${ebookPath}')" class="btn btn-primary">\uD83C\uDFB5 Generate</button>
                <button type="button" onclick="closeGenerateCacheModal()" class="btn">Cancel</button>
            </div>
        </div>
    `;

    loadGenCacheModels();
}

async function loadGenCacheModels() {
    try {
        const res = await fetch(`${API_BASE}/openai/models`);
        const models = await res.json();
        const select = document.getElementById('genCacheModel');
        select.innerHTML = '';
        for (const [name, model] of Object.entries(models)) {
            select.appendChild(createOption(name, model.name));
        }
        if (select.options.length > 0) updateGenCacheVoices();
    } catch (e) {
        document.getElementById('genCacheModel').innerHTML = '<option value="">Error loading models</option>';
    }
}

function updateGenCacheVoices() {
    const model = document.getElementById('genCacheModel').value;
    const voiceSelect = document.getElementById('genCacheVoice');
    voiceSelect.innerHTML = '';

    if (!model) {
        voiceSelect.innerHTML = '<option value="">Select model first</option>';
        return;
    }

    fetch(`${API_BASE}/openai/models/${model}/voices`)
        .then(r => r.json())
        .then(voices => {
            voices.forEach(voice => voiceSelect.appendChild(createOption(voice, voice)));
        })
        .catch(() => {
            voiceSelect.innerHTML = '<option value="">Error loading voices</option>';
        });
}

function closeGenerateCacheModal() {
    const modal = document.getElementById('generateCacheModal');
    if (modal) {
        modal.style.display = 'none';
        modal.classList.remove('active');
    }
}

async function handleCacheGenerate(ebookPath) {
    const model = document.getElementById('genCacheModel').value;
    const voice = document.getElementById('genCacheVoice').value;
    const instructions = document.getElementById('genCacheInstructions').value;

    if (!model || !voice) {
        showToast('Please select model and voice');
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/stream/generate-cache`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ebook_path: ebookPath, model, voice })
        });

        if (!res.ok) throw new Error('Failed to start generation');

        const data = await res.json();
        showToast(`Generation started: ${data.model}/${data.voice}`);
        closeGenerateCacheModal();
        loadAndRenderAudiobookStatus(ebookPath);
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

// ========== CACHE MANAGEMENT HANDLERS ==========

async function handleCacheRegenerate(ebookPath, model, voice) {
    if (!confirm(`Regenerate audiobook cache for ${model}/${voice}?\nThis will delete the existing cache and start fresh.`)) return;

    try {
        const delRes = await fetch(`${API_BASE}/stream/cache?ebook_path=${encodeURIComponent(ebookPath)}&model=${model}&voice=${voice}`, {
            method: 'DELETE'
        });
        if (!delRes.ok) throw new Error('Failed to delete cache');

        const genRes = await fetch(`${API_BASE}/stream/generate-cache`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ebook_path: ebookPath, model, voice })
        });
        if (!genRes.ok) throw new Error('Failed to start generation');

        showToast('Regenerating audiobook...');
        loadAndRenderAudiobookStatus(ebookPath);
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

async function handleCacheDelete(ebookPath, model, voice) {
    if (!confirm(`Delete audiobook cache for ${model}/${voice}?\nThis will remove all generated audio files.`)) return;

    try {
        const res = await fetch(`${API_BASE}/stream/cache?ebook_path=${encodeURIComponent(ebookPath)}&model=${model}&voice=${voice}`, {
            method: 'DELETE'
        });
        if (!res.ok) throw new Error('Failed to delete cache');

        const data = await res.json();
        showToast(`Cache deleted: ${data.deleted_size_mb} MB freed`);
        loadAndRenderAudiobookStatus(ebookPath);
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

async function handleCachePause(ebookPath, model, voice) {
    try {
        await fetch(`${API_BASE}/stream/cache-pause?ebook_path=${encodeURIComponent(ebookPath)}&model=${model}&voice=${voice}`, {
            method: 'POST'
        });
        showToast('Generation paused');
        loadAndRenderAudiobookStatus(ebookPath);
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

async function handleCacheResume(ebookPath, model, voice) {
    try {
        const res = await fetch(`${API_BASE}/stream/cache-resume?ebook_path=${encodeURIComponent(ebookPath)}&model=${model}&voice=${voice}`, {
            method: 'POST'
        });
        if (!res.ok) throw new Error('Failed to resume');

        showToast('Resuming generation...');
        loadAndRenderAudiobookStatus(ebookPath);
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

// ========== FILE OPERATIONS ==========

async function deleteFile(filePath, isDirectory) {
    const type = isDirectory ? 'directory' : 'file';
    if (!confirm(`Delete this ${type}?`)) return;

    try {
        await apiCall(`/files/delete?file_path=${encodeURIComponent(filePath)}`, { method: 'DELETE' });
        refreshFiles();
    } catch (error) {
        console.error('Delete error:', error);
    }
}

function downloadFile(filePath) {
    window.location.href = `${API_BASE}/files/download?file_path=${encodeURIComponent(filePath)}`;
}

function createDirectory() {
    const name = prompt('Enter directory name:');
    if (!name) return;
    const path = fileState.current ? `${fileState.current}/${name}` : name;
    createDir(path);
}

async function createDir(path) {
    try {
        await apiCall('/files/create-directory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path })
        });
        refreshFiles();
    } catch (error) {
        console.error('Create directory error:', error);
    }
}

async function moveFile(source, destination) {
    try {
        await apiCall('/files/move', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source, destination })
        });
        // Track as recently read when moving an ebook
        const isEbook = /\.(epub|txt|pdf)$/i.test(source);
        if (isEbook) {
            const fileName = source.split('/').pop();
            const newPath = destination ? `${destination}/${fileName}` : fileName;
            await trackAsRecentlyRead(newPath);
        }
        refreshFiles();
    } catch (error) {
        console.error('Move error:', error);
    }
}

// ========== MOVE MENU ==========

function showMoveMenu(filePath, isDirectory) {
    fileState.moveMenu = { visible: true, source: filePath, isDirectory, dest: '' };

    const menu = document.getElementById('moveMenu');
    menu.style.display = 'flex';
    menu.classList.add('active');
    menu.innerHTML = `
        <div class="modal-content" style="max-width:600px;">
            <h3 style="overflow-wrap: break-word;">Move ${isDirectory ? 'Directory' : 'File'}: <span id="moveSourceName" style="word-break: break-all;"></span></h3>
            <div id="moveNavBreadcrumb" class="breadcrumb"></div>
            <div id="moveNavList" style="max-height:300px;overflow-y:auto;margin-bottom:15px;"></div>
            <div class="modal-buttons">
                <button id="moveNewFolderBtn" class="btn">\uD83D\uDCC1 New Folder</button>
                <button id="moveHereBtn" class="btn btn-primary">Move Here</button>
                <button id="moveCancelBtn" class="btn btn-danger">Cancel</button>
            </div>
        </div>
    `;
    menu.querySelector('#moveSourceName').textContent = filePath.split('/').pop();
    renderMoveNav();

    menu.querySelector('#moveHereBtn').onclick = async () => {
        await moveFile(fileState.moveMenu.source, fileState.moveMenu.dest);
        hideMoveMenu();
    };
    menu.querySelector('#moveCancelBtn').onclick = hideMoveMenu;
    menu.querySelector('#moveNewFolderBtn').onclick = async () => {
        const name = prompt('Enter new folder name:');
        if (!name) return;
        const path = fileState.moveMenu.dest ? `${fileState.moveMenu.dest}/${name}` : name;
        await createDir(path);
        renderMoveNav();
    };
}

function hideMoveMenu() {
    fileState.moveMenu.visible = false;
    const menu = document.getElementById('moveMenu');
    if (menu) {
        menu.style.display = 'none';
        menu.classList.remove('active');
    }
}

async function renderMoveNav() {
    const navList = document.getElementById('moveNavList');
    const breadcrumb = document.getElementById('moveNavBreadcrumb');
    if (!navList || !breadcrumb) return;

    renderBreadcrumb(breadcrumb, fileState.moveMenu.dest, (path) => {
        fileState.moveMenu.dest = path;
        renderMoveNav();
    });

    navList.innerHTML = '';
    try {
        const data = await apiCall(`/files/list?path=${encodeURIComponent(fileState.moveMenu.dest)}`);
        const dirs = data.files.filter(f => f.is_directory && f.name !== '.gitkeep');

        if (dirs.length === 0) {
            navList.innerHTML = '<div class="loading">No folders found.</div>';
        } else {
            dirs.forEach(dir => {
                const item = document.createElement('div');
                item.className = 'file-item';
                item.innerHTML = `<div class="file-info"><div class="file-name">\uD83D\uDCC1 ${dir.name}</div></div>`;
                item.onclick = () => {
                    fileState.moveMenu.dest = dir.path;
                    renderMoveNav();
                };
                navList.appendChild(item);
            });
        }

        if (fileState.moveMenu.dest) {
            const upDiv = document.createElement('div');
            upDiv.className = 'file-item';
            upDiv.innerHTML = `<div class="file-info"><div class="file-name">\u2B06\uFE0F Up</div></div>`;
            upDiv.onclick = () => {
                const parts = fileState.moveMenu.dest.split('/').filter(Boolean);
                parts.pop();
                fileState.moveMenu.dest = parts.join('/');
                renderMoveNav();
            };
            navList.appendChild(upDiv);
        }
    } catch (e) {
        navList.innerHTML = '<div class="loading">Error loading folders</div>';
    }
}

// ========== TEXT FILE CREATION ==========

function showCreateTextFileModal() {
    const modal = document.getElementById('createTextFileModal');
    modal.style.display = 'flex';
    modal.classList.add('active');
    document.getElementById('newTextFileName').value = '';
    document.getElementById('newTextFileContent').value = '';
    document.getElementById('newTextFileName').focus();
}

function closeCreateTextFileModal() {
    const modal = document.getElementById('createTextFileModal');
    modal.style.display = 'none';
    modal.classList.remove('active');
}

async function handleCreateTextFile(e) {
    e.preventDefault();

    let filename = document.getElementById('newTextFileName').value.trim();
    const content = document.getElementById('newTextFileContent').value;

    if (!filename) {
        alert('Please enter a filename.');
        return;
    }

    if (!filename.includes('.')) {
        filename += '.txt';
    }

    const path = fileState.current ? `${fileState.current}/${filename}` : filename;

    try {
        await apiCall('/files/create-file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, content })
        });
        closeCreateTextFileModal();
        refreshFiles();
    } catch (error) {
        console.error('Create text file error:', error);
        alert(`Error creating file: ${error.message}`);
    }
}

// ========== UTILITIES ==========

function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
}

async function openStreamMode(filePath) {
    // Track as recently read before navigating away
    try { await trackAsRecentlyRead(filePath); } catch(e) {}
    window.location.href = `/stream?ebook=${encodeURIComponent(filePath)}`;
}

function showToast(msg) {
    let toast = document.getElementById('fileManagerToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'fileManagerToast';
        toast.style.cssText = 'position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.85); color: #fff; padding: 10px 20px; border-radius: 20px; z-index: 10000; font-size: 14px; opacity: 0; transition: opacity 0.3s;';
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.style.opacity = '1';
    setTimeout(() => { toast.style.opacity = '0'; }, 2000);
}

function createButton(className, text, onClick) {
    const btn = document.createElement('button');
    btn.className = className;
    btn.textContent = text;
    btn.addEventListener('click', onClick);
    return btn;
}

// ========== DUPLICATE DETECTION & POPUP MENU ==========

/**
 * Show the duplicate files popup menu.
 * Returns a promise that resolves to:
 *   - null if user cancels
 *   - { action: 'replace', paths: [...] }
 *   - { action: 'copy', paths: [...] }
 *   - { action: 'ignore', paths: [...] }
 *   - { action: 'upload_anyway', ignored: [...] }
 */
function showDuplicatePopupMenu(file, dupCheck) {
    return new Promise((resolve) => {
        const duplicates = dupCheck.duplicates || []
        if (duplicates.length === 0) {
            resolve(null); // No duplicates, caller should do normal upload
            return;
        }

        // Remove existing popup if any
        const existing = document.getElementById('duplicateUploadMenu');
        if (existing) existing.remove();

        const menu = document.createElement('div');
        menu.id = 'duplicateUploadMenu';
        menu.style.cssText = 'display: flex; z-index: 10004;';

        // Build rows for each duplicate
        let rowsHtml = '';
        duplicates.forEach((dup, idx) => {
            const totalCache = (dup.parse_cache_size_mb + dup.stream_cache_size_mb).toFixed(1);
            const isGenerating = dup.generation_status === 'in_progress';
            const genWarning = isGenerating
                ? `<div style="margin-top: 8px; padding: 6px 10px; background: rgba(255, 152, 0, 0.15); border-radius: 4px; font-size: 12px; color: #ffb74d;">⚠️ Audiobook generation in progress (${dup.generation_info?.model || 'unknown'}/${dup.generation_info?.voice || 'unknown'}). Replacing may interrupt it.</div>`
                : '';

            rowsHtml += `
                <div style="margin-bottom: 12px; padding: 15px; background: rgba(255,255,255,0.03); border-radius: 8px; border: 1px solid rgba(255,255,255,0.08);">
                    <div style="font-weight: 600; font-size: 15px; margin-bottom: 4px;">📄 ${dup.filename}</div>
                    <div style="font-size: 13px; color: var(--text-secondary); margin-bottom: 8px;">
                        ${formatBytes(dup.size)} · Path: ${dup.path}
                        ${dup.stream_cache_count > 0 ? ` · 🎵 ${dup.stream_cache_count} audiobook cache${dup.stream_cache_count > 1 ? 'es' : ''} · ${dup.stream_cache_size_mb.toFixed(1)} MB` : ''}
                        ${dup.parse_cache_size_mb > 0 ? ` · 📖 Parse cache: ${dup.parse_cache_size_mb.toFixed(1)} MB` : ''}
                    </div>
                    ${genWarning}
                    <div style="display: flex; gap: 8px; margin-top: 10px;">
                        <button class="btn btn-sm btn-replace" data-idx="${idx}" data-action="replace" style="flex: 1;">🔄 Replace</button>
                        <button class="btn btn-sm btn-copy" data-idx="${idx}" data-action="copy" style="flex: 1;">📋 Copy</button>
                        <button class="btn btn-sm btn-ignore" data-idx="${idx}" data-action="ignore" style="flex: 1;">⏭️ Ignore</button>
                    </div>
                </div>
            `;
        });

        menu.innerHTML = `
            <div style="max-width: 520px; width: 95%; margin: auto; padding: 24px; background: var(--bg-primary); border-radius: 12px; border: 1px solid var(--border-color); box-shadow: 0 20px 60px rgba(0,0,0,0.5);">
                <h3 style="margin: 0 0 16px 0; font-size: 18px;">📚 Uploading: ${file.name}</h3>
                <p style="margin: 0 0 16px; color: var(--text-secondary); font-size: 14px;">Found existing file(s) with the same name:</p>
                <div id="duplicateRows">${rowsHtml}</div>
                <div style="display: flex; gap: 10px; margin-top: 20px; padding-top: 16px; border-top: 1px solid rgba(255,255,255,0.1); justify-content: flex-end;">
                    <button id="dupCancelAll" class="btn btn-danger">Cancel</button>
                </div>
            </div>
        `;

        document.body.appendChild(menu);
        menu.style.display = 'flex';

        // Wire up per-file buttons — click to immediately close and proceed
        menu.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.idx);
                const action = btn.dataset.action;
                const dup = duplicates[idx];

                // Close popup immediately
                menu.style.display = 'none';
                menu.classList.remove('active');
                setTimeout(() => menu.remove(), 300);

                // Resolve with the action for this specific duplicate
                resolve({ action, paths: [dup.path] });
            });
        });

        // Cancel button
        menu.querySelector('#dupCancelAll').addEventListener('click', () => {
            menu.style.display = 'none';
            menu.classList.remove('active');
            setTimeout(() => menu.remove(), 300);
            resolve(null);
        });

        // Close on backdrop click
        menu.addEventListener('click', (e) => {
            if (e.target === menu) {
                menu.style.display = 'none';
                menu.classList.remove('active');
                setTimeout(() => menu.remove(), 300);
                resolve(null);
            }
        });
    });
}

// ========== FILE UPLOAD ==========

async function handleFileUpload(event) {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    const uploadPromises = Array.from(files).map(async (file) => {
        // Step 1: Check for duplicates BEFORE uploading
        let dupCheck = null;
        try {
            const checkParams = new URLSearchParams();
            checkParams.set('filename', file.name);
            checkParams.set('path', fileState.current);
            const checkUrl = `${API_BASE}/files/upload-check?${checkParams.toString()}`;
            const checkRes = await fetch(checkUrl);
            if (checkRes.ok) {
                dupCheck = await checkRes.json();
            }
        } catch (e) {
            console.error('[UPLOAD] Duplicate check failed:', e);
        }

        // Step 2: Handle duplicates if any
        if (dupCheck && dupCheck.has_duplicates && dupCheck.duplicates.length > 0) {
            const actions = await showDuplicatePopupMenu(file, dupCheck);

            if (actions === null) {
                // User cancelled
                return null;
            }

            const formData = new FormData();
            formData.append('file', file);
            formData.append('path', fileState.current);

            try {
                if (actions.action === 'replace') {
                    // Upload with replace cache
                    const params = new URLSearchParams();
                    params.set('path', fileState.current);
                    actions.paths.forEach(p => params.append('replace_paths', p));
                    const url = `${API_BASE}/files/upload-with-replace-cache?${params.toString()}`;
                    const res = await fetch(url, { method: 'POST', body: formData });
                    if (!res.ok) throw new Error(await res.text());
                    return file.name;
                } else if (actions.action === 'copy') {
                    // Upload with copy cache
                    const params = new URLSearchParams();
                    params.set('path', fileState.current);
                    actions.paths.forEach(p => params.append('copy_paths', p));
                    const url = `${API_BASE}/files/upload-with-copy-cache?${params.toString()}`;
                    const res = await fetch(url, { method: 'POST', body: formData });
                    if (!res.ok) throw new Error(await res.text());
                    return file.name;
                } else {
                    // Upload with ignore cache
                    const params = new URLSearchParams();
                    params.set('path', fileState.current);
                    actions.paths.forEach(p => params.append('ignored_paths', p));
                    const url = `${API_BASE}/files/upload-ignore-cache?${params.toString()}`;
                    const res = await fetch(url, { method: 'POST', body: formData });
                    if (!res.ok) throw new Error(await res.text());
                    return file.name;
                }
            } catch (error) {
                console.error(`Failed to upload ${file.name}:`, error);
                return null;
            }
        }

        // No duplicates — normal upload
        const formData = new FormData();
        formData.append('file', file);
        formData.append('path', fileState.current);

        try {
            const url = `${API_BASE}/files/upload?path=${encodeURIComponent(fileState.current)}`;
            const res = await fetch(url, {
                method: 'POST',
                body: formData
            });
            if (!res.ok) throw new Error(await res.text());
            return file.name;
        } catch (error) {
            console.error(`Failed to upload ${file.name}:`, error);
            return null;
        }
    });

    const results = await Promise.all(uploadPromises);
    const uploaded = results.filter(Boolean);
    const failed = results.length - uploaded.length;

    // Track uploaded ebooks as recently read so they sort to top under "recent"
    for (const fileName of uploaded) {
        const isEbook = /\.(epub|txt|pdf)$/i.test(fileName);
        if (isEbook) {
            const fullPath = fileState.current ? `${fileState.current}/${fileName}` : fileName;
            await trackAsRecentlyRead(fullPath).catch(() => {});
        }
    }

    if (uploaded.length > 0) {
        showToast(`Uploaded ${uploaded.length} file(s)${failed > 0 ? `, ${failed} failed` : ''}`);
        refreshFiles();
    } else if (failed > 0) {
        alert(`Failed to upload ${failed} file(s). Check console for details.`);
    }

    // Reset input so the same file can be uploaded again
    event.target.value = '';
}

// ========== FORMAT SELECTION MODAL + PROGRESS TRACKING (file-manager) ==========

// ========== DOWNLOAD STATE (module-scoped) ==========

let downloadPollInterval = null;    // Interval ID for polling download progress
let _downloadProgressModalRef = null; // Reference to the progress overlay element
let _fm_downloadParams = null;  // Shared state for format selection modal

/** Show the same format selection modal from stream-cache.js */
function showDownloadFormatModal(ebookPath, model, voice) {
    window._downloadParams = { ebookPath, model, voice };
    
    const existingOverlay = document.getElementById('_fm_formatSelectModal');
    if (existingOverlay) existingOverlay.remove();
    
    const overlay = document.createElement('div');
    overlay.className = 'modal';
    overlay.id = '_fm_formatSelectModal';
    overlay.style.cssText = 'display: flex; z-index: 10002;';
    
    // Store params on the modal for later retrieval
    overlay.dataset.ebookPath = ebookPath;
    overlay.dataset.model = model;
    overlay.dataset.voice = voice;
    
    overlay.innerHTML = `
        <div class="modal-content" style="max-width:400px;">
            <h2>📥 Download Format</h2>
            <p style="font-size:13px;color:var(--text-secondary);margin-bottom:16px;">Choose your preferred format:</p>
            
            <!-- OPUS -->
            <div class="format-option" onclick="startFormatConversion('opus')"
                 style="padding:14px 16px;margin-bottom:8px;border:2px solid var(--border-color);border-radius:8px;cursor:pointer;display:flex;align-items:center;gap:12px;">
                <div>
                    <strong>OPUS (.opus)</strong><br>
                    <span style="font-size:12px;color:var(--text-secondary)">Lossless concat • Smallest size</span>
                </div>
            </div>
            
            <!-- M4B -->  
            <div class="format-option" onclick="startFormatConversion('m4b')"
                 style="padding:14px 16px;margin-bottom:8px;border:2px solid var(--border-color);border-radius:8px;cursor:pointer;display:flex;align-items:center;gap:12px;">
                <div>
                    <strong>M4B with Chapters (.m4b)</strong><br>
                    <span style="font-size:12px;color:var(--text-secondary)">AAC re-encode • Chapter metadata embedded</span>
                </div>
            </div>
            
            <!-- MP3 -->
            <div class="format-option" onclick="startFormatConversion('mp3')"
                 style="padding:14px 16px;margin-bottom:8px;border:2px solid var(--border-color);border-radius:8px;cursor:pointer;display:flex;align-items:center;gap:12px;">
                <div>
                    <strong>MPEG-3 (.mp3)</strong><br>  
                    <span style="font-size:12px;color:var(--text-secondary)">Re-encoded mono • Universal compatibility</span>
                </div>
            </div>
            
            <!-- Cancel -->
            <button type="button" onclick="closeFormatMenu()" class="btn btn-primary"
                    style="margin-top:8px;width:100%;padding:10px;">Cancel</button>
        </div>
    `;
    
    document.body.appendChild(overlay);
}

function closeFormatMenu() {
    const el = document.getElementById('_fm_formatSelectModal');
    if (el) {
        el.style.display = 'none';
        el.classList.remove('active');
        setTimeout(() => el.remove(), 300);
    }
}

/** Called when user selects a format from the menu */
async function startFormatConversion(formatType) {
    const params = window._downloadParams;
    closeFormatMenu();
    
    showToast(`Starting ${formatType.toUpperCase()} download...`);
    showDownloadProgressOverlay(formatType);
    
    try {
        // 1. Start the job
        const res = await fetch(`${API_BASE}/stream/download-start?ebook_path=${encodeURIComponent(params.ebookPath)}&model=${params.model}&voice=${params.voice}&format_type=${formatType}`, { method: 'POST' });
        
        if (!res.ok) throw new Error('Failed to start download');  
        const job = await res.json();

        // 2. Start polling for progress updates (every second during conversion)
        pollDownloadProgress(job.job_id, params.ebookPath);
    } catch (error) {
        hideDownloadProgressOverlay();
        showToast('Error: ' + error.message, true);
        console.error('[DOWNLOAD] Error starting download:', error);
    }
}

/** Show a progress overlay with real-time updates */  
function showDownloadProgressOverlay(formatType) {
    const existing = document.getElementById('_fm_downloadProgressModal');
    if (existing) existing.remove();  // safety: remove any leftover
    
    const overlay = document.createElement('div');
    overlay.id = '_fm_downloadProgressModal';
    overlay.style.cssText = 'display:flex; z-index:10003; position:fixed; top:0;left:0;width:100%;height:100%; background:rgba(0,0,0,0.6); align-items:center; justify-content:center;';
    
    overlay.innerHTML = `
        <div style="background:var(--bg-primary); border-radius:12px; padding:32px; min-width:280px; text-align:center; box-shadow:0 20px 60px rgba(0,0,0,0.5);">
            <div id="_fm_progressBarOuter" style="width:100%;margin-bottom:16px;display:none;">
                <div style="background:var(--bg-tertiary);border-radius:4px;height:8px;overflow:hidden;margin-bottom:8px;">
                    <div id="_fm_progressBarInner" style="height:100%;width:0%;background:#ff950a;transition:width 0.3s;"></div>
                </div>
                <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-secondary);">
                    <span id="_fm_progressPct">0%</span>  
                    <span id="_fm_formatBadge">${formatType.toUpperCase()}</span>
                </div>
            </div>
            <!-- Inline spinner (no CSS dependency) -->
            <div style="width:48px;height:48px;border:4px solid var(--border-color); border-top-color:#ff950a; border-radius:50%; animation:_fm_spin 0.8s linear infinite; margin:0 auto 16px;"></div>
            <style>@keyframes _fm_spin{to{transform:rotate(360deg)}}</style>
            <div id="_fm_progressMessage" style="font-size:14px;color:var(--text-primary);">Starting conversion...</div>
        </div>
    `;
    
    document.body.appendChild(overlay);
    _downloadProgressModalRef = overlay;
}

/** Update the download progress overlay with job data */  
function updateDownloadProgressUI(job) {
    const pctEl = document.getElementById('_fm_progressPct');
    const barInner = document.getElementById('_fm_progressBarInner');  
    const msgEl = document.getElementById('_fm_progressMessage');
    const barOuter = document.getElementById('_fm_progressBarOuter');
    
    if (job.status === 'converting' && job.progress_pct > 0) {
        // Show progress bar for active conversions
        barOuter.style.display = 'block';
        pctEl.textContent = `${job.progress_pct}%`;
        barInner.style.width = `${job.progress_pct}%`;
        
        if (msgEl) msgEl.textContent = job.message || 'Converting...';
    } else {
        // Don't show progress bar for initial pending state  
        barOuter.style.display = 'none';
        if (msgEl) msgEl.textContent = job.message;
    }
}

/** Hide the download progress overlay and remove it from DOM */
function hideDownloadProgressOverlay() {
    if (_downloadProgressModalRef) {
        _downloadProgressModalRef.style.display = 'none';
        _downloadProgressModalRef.classList.remove('active');
        const el = _downloadProgressModalRef;
        setTimeout(() => { try { el.remove(); } catch(e) {} }, 300);
        _downloadProgressModalRef = null;
    }
}

/** Poll download-progress endpoint every second during conversion */
function pollDownloadProgress(jobId, ebookPath) {
    const poll = async () => {
        try {
            const res = await fetch(`${API_BASE}/stream/download-progress/${jobId}`);  
            
            if (!res.ok) throw new Error('Failed to get progress');  
            
            const job = await res.json();
            updateDownloadProgressUI(job);
            
            // Completed or failed — stop polling, handle result
            if (job.status === 'ready') {
                clearInterval(downloadPollInterval);  
                downloadPollInterval = null;
                hideDownloadProgressOverlay();
                
                showToast(`Download ready! Starting download as .${job.format_type}`);
                setTimeout(() => { try { downloadByJobId(job.job_id); } catch(e) {
                    console.error('[DOWNLOAD] Download failed:', e);
                    // Fallback: open in new tab so user can manually save
                    window.open(`${API_BASE}/stream/download/${job.job_id}`, '_blank');
                }}, 500);
            } else if (job.status === 'failed') {
                clearInterval(downloadPollInterval);
                downloadPollInterval = null;
                _downloadProgressModalRef = null; // also clean up reference
                hideDownloadProgressOverlay();
                
                const errorMsg = job.error_message || 'Conversion failed';
                showToast(`Conversion failed: ${errorMsg}`, true);
                console.log('[DOWNLOAD] Job failed:', job);
            }
        } catch (error) {
            // Stop polling if the interval was already cleared (job succeeded/failed)
            if (!downloadPollInterval) return;
            // Don't show toast — polling errors are expected between updates
            console.error('[PROGRESS POLL] Error:', error);
        }
    };
    
    poll();  // First poll immediately
    downloadPollInterval = setInterval(poll, 1000);  // Poll every second during conversion
}

/** Download the completed file by job ID */
async function downloadByJobId(jobId) {
    const url = `${API_BASE}/stream/download/${jobId}`;  
    const a = document.createElement('a');
    a.href = url;
    a.download = '';  // Let server determine filename from Content-Disposition header
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

// ========== GLOBAL EXPORTS ==========

window.createDirectory = createDirectory;
window.openFileSettingsPanel = openFileSettingsPanel;
window.closeFileSettingsPanel = closeFileSettingsPanel;
window.openStreamMode = openStreamMode;
window.showCreateTextFileModal = showCreateTextFileModal;
window.closeCreateTextFileModal = closeCreateTextFileModal;
window.showGenerateCacheModal = showGenerateCacheModal;
window.closeGenerateCacheModal = closeGenerateCacheModal;
window.loadGenCacheModels = loadGenCacheModels;
window.updateGenCacheVoices = updateGenCacheVoices;
window.handleCacheGenerate = handleCacheGenerate;
window.handleCacheRegenerate = handleCacheRegenerate;
window.handleCacheDelete = handleCacheDelete;
window.handleCachePause = handleCachePause;
window.handleCacheResume = handleCacheResume;
window.showDownloadFormatModal = showDownloadFormatModal;
window.closeFormatMenu = closeFormatMenu;
window.startFormatConversion = startFormatConversion;
window.downloadByJobId = downloadByJobId;

// Bind form submit
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('createTextFileForm');
    if (form) {
        form.addEventListener('submit', handleCreateTextFile);
    }
});
