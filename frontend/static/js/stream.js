// stream.js — Main glue: init, modals, settings, sleep timer, touch, navigation, bookmarks
// Depends on: stream-state.js, stream-audio.js, stream-text.js, stream-cache.js

// ========== INITIALIZATION ==========

document.addEventListener('DOMContentLoaded', async () => {
    log('Initializing with ebook:', EBOOK_PATH);
    trackFilePlayback(EBOOK_PATH).catch(err => _warn('[TRACKING] Skipped:', err.message));

    await Promise.all([loadSettings(), loadModels(), loadProgress()]);
    await parseBook();

    if (state.progress?.current_chunk != null && state.progress.current_chunk !== undefined) {
        log('Restoring position to chunk:', state.progress.current_chunk);
        state.currentChunk = state.progress.current_chunk;
        highlightCurrentChunk();
        updateProgress();
        scrollToCurrentChunk();
    }

    setupAudioPlayer();
    startAudioWatchdog();
    applyDisplaySettings();
    initSleepTimer();

    // Load cache status on initial page load (elements exist in DOM)
    refreshCacheStatus().catch(() => {});
});

window.addEventListener('beforeunload', () => {
    if (state.audioWatchdogInterval) clearInterval(state.audioWatchdogInterval);
    if (state.currentAudioBlobUrl) URL.revokeObjectURL(state.currentAudioBlobUrl);
    if (state.chunkObserver) { state.chunkObserver.disconnect(); state.chunkObserver = null; }
    state.audioCache.clear();
    state.inFlightControllers.forEach(c => { try { c.abort(); } catch (e) { } });
    state.inFlightControllers.clear();
});

// ========== PLAYBACK TRACKING ==========

/** Track file playback in user preferences */
async function trackFilePlayback(filePath) {
    try {
        const res = await fetch(`${API_BASE}/audiobooks/preferences/get`);
        const prefs = await res.json();
        if (!prefs.audiobooks) prefs.audiobooks = {};
        prefs.audiobooks[filePath] = { last_played: Date.now() };
        await fetch(`${API_BASE}/audiobooks/preferences/save`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(prefs)
        });
    } catch (error) {
        logError('Failed to track playback', error);
    }
}

// ========== PROGRESS & TIME DISPLAY ==========

/** Update the audiobook completion indicator */
async function updateAudiobookIndicator() {
    const indicator = document.getElementById('audiobookCompleteIndicator');
    if (!indicator) return;

    try {
        const res = await fetch(`${API_BASE}/stream/cache-info?ebook_path=${encodeURIComponent(EBOOK_PATH)}`);
        if (!res.ok) return;
        const data = await res.json();
        const caches = data.caches || [];
        const hasCompleted = caches.some(c => c.status === 'completed');

        if (hasCompleted) {
            indicator.style.display = 'inline';
            const titles = caches.filter(c => c.status === 'completed')
                .map(c => `${c.model}/${c.voice}`).join(', ');
            indicator.title = `Audiobook(s) ready: ${titles}`;
        } else {
            indicator.style.display = 'none';
        }
    } catch (error) {
        // Silently fail — indicator updates on next poll
    }
}

/** Update progress display (chapter or book mode) */
function updateProgress() {
    if (!state.book) return;
    const speed = parseFloat(DOM.speedControl?.value || 1);

    if (state.progressMode === 'chapter') {
        const chapter = getCurrentChapter();
        if (!chapter) return updateProgressBookMode(speed);

        const chapterChunks = chapter.end_chunk - chapter.start_chunk + 1;
        const chapterCurrentChunk = state.currentChunk - chapter.start_chunk;
        const progressPercent = (chapterCurrentChunk / chapterChunks) * 100;

        const currentChar = state.currentChunk < state.book.chunks.length
            ? state.book.chunks[state.currentChunk].start_idx
            : chapter.end_idx;
        const elapsedChars = currentChar - chapter.start_idx;
        const remainingChars = chapter.end_idx - currentChar;

        const elapsed = elapsedChars / CHARS.PER_SECOND / speed;
        const remaining = remainingChars / CHARS.PER_SECOND / speed;
        const total = chapter.length / CHARS.PER_SECOND / speed;

        DOM.progressBar.value = Math.max(0, Math.min(100, progressPercent));
        DOM.totalProgress.textContent = `${Math.round(progressPercent)}% (Ch)`;
        DOM.currentPosition.textContent = `Chunk ${chapterCurrentChunk + 1} / ${chapterChunks} in chapter`;
        DOM.timeEstimate.textContent = state.timeMode === 'remaining'
            ? `~${formatTime(elapsed)} / -${formatTime(remaining)} left`
            : `~${formatTime(elapsed)} / ~${formatTime(total)}`;
    } else {
        updateProgressBookMode(speed);
    }
}

