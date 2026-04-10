// ========== FILE MANAGER ==========
// Consolidated state to avoid conflicts
const fileState = {
    current: '',
    all: [],
    moveMenu: { visible: false, source: '', isDirectory: false, dest: '' }
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

    // Reuse Helpers.renderBreadcrumb from app.js if available, otherwise inline
    if (window.Helpers && Helpers.renderBreadcrumb) {
        Helpers.renderBreadcrumb(breadcrumb, fileState.current, navigateToDirectory, '🏠 Home');
    } else {
        // Inline fallback
        breadcrumb.innerHTML = '';
        const homeLink = document.createElement('span');
        homeLink.className = 'breadcrumb-item';
        homeLink.textContent = '🏠 Home';
        homeLink.onclick = () => navigateToDirectory('');
        breadcrumb.appendChild(homeLink);

        if (fileState.current) {
            const parts = fileState.current.split('/').filter(p => p);
            let accumulated = '';
            parts.forEach(part => {
                breadcrumb.appendChild(document.createTextNode(' / '));
                accumulated += (accumulated ? '/' : '') + part;
                const link = document.createElement('span');
                link.className = 'breadcrumb-item';
                link.textContent = part;
                const p = accumulated;
                link.onclick = () => navigateToDirectory(p);
                breadcrumb.appendChild(link);
            });
        }
    }
}

// ========== FILTERING & SORTING ==========
function filterFiles() {
    const searchTerm = document.getElementById('fileSearch').value.toLowerCase();
    const filtered = fileState.all.filter(file => file.name.toLowerCase().includes(searchTerm));
    displayFiles(filtered);
}