function updateProgressBookMode(speed) {
    const progressPercent = (state.currentChunk / state.book.total_chunks) * 100;
    const currentChar = state.currentChunk < state.book.chunks.length
        ? state.book.chunks[state.currentChunk].start_idx
        : state.book.total_chars;
    const remainingChars = state.book.total_chars - currentChar;

    const elapsed = currentChar / CHARS.PER_SECOND / speed;
    const remaining = remainingChars / CHARS.PER_SECOND / speed;
    const total = state.book.total_chars / CHARS.PER_SECOND / speed;

    DOM.progressBar.value = progressPercent;
    DOM.totalProgress.textContent = `${Math.round(progressPercent)}%`;
    DOM.currentPosition.textContent = `Chunk ${state.currentChunk + 1} / ${state.book.total_chunks}`;
    DOM.timeEstimate.textContent = state.timeMode === 'remaining'
        ? `~${formatTime(elapsed)} / -${formatTime(remaining)} left`
        : `~${formatTime(elapsed)} / ~${formatTime(total)}`;
}

/** Get the chapter containing the current chunk */
function getCurrentChapter() {
    if (!state.book || state.currentChunk >= state.book.chunks.length) return null;
    const currentChar = state.book.chunks[state.currentChunk].start_idx;
    return state.book.chapters.find(ch => currentChar >= ch.start_idx && currentChar < ch.end_idx);
}

/** Update progress display for seek bar dragging */
function updateProgressVisual(percent) {
    if (!state.book) return;
    const speed = parseFloat(DOM.speedControl?.value || 1);
    const progressPercent = (state.currentChunk / state.book.total_chunks) * 100;

    DOM.totalProgress.textContent = `${Math.round(percent)}%`;
    DOM.currentPosition.textContent = `Chunk ${state.currentChunk + 1} / ${state.book.total_chunks}`;

    const currentChar = state.currentChunk < state.book.chunks.length
        ? state.book.chunks[state.currentChunk].start_idx
        : state.book.total_chars;
    const remainingChars = state.book.total_chars - currentChar;

    const elapsed = currentChar / CHARS.PER_SECOND / speed;
    const remaining = remainingChars / CHARS.PER_SECOND / speed;
    const total = state.book.total_chars / CHARS.PER_SECOND / speed;

    DOM.timeEstimate.textContent = state.timeMode === 'remaining'
        ? `~${formatTime(elapsed)} / -${formatTime(remaining)} left`
        : `~${formatTime(elapsed)} / ~${formatTime(total)}`;
}

function updatePlayButton() {
    DOM.playBtn.textContent = state.isPlaying ? '⏸' : '▶';
}

// ========== SETTINGS & PERSISTENCE ==========

async function loadSettings() {
    try {
        const res = await fetch(`${API_BASE}/stream/settings`);
        if (!res.ok) throw new Error('Failed to load settings');

        state.settings = await res.json();

        if (state.settings.progress_mode === 'book' || state.settings.progress_mode === 'chapter') {
            state.progressMode = state.settings.progress_mode;
        }
        if (state.settings.time_mode === 'total' || state.settings.time_mode === 'remaining') {
            state.timeMode = state.settings.time_mode;
        }
        state.showImages = state.settings.show_images === true;
        if (state.settings.save_stream_audio !== undefined) {
            const toggle = document.getElementById('saveStreamAudioToggle');
            if (toggle) toggle.checked = !!state.settings.save_stream_audio;
        }

        applyVisibilitySettings();
    } catch (error) {
        logError('Settings load error', error);
        state.settings = {
            font_size: 16, font_family: 'system', preferred_model: null, preferred_voice: null,
            progress_mode: 'book', time_mode: 'total', show_title: true,
            show_progress_bar: true, show_images: false, sleep_timer_minutes: 0, show_sleep_timer: false
        };
        state.showImages = false;
    }
}

async function loadModels() {
    try {
        const res = await fetch(`${API_BASE}/openai/models`);
        if (!res.ok) throw new Error('Failed to load models');

        state.models = await res.json();

        if (!state.settings.preferred_model && Object.keys(state.models).length > 0) {
            const firstModel = Object.keys(state.models)[0];
            state.settings.preferred_model = firstModel;
            state.settings.preferred_voice = state.models[firstModel].voices[0];
        }
    } catch (error) {
        logError('Models load error', error);
        alert('Failed to load models. Please configure models first.');
    }
}

async function loadProgress() {
    try {
        const res = await fetch(`${API_BASE}/stream/progress?ebook_path=${encodeURIComponent(EBOOK_PATH)}`);
        if (!res.ok) throw new Error('Failed to load progress');
        state.progress = await res.json();

        if (!state.progress.bookmark_indices) {
            if (Array.isArray(state.progress.bookmarks)) {
                state.progress.bookmark_indices = state.progress.bookmarks;
            } else {
                state.progress.bookmark_indices = Object.keys(state.progress.bookmarks || {})
                    .map(k => parseInt(k)).sort((a, b) => a - b);
            }
        }
    } catch (error) {
        logError('Progress load error', error);
        state.progress = { ebook_path: EBOOK_PATH, current_chunk: 0, bookmarks: {}, bookmark_indices: [] };
    }
}

async function saveProgress() {
    try {
        await fetch(`${API_BASE}/stream/progress`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ebook_path: EBOOK_PATH, chunk_index: state.currentChunk })
        });
    } catch (error) {
        logError('Progress save error', error);
    }
}