async function sortFiles() {
    const sortBy = document.getElementById('fileSort').value;
    const searchTerm = document.getElementById('fileSearch').value.toLowerCase();
    let filtered = fileState.all.filter(file => file.name.toLowerCase().includes(searchTerm));

    if (sortBy === 'recent') {
        try {
            const prefs = await fetch('/api/audiobooks/preferences/get').then(r => r.json());
            filtered = applySortToFiles(filtered, 'recent', prefs);
        } catch (error) {
            console.log('No preferences found, using alphabetical order');
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
                    if (aTime === 0 && bTime === 0) return b.modified - a.modified; // Sort unplayed by date added (newest first)
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

    const icon = file.is_directory ? '📁' : '📄';
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
        <div class="file-meta">${size} ${size && date ? '•' : ''} ${date}</div>
    `;

    // Actions section
    const fileActions = document.createElement('div');
    fileActions.className = 'file-actions';

    // Generate button for files
    if (!file.is_directory) {
        const genBtn = createButton('btn-small', '🎵', (e) => {
            e.stopPropagation();
            showGenerateModal(file.path);
        });
        fileActions.appendChild(genBtn);
    }

    // Settings menu
    const menuId = `file-${file.path.replace(/[^a-zA-Z0-9]/g, '_')}`;
    const settingsBtn = createButton('btn-small settings-btn', '⚙️', (e) => {
        e.stopPropagation();
        toggleFileSettingsMenu(e, menuId);
    });
    fileActions.appendChild(settingsBtn);

    const settingsMenu = createSettingsMenu(file, menuId);
    fileActions.appendChild(settingsMenu);

    item.appendChild(fileInfo);
    item.appendChild(fileActions);
    return item;
}

function createSettingsMenu(file, menuId) {
    const menu = document.createElement('div');
    menu.id = `settings-${menuId}`;
    menu.className = 'settings-menu';
    menu.style.display = 'none';

    const actions = [];

    // Download (files only)
    if (!file.is_directory) {
        actions.push(createMenuButton('⬇️ Download', () => {
            downloadFile(file.path);
            closeAllFileSettingsMenus();
        }));
    }

    // Move
    actions.push(createMenuButton('↔️ Move', () => {
        showMoveMenu(file.path, file.is_directory);
        closeAllFileSettingsMenus();
    }));

    // Delete
    actions.push(createMenuButton('🗑️ Delete', () => {
        deleteFile(file.path, file.is_directory);
        closeAllFileSettingsMenus();
    }, 'btn-danger'));

    actions.forEach(btn => menu.appendChild(btn));
    return menu;
}

function createButton(className, text, onClick) {
    const btn = document.createElement('button');
    btn.className = className;
    btn.textContent = text;
    btn.addEventListener('click', onClick);
    return btn;
}

function createMenuButton(text, onClick, extraClass = '') {
    const btn = document.createElement('button');
    btn.className = `settings-menu-item ${extraClass}`.trim();
    btn.textContent = text;
    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        onClick();
    });
    return btn;
}

// ========== FILE OPERATIONS ==========
async function handleFileUpload(e) {
    const files = e.target.files;
    if (!files.length) return;

    for (const file of files) {
        const formData = new FormData();
        formData.append('file', file);

        try {
            await fetch(`${API_BASE}/files/upload?path=${encodeURIComponent(fileState.current)}`, {
                method: 'POST',
                body: formData
            });
            // Track as recently read when uploading an ebook
            const isEbook = /\.(epub|txt|pdf)$/i.test(file.name);
            if (isEbook) {
                const filePath = fileState.current ? `${fileState.current}/${file.name}` : file.name;
                await trackFileAsRecentlyRead(filePath);
            }
        } catch (error) {
            alert(`Error uploading ${file.name}: ${error.message}`);
        }
    }

    e.target.value = '';
    refreshFiles();
}

async function deleteFile(filePath, isDirectory) {
    const type = isDirectory ? 'directory' : 'file';
    if (!confirm(`Delete this ${type}?`)) return;

    try {
        await apiCall(`/files/delete?file_path=${encodeURIComponent(filePath)}`, { method: 'DELETE' });
        await refreshFiles();
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
        await refreshFiles();
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
            await trackFileAsRecentlyRead(newPath);
        }
        await refreshFiles();
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
            <h3>Move ${isDirectory ? 'Directory' : 'File'}: <span id="moveSourceName"></span></h3>
            <div id="moveNavBreadcrumb" class="breadcrumb"></div>
            <div id="moveNavList" style="max-height:300px;overflow-y:auto;margin-bottom:15px;"></div>
            <div class="modal-buttons">
                <button id="moveNewFolderBtn" class="btn">📁 New Folder</button>
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

    // Breadcrumb
    if (window.Helpers && Helpers.renderBreadcrumb) {
        Helpers.renderBreadcrumb(breadcrumb, fileState.moveMenu.dest, (path) => {
            fileState.moveMenu.dest = path;
            renderMoveNav();
        });
    } else {
        // Inline fallback
        breadcrumb.innerHTML = '';
        const homeLink = document.createElement('span');
        homeLink.className = 'breadcrumb-item';
        homeLink.textContent = '🏠 Home';
        homeLink.onclick = () => { fileState.moveMenu.dest = ''; renderMoveNav(); };
        breadcrumb.appendChild(homeLink);

        if (fileState.moveMenu.dest) {
            const parts = fileState.moveMenu.dest.split('/').filter(p => p);
            let accumulatedPath = '';
            parts.forEach(part => {
                breadcrumb.appendChild(document.createTextNode(' / '));
                accumulatedPath += (accumulatedPath ? '/' : '') + part;
                const link = document.createElement('span');
                link.className = 'breadcrumb-item';
                link.textContent = part;
                const path = accumulatedPath;
                link.onclick = () => { fileState.moveMenu.dest = path; renderMoveNav(); };
                breadcrumb.appendChild(link);
            });
        }
    }

    // Directory list
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
                item.innerHTML = `<div class="file-info"><div class="file-name">📁 ${dir.name}</div></div>`;
                item.onclick = () => {
                    fileState.moveMenu.dest = dir.path;
                    renderMoveNav();
                };
                navList.appendChild(item);
            });
        }

        // "Up" navigation
        if (fileState.moveMenu.dest) {
            const upDiv = document.createElement('div');
            upDiv.className = 'file-item';
            upDiv.innerHTML = `<div class="file-info"><div class="file-name">⬆️ Up</div></div>`;
            upDiv.onclick = () => {
                const parts = fileState.moveMenu.dest.split('/').filter(p => p);
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

// ========== RECENTLY READ TRACKING ==========
async function trackFileAsRecentlyRead(filePath) {
    try {
        const prefs = await fetch(`${API_BASE}/audiobooks/preferences/get`).then(r => r.json());
        if (!prefs.audiobooks) prefs.audiobooks = {};
        prefs.audiobooks[filePath] = { last_played: Date.now() };

        await fetch(`${API_BASE}/audiobooks/preferences/save`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(prefs)
        });
        console.log(`[TRACKING] Marked file as recently read: ${filePath}`);
    } catch (error) {
        console.error('[TRACKING] Failed to track file as recently read:', error);
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

function openStreamMode(filePath) {
    window.location.href = `/stream?ebook=${encodeURIComponent(filePath)}`;
}

// ========== SETTINGS MENU ==========
function toggleFileSettingsMenu(event, menuId) {
    event.stopPropagation();
    const menu = document.getElementById('settings-' + menuId);
    if (!menu) return;

    document.querySelectorAll('.settings-menu').forEach(m => {
        if (m.id !== 'settings-' + menuId) m.style.display = 'none';
    });

    menu.style.display = menu.style.display === 'none' || !menu.style.display ? 'block' : 'none';
}

function closeAllFileSettingsMenus() {
    document.querySelectorAll('.settings-menu').forEach(menu => menu.style.display = 'none');
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('.settings-menu') && !e.target.classList.contains('settings-btn')) {
        closeAllFileSettingsMenus();
    }
});

// ========== GLOBAL EXPORTS ==========
window.createDirectory = createDirectory;
window.toggleFileSettingsMenu = toggleFileSettingsMenu;
window.closeAllFileSettingsMenus = closeAllFileSettingsMenus;
window.openStreamMode = openStreamMode;