/** Save current settings to server */
async function saveSettingsToServer() {
    try {
        await fetch(`${API_BASE}/stream/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                preferred_model: state.settings.preferred_model,
                preferred_voice: state.settings.preferred_voice,
                font_size: state.settings.font_size,
                font_family: state.settings.font_family,
                progress_mode: state.progressMode,
                time_mode: state.timeMode,
                show_title: state.settings.show_title,
                show_progress_bar: state.settings.show_progress_bar,
                show_images: state.showImages,
                ...(state.settings.save_stream_audio !== undefined && { save_stream_audio: state.settings.save_stream_audio }),
                sleep_timer_minutes: state.settings.sleep_timer_minutes,
                show_sleep_timer: state.sleepTimer.showTimer
            })
        });
    } catch (error) {
        logError('Error saving settings', error);
    }
}

/** Update voice dropdown based on selected model */
function updateVoiceOptions() {
    const modelSelect = document.getElementById('modelSelect');
    const voiceSelect = document.getElementById('voiceSelect');
    const modelData = state.models[modelSelect.value];
    if (!modelData) return;

    voiceSelect.innerHTML = '';
    modelData.voices.forEach(voice => {
        const option = document.createElement('option');
        option.value = voice;
        option.textContent = voice;
        if (voice === state.settings.preferred_voice) option.selected = true;
        voiceSelect.appendChild(option);
    });
}

// ========== BOOKMARKS ==========

async function toggleBookmark(chunkIndex) {
    try {
        let textPreview = '';
        const chunkDiv = document.querySelector(`.chunk-container[data-chunk-index="${chunkIndex}"]`);
        if (chunkDiv?.dataset.loaded === 'true') {
            textPreview = (chunkDiv.textContent || '').substring(0, 150).trim();
        }

        const res = await fetch(`${API_BASE}/stream/bookmark`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ebook_path: EBOOK_PATH,
                chunk_index: chunkIndex,
                text_preview: textPreview
            })
        });

        if (!res.ok) throw new Error('Failed to toggle bookmark');

        const data = await res.json();
        state.progress.bookmarks = data.bookmarks;
        state.progress.bookmark_indices = data.bookmark_indices || [];
        updateBookmarkVisuals();
    } catch (error) {
        logError('Bookmark toggle error', error);
        alert('Failed to toggle bookmark: ' + error.message);
    }
}

function isBookmarked(chunkIndex) {
    if (!state.progress) return false;
    if (state.progress.bookmark_indices) {
        return state.progress.bookmark_indices.includes(chunkIndex);
    }
    if (Array.isArray(state.progress.bookmarks)) {
        return state.progress.bookmarks.includes(chunkIndex);
    }
    return String(chunkIndex) in (state.progress.bookmarks || {});
}

function updateBookmarkVisuals() {
    document.querySelectorAll('.chunk-container').forEach(chunk => {
        const chunkIndex = parseInt(chunk.dataset.chunkIndex);
        isBookmarked(chunkIndex)
            ? chunk.classList.add('bookmarked')
            : chunk.classList.remove('bookmarked');
    });
}

// ========== DISPLAY SETTINGS ==========

function applyDisplaySettings() {
    if (state.settings.font_size) {
        DOM.textDisplay.style.fontSize = `${state.settings.font_size}px`;
    }

    if (state.settings.font_family) {
        const fontMap = {
            'system': '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
            'serif': 'Georgia, "Times New Roman", serif',
            'sans': 'Arial, Helvetica, sans-serif',
            'mono': '"Courier New", Courier, monospace'
        };
        DOM.textDisplay.style.fontFamily = fontMap[state.settings.font_family] || fontMap['system'];
    }
}

function applyVisibilitySettings() {
    const showTitle = state.settings.show_title !== undefined ? state.settings.show_title : true;
    const showProgressBar = state.settings.show_progress_bar !== undefined ? state.settings.show_progress_bar : true;

    const playerHeader = document.getElementById('playerHeader');
    const progressBarContainer = document.getElementById('progressBarContainer');

    if (playerHeader) {
        showTitle ? playerHeader.classList.remove('hidden') : playerHeader.classList.add('hidden');
    }
    if (progressBarContainer) {
        showProgressBar ? progressBarContainer.classList.remove('hidden') : progressBarContainer.classList.add('hidden');
    }
}

// ========== SLEEP TIMER ==========

function initSleepTimer() {
    const sleepTimerMinutes = state.settings.sleep_timer_minutes || 0;
    const showSleepTimer = state.settings.show_sleep_timer || false;

    state.sleepTimer.minutes = sleepTimerMinutes;
    state.sleepTimer.enabled = sleepTimerMinutes > 0;
    state.sleepTimer.showTimer = showSleepTimer;
    state.sleepTimer.lastActivityTime = Date.now();

    const input = document.getElementById('sleepTimerInput');
    if (input) input.value = String(sleepTimerMinutes);

    const checkbox = document.getElementById('showSleepTimerToggle');
    if (checkbox) checkbox.checked = showSleepTimer;

    createSleepTimerDisplay();
    updateSleepTimerDisplay();

    if (state.sleepTimer.enabled) {
        setupSleepTimerListeners();
        startSleepTimer();
    }
}

function createSleepTimerDisplay() {
    let timerDisplay = document.getElementById('sleepTimerDisplay');
    if (!timerDisplay) {
        timerDisplay = document.createElement('div');
        timerDisplay.id = 'sleepTimerDisplay';
        timerDisplay.style.cssText = `
            position: fixed; top: 60px; right: 10px;
            background: rgba(0, 0, 0, 0.7); color: #fff;
            padding: 8px 12px; border-radius: 4px;
            font-size: 14px; z-index: 9999; display: none;
            font-family: monospace; min-width: 80px; text-align: center;
        `;
        document.body.appendChild(timerDisplay);
    }
    return timerDisplay;
}

function setupSleepTimerListeners() {
    if (state.sleepTimer.listenersSetup) return;

    let lastResetTime = 0;
    const RESET_THROTTLE = 500;

    const resetSleepTimer = () => {
        const now = Date.now();
        if (now - lastResetTime < RESET_THROTTLE) return;
        lastResetTime = now;

        state.sleepTimer.lastActivityTime = Date.now();
        if (state.sleepTimer.enabled) {
            clearTimeout(state.sleepTimer.timeoutId);
            startSleepTimer();
        }
    };

    const handleScrollReset = () => {
        if (!state.scroll.autoInProgress) resetSleepTimer();
    };

    state.sleepTimer.resetFunction = resetSleepTimer;
    state.sleepTimer.scrollResetFunction = handleScrollReset;

    document.addEventListener('click', resetSleepTimer, true);
    document.addEventListener('scroll', handleScrollReset, true);
    document.addEventListener('keydown', resetSleepTimer, true);
    document.addEventListener('touchstart', resetSleepTimer, true);
    document.addEventListener('touchend', resetSleepTimer, true);
    document.addEventListener('input', resetSleepTimer, true);
    document.addEventListener('change', resetSleepTimer, true);

    const textDisplay = DOM.textDisplay;
    if (textDisplay) textDisplay.addEventListener('scroll', handleScrollReset, true);

    state.sleepTimer.listenersSetup = true;
    log('Sleep timer listeners set up');
}

function startSleepTimer() {
    if (!state.sleepTimer.enabled || state.sleepTimer.minutes === 0) return;

    if (state.sleepTimer.timeoutId) clearTimeout(state.sleepTimer.timeoutId);
    if (state.sleepTimer.updateIntervalId) clearInterval(state.sleepTimer.updateIntervalId);

    const timeoutMs = state.sleepTimer.minutes * 60 * 1000;
    log(`Sleep timer started: ${state.sleepTimer.minutes} minutes`);

    state.sleepTimer.timeoutId = setTimeout(() => {
        if (state.isPlaying) {
            log('Sleep timer triggered - pausing playback');
            stopPlaying();
            showToast(`Sleep timer triggered - paused after ${state.sleepTimer.minutes} minutes of inactivity`);
        }
        if (state.sleepTimer.updateIntervalId) clearInterval(state.sleepTimer.updateIntervalId);
        updateSleepTimerDisplay();
    }, timeoutMs);

    if (state.sleepTimer.showTimer) {
        state.sleepTimer.updateIntervalId = setInterval(() => {
            updateSleepTimerDisplay();
        }, 1000);
    }

    updateSleepTimerDisplay();
}

function updateSleepTimerDisplay() {
    const statusLabel = document.getElementById('sleepTimerStatusLabel');
    const timerDisplay = document.getElementById('sleepTimerDisplay');

    if (statusLabel) {
        if (!state.sleepTimer.enabled || state.sleepTimer.minutes === 0) {
            statusLabel.textContent = 'Disabled';
            statusLabel.style.color = 'var(--text-secondary)';
        } else {
            statusLabel.textContent = `Active (${state.sleepTimer.minutes}m)`;
            statusLabel.style.color = '#4CAF50';
        }
    }

    if (timerDisplay) {
        if (state.sleepTimer.showTimer && state.sleepTimer.enabled && state.sleepTimer.minutes > 0) {
            const elapsedMs = Date.now() - state.sleepTimer.lastActivityTime;
            const totalMs = state.sleepTimer.minutes * 60 * 1000;
            const remainingMs = Math.max(0, totalMs - elapsedMs);
            const remainingMinutes = Math.floor(remainingMs / 60000);
            const remainingSeconds = Math.floor((remainingMs % 60000) / 1000);

            timerDisplay.textContent = `${pad(remainingMinutes)}:${pad(remainingSeconds)}`;
            timerDisplay.style.display = 'block';
        } else {
            timerDisplay.style.display = 'none';
        }
    }
}

function cleanupSleepTimer() {
    if (state.sleepTimer.timeoutId) {
        clearTimeout(state.sleepTimer.timeoutId);
        state.sleepTimer.timeoutId = null;
    }
    if (state.sleepTimer.updateIntervalId) {
        clearInterval(state.sleepTimer.updateIntervalId);
        state.sleepTimer.updateIntervalId = null;
    }

    const timerDisplay = document.getElementById('sleepTimerDisplay');
    if (timerDisplay) timerDisplay.style.display = 'none';

    state.sleepTimer.listenersSetup = false;
}

// ========== TOUCH HANDLING ==========

function handleTouchStart(e) {
    const touch = e.touches[0];
    state.touch.startX = touch.clientX;
    state.touch.startY = touch.clientY;
    state.touch.startTime = Date.now();
}

function handleTouchMove(e) {
    const touch = e.touches[0];
    const deltaX = touch.clientX - state.touch.startX;
    const deltaY = Math.abs(touch.clientY - state.touch.startY);
    if (Math.abs(deltaX) > deltaY * 2 && Math.abs(deltaX) > 30) {
        e.preventDefault();
    }
}

function handleTouchEnd(e) {
    const deltaX = e.changedTouches[0].clientX - state.touch.startX;
    const deltaY = Math.abs(e.changedTouches[0].clientY - state.touch.startY);
    const deltaTime = Date.now() - state.touch.startTime;

    if (deltaX < -30 && deltaY < 50 && deltaTime < 500) {
        const chunkIndex = parseInt(e.currentTarget.dataset.chunkIndex);
        const wasBookmarked = isBookmarked(chunkIndex);
        navigator.vibrate?.(50);
        toggleBookmark(chunkIndex);
        e.preventDefault();
        showToast(wasBookmarked ? 'Bookmark removed' : 'Bookmark added');
    }
}

// ========== MODAL OPENERS ==========

function showChapters() {
    const list = DOM.modalList('chapter');
    if (!state.book || !state.book.chapters) {
        list.innerHTML = '<div class="loading">No chapters available</div>';
    } else {
        let html = '';
        state.book.chapters.forEach((ch, i) => {
            const isActive = state.currentChunk >= ch.start_chunk && state.currentChunk <= ch.end_chunk;
            html += `<div class="chapter-item${isActive ? ' active' : ''}" onclick="jumpToChapter(${i})">
                <div class="chapter-name">${ch.name || 'Chapter ' + (i + 1)}</div>
                <div class="chapter-info">Chunks ${ch.start_chunk + 1}–${ch.end_chunk + 1}</div>
            </div>`;
        });
        list.innerHTML = html;
    }
    showModal('chapters');
}

function jumpToChapter(index) {
    const ch = state.book.chapters[index];
    if (ch) {
        jumpToChunk(ch.start_chunk);
        closeModal('chapters');
    }
}

function showBookmarks() {
    const list = DOM.modalList('bookmark');
    if (!state.progress || !state.progress.bookmark_indices || state.progress.bookmark_indices.length === 0) {
        list.innerHTML = '<div class="loading">No bookmarks yet</div>';
    } else {
        let html = '';
        state.progress.bookmark_indices.forEach(idx => {
            // Prefer the text preview stored with the bookmark, fall back to chunk text
            const bookmarkDict = state.progress?.bookmarks;
            let text = '';
            if (bookmarkDict && typeof bookmarkDict === 'object' && !Array.isArray(bookmarkDict)) {
                text = bookmarkDict[String(idx)] || '';
            }
            if (!text) {
                const chunk = state.book?.chunks?.[idx];
                text = chunk?.text || '';
            }
            text = text.trim().substring(0, 120) || `Chunk #${idx + 1}`;
            html += `<div class="chapter-item" onclick="jumpToChunk(${idx}); closeBookmarksModal();">
                <div class="chapter-name">Bookmark #${idx + 1}</div>
                <div class="chapter-info">${text}...</div>
            </div>`;
        });
        list.innerHTML = html;
    }
    showModal('bookmarks');
}

function showSettings() {
    showModal('settings');

    // Refresh cache status in background so it's ready when user scrolls down
    refreshCacheStatus().catch(() => {});

    const modelSelect = document.getElementById('modelSelect');
    const voiceSelect = document.getElementById('voiceSelect');
    const fontSizeSelect = document.getElementById('fontSizeSelect');
    const fontFamilySelect = document.getElementById('fontFamilySelect');
    const progressModeSelect = document.getElementById('progressModeSelect');
    const timeModeSelect = document.getElementById('timeModeSelect');
    const showTitleToggle = document.getElementById('showTitleToggle');
    const showProgressBarToggle = document.getElementById('showProgressBarToggle');
    const showImagesToggle = document.getElementById('showImagesToggle');
    const showSleepTimerToggle = document.getElementById('showSleepTimerToggle');

    if (modelSelect) {
        modelSelect.innerHTML = '';
        Object.keys(state.models).forEach(m => {
            const opt = document.createElement('option');
            opt.value = m;
            opt.textContent = m;
            if (m === state.settings.preferred_model) opt.selected = true;
            modelSelect.appendChild(opt);
        });
    }
    if (voiceSelect) updateVoiceOptions();
    if (fontSizeSelect) fontSizeSelect.value = String(state.settings.font_size || 16);
    if (fontFamilySelect) fontFamilySelect.value = state.settings.font_family || 'system';
    if (progressModeSelect) progressModeSelect.value = state.progressMode;
    if (timeModeSelect) timeModeSelect.value = state.timeMode;
    if (showTitleToggle) showTitleToggle.checked = state.settings.show_title !== false;
    if (showProgressBarToggle) showProgressBarToggle.checked = state.settings.show_progress_bar !== false;
    if (showImagesToggle) showImagesToggle.checked = state.showImages;
    if (showSleepTimerToggle) showSleepTimerToggle.checked = state.sleepTimer.showTimer;
    const saveStreamAudioToggle = document.getElementById('saveStreamAudioToggle');
    if (saveStreamAudioToggle) saveStreamAudioToggle.checked = !!state.settings.save_stream_audio;
}

function saveSettings(e) {
    e.preventDefault();

    const modelSelect = document.getElementById('modelSelect');
    const voiceSelect = document.getElementById('voiceSelect');
    const fontSizeSelect = document.getElementById('fontSizeSelect');
    const fontFamilySelect = document.getElementById('fontFamilySelect');
    const progressModeSelect = document.getElementById('progressModeSelect');
    const timeModeSelect = document.getElementById('timeModeSelect');
    const showTitleToggle = document.getElementById('showTitleToggle');
    const showProgressBarToggle = document.getElementById('showProgressBarToggle');
    const showImagesToggle = document.getElementById('showImagesToggle');
    const showSleepTimerToggle = document.getElementById('showSleepTimerToggle');

    state.settings.preferred_model = modelSelect?.value;
    state.settings.preferred_voice = voiceSelect?.value;
    state.settings.font_size = parseInt(fontSizeSelect?.value) || 16;
    state.settings.font_family = fontFamilySelect?.value || 'system';
    state.settings.show_title = showTitleToggle?.checked !== false;
    state.settings.show_progress_bar = showProgressBarToggle?.checked !== false;
    state.settings.show_images = showImagesToggle?.checked;
    const saveStreamAudioToggle2 = document.getElementById('saveStreamAudioToggle');
    state.settings.save_stream_audio = !!saveStreamAudioToggle2?.checked;

    state.progressMode = progressModeSelect?.value || 'book';
    state.timeMode = timeModeSelect?.value || 'total';
    state.showImages = state.settings.show_images;
    state.sleepTimer.showTimer = showSleepTimerToggle?.checked || false;

    applyDisplaySettings();
    applyVisibilitySettings();
    updateProgress();
    saveSettingsToServer();
    closeModal('settings');
    showToast('Settings saved');
}

// ========== CONTROLS (UI-related) ==========

function toggleProgressMode() {
    const select = document.getElementById('progressModeSelect');
    if (select) {
        state.progressMode = select.value;
        saveSettingsToServer();
        updateProgress();
    }
}

function toggleTimeMode() {
    const select = document.getElementById('timeModeSelect');
    if (select) {
        state.timeMode = select.value;
        saveSettingsToServer();
        updateProgress();
    }
}

function toggleTitle() {
    const toggle = document.getElementById('showTitleToggle');
    state.settings.show_title = toggle?.checked !== false;
    applyVisibilitySettings();
    saveSettingsToServer();
}

function toggleSaveStreamAudio() {
    const toggle = document.getElementById('saveStreamAudioToggle');
    state.settings.save_stream_audio = !!toggle?.checked;
    if (state.settings.save_stream_audio) {
        showToast('Cache audio saving enabled — future chunks will be saved to disk', 3000);
    } else {
        showToast('Cache audio saving disabled', 2500);
    }
    saveSettingsToServer();
}

function toggleProgressBar() {
    const toggle = document.getElementById('showProgressBarToggle');
    state.settings.show_progress_bar = toggle?.checked !== false;
    applyVisibilitySettings();
    saveSettingsToServer();
}

function updateSleepTimer() {
    const input = document.getElementById('sleepTimerInput');
    const showToggle = document.getElementById('showSleepTimerToggle');
    const minutes = parseInt(input?.value) || 0;

    state.sleepTimer.minutes = minutes;
    state.sleepTimer.showTimer = showToggle?.checked || false;
    state.settings.sleep_timer_minutes = minutes;
    state.settings.show_sleep_timer = state.sleepTimer.showTimer;

    if (minutes > 0) {
        state.sleepTimer.enabled = true;
        if (!state.sleepTimer.listenersSetup) setupSleepTimerListeners();
        startSleepTimer();
    } else {
        state.sleepTimer.enabled = false;
        if (state.sleepTimer.timeoutId) { clearTimeout(state.sleepTimer.timeoutId); state.sleepTimer.timeoutId = null; }
        if (state.sleepTimer.updateIntervalId) { clearInterval(state.sleepTimer.updateIntervalId); state.sleepTimer.updateIntervalId = null; }
        const timerDisplay = document.getElementById('sleepTimerDisplay');
        if (timerDisplay) timerDisplay.style.display = 'none';
    }

    updateSleepTimerDisplay();
    saveSettingsToServer();
}

// ========== PROGRESS BAR ==========

function onProgressBarDrag(value) {
    updateProgressVisual(parseFloat(value));
}

function onProgressBarRelease(value) {
    seekToPosition(parseFloat(value));
}

// ========== MODAL CLOSE HELPERS ==========

function closeChaptersModal() { closeModal('chapters'); }
function closeSettingsModal() { closeModal('settings'); }
function closeBookmarksModal() { closeModal('bookmarks'); }

// ========== NAVIGATION ==========

function goBack() {
    stopPlaying();
    // Navigate back to the ebook's parent directory
    const parentDir = EBOOK_PATH.split('/').slice(0, -1).join('/');
    shutdownAndNavigate('/?path=' + encodeURIComponent(parentDir));
}

async function shutdownAndNavigate(targetHref) {
    // Save current position before navigating away
    try { await saveProgress(); } catch(e) {}
    
    hideLoading();
    hideAudioStatus();
    cleanupSleepTimer();

    state.isJumping = false;
    state.isGeneratingAudio = false;
    state.loadingChunks.clear();
    state.isUserStopping = true;

    if (state.chunkObserver) {
        state.chunkObserver.disconnect();
        state.chunkObserver = null;
    }

    state.inFlightControllers.forEach(c => { try { c.abort(); } catch (e) { } });
    state.inFlightControllers.clear();

    if (state.currentAudioBlobUrl) {
        try { URL.revokeObjectURL(state.currentAudioBlobUrl); } catch (e) { }
        state.currentAudioBlobUrl = null;
    }
    state.audioCache.clear();

    setTimeout(() => window.location.href = targetHref, 50);
}
