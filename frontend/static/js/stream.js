// Streaming mode JavaScript
const API_BASE = '/api';

// ========== STATE MANAGEMENT ==========
const state = {
    book: null,
    currentChunk: 0,
    isPlaying: false,
    currentAudioSegment: null,
    settings: {},
    models: {},
    chunkSize: 4096,
    autoScrollEnabled: true,
    progress: null,
    audioCache: new Map(),
    isGeneratingAudio: false,
    pendingJump: null,
    audioPlaybackId: 0,
    hasShownErrorAlert: false,
    isTransitioning: false,
    isJumping: false,
    isUserStopping: false,
    audioCachePreloadInProgress: false,
    currentAudioBlobUrl: null,
    prefetchInFlight: new Set(),
    inFlightControllers: new Set(),
    progressMode: 'book',
    timeMode: 'total',
    showImages: false,
    imageCache: new Map(),
    touch: { startX: 0, startY: 0, startTime: 0 },
    scroll: {
        timeout: null,
        autoInProgress: false,
        isLoadingChunks: false,
        previousChunk: -1,
        lastManual: 0
    },
    sleepTimer: {
        enabled: false,
        minutes: 0,
        timeoutId: null,
        lastActivityTime: Date.now(),
        showTimer: false,
        timeRemaining: 0,
        updateIntervalId: null,
        listenersSetup: false,
        resetFunction: null
    }
};

// Constants
const CACHE = { SIZE: 10, CONCURRENCY: 3, MAX_SIZE: 30 };
const CHARS = { PER_MINUTE: 1000, PER_SECOND: 1000 / 60 };
const LOAD = { INITIAL: 100, RADIUS: 75, BATCH: 20, CLEANUP_MULT: 3 };
const SCROLL = { THRESHOLD_MULT: 3, DEBOUNCE: 30, SCROLL_DELAY: 600, MANUAL_TIMEOUT: 2000 };

// ========== DOM HELPERS ==========
const DOM = {
    get audio() { return document.getElementById('audioPlayer'); },
    get playBtn() { return document.getElementById('playButton'); },
    get textDisplay() { return document.getElementById('textDisplay'); },
    get progressBar() { return document.getElementById('progressBar'); },
    get speedControl() { return document.getElementById('speedControl'); },
    get bookTitle() { return document.getElementById('bookTitle'); },
    get totalProgress() { return document.getElementById('totalProgress'); },
    get currentPosition() { return document.getElementById('currentPosition'); },
    get timeEstimate() { return document.getElementById('timeEstimate'); },
    get loadingOverlay() { return document.getElementById('loadingOverlay'); },
    modal: (type) => document.getElementById(`${type}Modal`),
    modalList: (type) => document.getElementById(`${type}List`)
};

// ========== UTILITY FUNCTIONS ==========
const log = (msg, data = '') => console.log(`[STREAM] ${msg}`, data);
const logCache = (msg, data = '') => console.log(`[STREAM CACHE] ${msg}`, data);
const logError = (msg, err) => console.error(`[STREAM] ${msg}:`, err);

const formatTime = (seconds) => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
};

const pad = (n) => String(n).padStart(2, '0');
const formatNumber = (n) => n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
const showLoading = (msg = 'Loading...') => {
    DOM.loadingOverlay.querySelector('div div:last-child').textContent = msg;
    DOM.loadingOverlay.classList.remove('hidden');
};
const hideLoading = () => DOM.loadingOverlay.classList.add('hidden');

// Non-blocking audio status indicator
const showAudioStatus = (msg = 'Generating audio...') => {
    const statusEl = document.getElementById('audioStatus');
    const textEl = document.getElementById('audioStatusText');
    if (statusEl && textEl) {
        textEl.textContent = msg;
        statusEl.classList.add('visible');
    }
};
const hideAudioStatus = () => {
    const statusEl = document.getElementById('audioStatus');
    if (statusEl) statusEl.classList.remove('visible');
};

// Skip audio generation and stop playback
function skipAudioGeneration() {
    console.log('[STREAM] User skipped audio generation');
    // Set flag to ignore error events during user-initiated stop
    state.isUserStopping = true;
    // Abort all in-flight requests
    state.inFlightControllers.forEach(c => { try { c.abort(); } catch (e) {} });
    state.inFlightControllers.clear();
    // Stop playback
    stopPlaying();
    hideAudioStatus();
    showToast('Audio generation cancelled');
    // Reset flag after a short delay to allow error events to be ignored
    setTimeout(() => { state.isUserStopping = false; }, 500);
}

const showToast = (msg) => {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        toast.style.cssText = `position:fixed;bottom:80px;left:50%;transform:translateX(-50%);
            background:rgba(255,255,255,0.9);color:#000;padding:10px 20px;border-radius:20px;
            z-index:10000;opacity:0;transition:opacity 0.3s;`;
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.style.opacity = '1';
    setTimeout(() => toast.style.opacity = '0', 2000);
};

// ========== MODAL MANAGEMENT ==========
const showModal = (type, populateFn) => {
    const modal = DOM.modal(type);
    if (populateFn) populateFn();
    modal.style.display = 'flex';
};

const closeModal = (type) => DOM.modal(type).style.display = 'none';

window.onclick = (e) => {
    if (e.target.classList.contains('modal')) e.target.style.display = 'none';
};

// ========== INITIALIZATION ==========
document.addEventListener('DOMContentLoaded', async () => {
    log('Initializing with ebook:', EBOOK_PATH);
    trackFilePlayback(EBOOK_PATH).catch(err => console.warn('[TRACKING] Skipped:', err.message));

    await Promise.all([loadSettings(), loadModels(), loadProgress()]);
    await parseBook();

    if (state.progress?.current_chunk > 0) {
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
});

window.addEventListener('beforeunload', () => {
    if (state.audioWatchdogInterval) clearInterval(state.audioWatchdogInterval);
    if (state.currentAudioBlobUrl) URL.revokeObjectURL(state.currentAudioBlobUrl);
    state.audioCache.clear();
    state.inFlightControllers.forEach(c => { try { c.abort(); } catch (e) { } });
    state.inFlightControllers.clear();
});

// ========== PLAYBACK TRACKING ==========
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

// ========== BOOK PARSING & CHUNKS ==========
async function parseBook() {
    try {
        showLoading('Loading book...');
        const withImages = state.showImages ? '&with_images=true' : '';
        const res = await fetch(`${API_BASE}/stream/parse?ebook_path=${encodeURIComponent(EBOOK_PATH)}&chunk_size=${state.chunkSize}${withImages}`);
        if (!res.ok) throw new Error('Failed to parse book');

        state.book = await res.json();
        log('Book parsed:', state.book);

        DOM.bookTitle.textContent = state.book.title;
        await loadAllChunks();
        DOM.playBtn.disabled = false;
        hideLoading();
    } catch (error) {
        logError('Parse error', error);
        alert('Failed to load book: ' + error.message);
        hideLoading();
    }
}

async function loadAllChunks() {
    console.log('[STREAM] Loading text chunks on-demand...');

    DOM.textDisplay.innerHTML = '';

    for (let i = 0; i < Math.min(LOAD.INITIAL, state.book.chunks.length); i++) {
        DOM.textDisplay.appendChild(createChunkElement(i));
    }

    await loadChunksAround(state.currentChunk, LOAD.RADIUS);
    DOM.textDisplay.addEventListener('scroll', handleScroll);

    console.log('[STREAM] Initial chunks created');
    updateProgress();
}

function createChunkElement(chunkIndex) {
    const div = document.createElement('div');
    div.className = 'chunk-container';
    div.dataset.chunkIndex = chunkIndex;
    div.dataset.loaded = 'false';
    div.textContent = 'Loading...';
    div.style.minHeight = '200px';
    div.onclick = () => loadAndJumpToChunk(chunkIndex);

    if (isBookmarked(chunkIndex)) {
        div.classList.add('bookmarked');
    }

    // Add touch event handlers
    div.addEventListener('touchstart', handleTouchStart);
    div.addEventListener('touchmove', handleTouchMove);
    div.addEventListener('touchend', handleTouchEnd);

    // Disable text selection
    div.style.userSelect = 'none';
    div.style.webkitUserSelect = 'none';
    return div;
}

function handleScroll() {
    if (state.scroll.autoInProgress) return;
    state.scroll.lastManual = Date.now();

    if (state.scroll.timeout) clearTimeout(state.scroll.timeout);
    state.scroll.timeout = setTimeout(() => {
        if (state.scroll.isLoadingChunks) {
            console.log('[STREAM] Already loading chunks, skipping');
            return;
        }

        const scrollTop = DOM.textDisplay.scrollTop;
        const scrollHeight = DOM.textDisplay.scrollHeight;
        const clientHeight = DOM.textDisplay.clientHeight;
        const distanceFromTop = scrollTop;
        const distanceFromBottom = scrollHeight - scrollTop - clientHeight;

        const loadedChunks = Array.from(DOM.textDisplay.querySelectorAll('.chunk-container'))
            .map(el => parseInt(el.dataset.chunkIndex))
            .sort((a, b) => a - b);

        if (loadedChunks.length === 0) return;

        const [firstLoaded, lastLoaded] = [loadedChunks[0], loadedChunks[loadedChunks.length - 1]];
        const threshold = clientHeight * SCROLL.THRESHOLD_MULT;

        let targetChunk = state.currentChunk;
        let shouldLoad = false;

        if (distanceFromTop < threshold && firstLoaded > 0) {
            console.log(`[STREAM] Near top (${distanceFromTop}px from top), loading earlier chunks`);
            targetChunk = Math.max(0, firstLoaded - 30);
            shouldLoad = true;
        } else if (distanceFromBottom < threshold && lastLoaded < state.book.total_chunks - 1) {
            console.log(`[STREAM] Near bottom (${distanceFromBottom}px from bottom), loading later chunks`);
            targetChunk = Math.min(state.book.total_chunks - 1, lastLoaded + 30);
            shouldLoad = true;
        }

        if (shouldLoad) {
            // Load more chunks around the target without blocking scroll
            loadChunksAroundAsync(targetChunk, LOAD.RADIUS);
        }
    }, SCROLL.DEBOUNCE);
}

async function loadChunksAroundAsync(centerChunk, radius = 50) {
    if (state.scroll.isLoadingChunks) return;
    state.scroll.isLoadingChunks = true;
    
    // Safety timeout - reset flag after 15 seconds in case something hangs
    const safetyTimeout = setTimeout(() => {
        if (state.scroll.isLoadingChunks) {
            console.warn('[STREAM] isLoadingChunks flag stuck, resetting');
            state.scroll.isLoadingChunks = false;
        }
    }, 15000);
    
    try {
        await loadChunksAround(centerChunk, radius);
    } finally {
        clearTimeout(safetyTimeout);
        state.scroll.isLoadingChunks = false;
    }
}

async function loadChunksAround(centerChunk, radius = 25) {
    const startChunk = Math.max(0, centerChunk - radius);
    const endChunk = Math.min(state.book.chunks.length - 1, centerChunk + radius);

    console.log(`[STREAM] Loading chunks ${startChunk} to ${endChunk} around chunk ${centerChunk}`);

    const textDisplay = DOM.textDisplay;
    let scrollAnchorIndex = null;
    let scrollAnchorOffset = 0;

    const visibleChunks = Array.from(textDisplay.querySelectorAll('.chunk-container'))
        .filter(el => {
            const rect = el.getBoundingClientRect();
            const containerRect = textDisplay.getBoundingClientRect();
            return rect.top < containerRect.bottom && rect.bottom > containerRect.top;
        });

    if (visibleChunks.length > 0) {
        const topVisible = visibleChunks[0];
        scrollAnchorIndex = parseInt(topVisible.dataset.chunkIndex);
        scrollAnchorOffset = topVisible.getBoundingClientRect().top - textDisplay.getBoundingClientRect().top;
        console.log(`[STREAM] Scroll anchor: chunk ${scrollAnchorIndex} at offset ${scrollAnchorOffset}px`);
    }

    const existingChunks = new Set();
    const existingElements = new Map();
    textDisplay.querySelectorAll('.chunk-container').forEach(chunk => {
        const idx = parseInt(chunk.dataset.chunkIndex);
        existingChunks.add(idx);
        existingElements.set(idx, chunk);
    });

    for (const [idx, element] of existingElements) {
        if (Math.abs(idx - centerChunk) > radius * LOAD.CLEANUP_MULT) {
            element.remove();
            console.log(`[STREAM] Removed far chunk ${idx}`);
        }
    }

    for (let i = startChunk; i <= endChunk; i++) {
        if (!existingChunks.has(i)) {
            // Find correct position to insert
            const allChunks = Array.from(textDisplay.querySelectorAll('.chunk-container'));
            let insertBefore = null;

            for (const chunk of allChunks) {
                const chunkIdx = parseInt(chunk.dataset.chunkIndex);
                if (chunkIdx > i) {
                    insertBefore = chunk;
                    break;
                }
            }

            const newChunk = createChunkElement(i);
            if (insertBefore) {
                textDisplay.insertBefore(newChunk, insertBefore);
            } else {
                textDisplay.appendChild(newChunk);
            }
        }
    }

    if (scrollAnchorIndex !== null) {
        const anchorElement = document.querySelector(`.chunk-container[data-chunk-index="${scrollAnchorIndex}"]`);
        if (anchorElement) {
            const currentOffset = anchorElement.getBoundingClientRect().top - textDisplay.getBoundingClientRect().top;
            const scrollAdjustment = currentOffset - scrollAnchorOffset;
            if (Math.abs(scrollAdjustment) > 1) { // Only adjust if difference is significant
                textDisplay.scrollTop += scrollAdjustment;
                console.log(`[STREAM] Adjusted scroll by ${scrollAdjustment}px to maintain position`);
            }
        }
    }

    const chunksToLoad = [];
    for (let i = startChunk; i <= endChunk; i++) {
        const chunkDiv = textDisplay.querySelector(`.chunk-container[data-chunk-index="${i}"]`);
        if (chunkDiv?.dataset.loaded === 'false') chunksToLoad.push(i);
    }

    for (let i = 0; i < chunksToLoad.length; i += LOAD.BATCH) {
        const batch = chunksToLoad.slice(i, i + LOAD.BATCH);
        await Promise.all(batch.map(idx => loadSingleChunk(idx)));

        // No delay between batches - load as fast as possible to prevent wall
    }
}

async function loadSingleChunk(chunkIndex) {
    try {
        const chunkDiv = document.querySelector(`.chunk-container[data-chunk-index="${chunkIndex}"]`);
        if (!chunkDiv || chunkDiv.dataset.loaded === 'true') return;

        const withImages = state.showImages ? '&with_images=true' : '';
        const res = await fetch(`${API_BASE}/stream/text?ebook_path=${encodeURIComponent(EBOOK_PATH)}&chunk_index=${chunkIndex}${withImages}`);
        if (!res.ok) throw new Error('Failed to load chunk text');

        const data = await res.json();
        
        // Clear and rebuild chunk content
        chunkDiv.innerHTML = '';
        
        // Handle inline images if available and enabled
        if (state.showImages && data.image_data && data.image_data.length > 0 && data.display_text) {
            // Use display_text which contains the markers
            let text = data.display_text;
            
            // Load all images first
            const imageElements = new Map();
            for (const imgData of data.image_data) {
                const img = await loadImage(imgData.id);
                if (img) {
                    imageElements.set(imgData.marker, img);
                }
            }
            
            // Split text by markers and build content with inline images
            let lastPos = 0;
            const fragment = document.createDocumentFragment();
            
            // Find all marker positions in the display_text
            const markerPositions = data.image_data
                .map(imgData => ({
                    pos: text.indexOf(imgData.marker),
                    marker: imgData.marker,
                    length: imgData.marker.length
                }))
                .filter(m => m.pos >= 0)
                .sort((a, b) => a.pos - b.pos);
            
            for (const markerInfo of markerPositions) {
                // Add text before marker
                if (markerInfo.pos > lastPos) {
                    const textBefore = text.substring(lastPos, markerInfo.pos);
                    fragment.appendChild(document.createTextNode(textBefore));
                }
                
                // Add image
                const img = imageElements.get(markerInfo.marker);
                if (img) {
                    const imageWrapper = document.createElement('div');
                    imageWrapper.className = 'inline-image-wrapper';
                    imageWrapper.appendChild(img.cloneNode(true));
                    fragment.appendChild(imageWrapper);
                }
                
                lastPos = markerInfo.pos + markerInfo.length;
            }
            
            // Add remaining text
            if (lastPos < text.length) {
                fragment.appendChild(document.createTextNode(text.substring(lastPos)));
            }
            
            chunkDiv.appendChild(fragment);
        } else {
            // No images, just add text (use clean text)
            const textNode = document.createTextNode(data.text);
            chunkDiv.appendChild(textNode);
        }
        
        chunkDiv.dataset.loaded = 'true';
        chunkDiv.style.minHeight = '';

        if (isBookmarked(chunkIndex)) {
            chunkDiv.classList.add('bookmarked');
        }
    } catch (error) {
        logError('Chunk load error', error);
        const chunkDiv = document.querySelector(`.chunk-container[data-chunk-index="${chunkIndex}"]`);
        if (chunkDiv) {
            chunkDiv.textContent = `[Error loading chunk ${chunkIndex}]`;
            chunkDiv.style.minHeight = '';
        }
    }
}

async function loadImage(imageId) {
    // Check cache first
    if (state.imageCache.has(imageId)) {
        return createImageElement(imageId, state.imageCache.get(imageId));
    }
    
    try {
        const res = await fetch(`${API_BASE}/stream/image?ebook_path=${encodeURIComponent(EBOOK_PATH)}&image_id=${imageId}`);
        if (!res.ok) return null;
        
        const data = await res.json();
        if (data.data) {
            state.imageCache.set(imageId, data.data);
            return createImageElement(imageId, data.data);
        }
    } catch (error) {
        logError('Image load error', error);
    }
    return null;
}

function createImageElement(imageId, dataUrl) {
    const img = document.createElement('img');
    img.src = dataUrl;
    img.className = 'chunk-image';
    img.alt = 'Book image';
    img.dataset.imageId = imageId;
    
    // Click to view full size
    img.onclick = (e) => {
        e.stopPropagation();
        showImageModal(dataUrl);
    };
    
    return img;
}

function showImageModal(imageUrl) {
    const modal = document.createElement('div');
    modal.className = 'image-modal';
    modal.onclick = () => modal.remove();
    
    const img = document.createElement('img');
    img.src = imageUrl;
    
    modal.appendChild(img);
    document.body.appendChild(modal);
}

async function loadAndJumpToChunk(chunkIndex) {
    // Prevent multiple simultaneous jump operations
    if (state.isJumping) {
        console.log('[STREAM] Already jumping, ignoring click');
        return;
    }
    state.isJumping = true;
    
    // Safety timeout - reset flag after 10 seconds in case something hangs
    const safetyTimeout = setTimeout(() => {
        if (state.isJumping) {
            console.warn('[STREAM] isJumping flag stuck, resetting');
            state.isJumping = false;
        }
    }, 10000);
    
    try {
        await jumpToChunk(chunkIndex);
    } finally {
        clearTimeout(safetyTimeout);
        state.isJumping = false;
    }
}

// ========== AUDIO PLAYER SETUP ==========
function setupAudioPlayer() {
    const audio = DOM.audio;

    audio.addEventListener('timeupdate', handleChunkTransition);

    audio.addEventListener('error', async (e) => {
        // Ignore errors during user-initiated stops (e.g., skip button)
        if (state.isUserStopping) {
            console.log('[STREAM] Ignoring audio error during user-initiated stop');
            return;
        }
        
        logError('Audio element error', e);
        
        // Get more info about the error
        const errorCode = audio.error?.code;
        const errorMsg = audio.error?.message || 'Unknown error';
        log(`Audio error details - code: ${errorCode}, message: ${errorMsg}`);
        
        // If we're playing, try to skip to next chunk instead of stopping completely
        if (state.isPlaying && state.book?.lrc_data) {
            const totalChunks = state.book.audio_chunks?.length || 0;
            const nextChunk = state.currentChunk + 1;
            
            if (nextChunk < totalChunks) {
                log(`Skipping broken chunk ${state.currentChunk}, moving to chunk ${nextChunk}`);
                showToast(`Skipping unplayable audio segment...`);
                
                // Clear the broken audio
                audio.src = '';
                state.currentAudioSegment = null;
                
                // Jump to next chunk
                state.currentChunk = nextChunk;
                try {
                    await playCurrentChunk();
                } catch (err) {
                    logError('Failed to play next chunk after error', err);
                    stopPlaying();
                }
                return;
            }
        }
        
        // If we can't skip or not playing, show error
        if (!state.hasShownErrorAlert && state.isPlaying) {
            state.hasShownErrorAlert = true;
            stopPlaying();
            alert('Audio playback error. Please try again.');
            setTimeout(() => state.hasShownErrorAlert = false, 3000);
        } else {
            stopPlaying();
        }
    });

    audio.addEventListener('pause', () => {
        // Don't update state.isPlaying if we're generating audio
        // (pause events during chunk transitions shouldn't stop playback)
        if (state.isPlaying && !state.isGeneratingAudio) {
            state.isPlaying = false;
            updatePlayButton();
        }
    });

    audio.addEventListener('play', () => {
        if (!state.isPlaying) {
            state.isPlaying = true;
            updatePlayButton();
        }
    });

    audio.addEventListener('loadeddata', async () => {
        if (state.isPlaying && audio.paused) {
            try {
                await audio.play();
            } catch (err) {
                const errorMsg = err.message || err.toString() || JSON.stringify(err);
                if (errorMsg.includes('abort') || errorMsg.includes('NotAllowed')) {
                    // Watchdog will retry
                } else {
                    state.isPlaying = false;
                    updatePlayButton();
                }
            }
        }
    });
}

function startAudioWatchdog() {
    if (state.audioWatchdogInterval) clearInterval(state.audioWatchdogInterval);
    state.audioWatchdogInterval = setInterval(() => {
        const audio = DOM.audio;
        // Don't restart audio if:
        // - We're generating new audio (e.g., after a seek/jump)
        // - The loaded audio doesn't match the current chunk (prevents playing old chunk after jump)
        const audioMatchesCurrentChunk = state.currentAudioSegment?.chunkIndex === state.currentChunk;
        if (state.isPlaying && audio?.src && !state.isGeneratingAudio && audioMatchesCurrentChunk) {
            if (audio.paused && audio.readyState >= 2) {
                audio.play().catch(err => {
                    // Keep trying silently
                });
            }
        }
    }, 500);
}

async function handleChunkTransition() {
    const audio = DOM.audio;
    if (!state.isPlaying || !audio?.src || state.isTransitioning) return;

    const currentTime = audio.currentTime;
    const duration = audio.duration;

    if (duration && currentTime >= duration - 0.5) {
        state.isTransitioning = true;
        try {
            if (!state.isPlaying) return;
            if (state.currentAudioSegment?.playbackId !== state.audioPlaybackId) return;

            // Don't transition if the audio doesn't match the current chunk
            // (e.g., old audio finishing after a jump)
            if (state.currentAudioSegment?.chunkIndex !== state.currentChunk) {
                console.log('[STREAM] Ignoring transition - audio chunk mismatch');
                return;
            }

            if (state.currentChunk >= state.book.total_chunks - 1) {
                stopPlaying();
                alert('Finished reading the book!');
                return;
            }

            state.currentChunk++;
            saveProgress();
            await playNextSegment();
        } finally {
            state.isTransitioning = false;
        }
    }
}

// ========== PLAYBACK CONTROLS ==========
async function togglePlay() {
    state.isPlaying ? pausePlaying() : await startPlaying();
}

async function startPlaying() {
    state.isPlaying = true;
    updatePlayButton();
    await loadChunksAround(state.currentChunk, LOAD.RADIUS);
    await playNextSegment();
}

function pausePlaying() {
    state.isPlaying = false;
    state.isGeneratingAudio = false;
    updatePlayButton();
    DOM.audio.pause();
}

function stopPlaying() {
    state.isPlaying = false;
    state.isGeneratingAudio = false;
    state.audioPlaybackId++;
    updatePlayButton();

    const audio = DOM.audio;
    audio.pause();
    audio.src = '';

    if (state.currentAudioBlobUrl) {
        URL.revokeObjectURL(state.currentAudioBlobUrl);
        state.currentAudioBlobUrl = null;
    }

    if (state.currentAudioSegment?.url) {
        URL.revokeObjectURL(state.currentAudioSegment.url);
    }

    state.currentAudioSegment = null;
}

async function playNextSegment(shouldPlay = false) {
    // If called from jumpToChunk, shouldPlay indicates whether to resume playback
    if (shouldPlay) {
        state.isPlaying = true;
        updatePlayButton();
    }

    if (!state.isPlaying) return;

    // Prevent multiple concurrent calls to playNextSegment
    if (state.isGeneratingAudio) {
        console.log('[STREAM] Already generating audio, skipping duplicate call');
        return;
    }

    if (state.currentChunk >= state.book.total_chunks) {
        stopPlaying();
        alert('Finished reading the book!');
        return;
    }

    const thisPlaybackId = state.audioPlaybackId;

    try {
        state.isGeneratingAudio = true;
        const chunk = state.book.chunks[state.currentChunk];
        const cacheKey = `${chunk.start_idx}-${chunk.end_idx}`;

        // If we're switching chunks, pause the old audio to prevent it playing during generation
        const audio = DOM.audio;
        if (state.currentAudioSegment && state.currentAudioSegment.chunkIndex !== state.currentChunk) {
            audio.pause();
            audio.currentTime = 0;
        }

        if (!state.audioCache.has(cacheKey)) showAudioStatus('Generating audio...');

        const audioBlob = await generateAudio(chunk.start_idx, chunk.end_idx);
        hideAudioStatus();
        state.isGeneratingAudio = false;

        if (state.audioPlaybackId !== thisPlaybackId || !state.isPlaying) return;

        if (state.currentAudioBlobUrl) {
            URL.revokeObjectURL(state.currentAudioBlobUrl);
        }

        const audioUrl = URL.createObjectURL(audioBlob);
        state.currentAudioBlobUrl = audioUrl;
        state.currentAudioSegment = {
            chunkIndex: state.currentChunk,
            url: audioUrl,
            playbackId: thisPlaybackId
        };

        audio.src = audioUrl;
        audio.playbackRate = parseFloat(DOM.speedControl.value);

        try {
            await audio.play();
        } catch (err) {
            // Watchdog will retry
        }

        highlightCurrentChunk();
        updateProgress();

        // Now that current chunk is playing, prefetch the next chunks in the background
        setTimeout(() => prefetchAudio(state.currentChunk + 1, 3), 0);

    } catch (error) {
        hideAudioStatus();
        state.isGeneratingAudio = false;

        const isAbortError = error.message.includes('aborted') || error.message.includes('abort');
        if (!isAbortError && !state.hasShownErrorAlert && state.isPlaying) {
            state.hasShownErrorAlert = true;
            state.isPlaying = false;
            updatePlayButton();

            const errorMsg = error.message.includes('Connection refused') || error.message.includes('ECONNREFUSED')
                ? 'Cannot connect to TTS service. Please check if the service is running.'
                : error.message.includes('timeout') || error.message.includes('timed out')
                    ? 'Audio generation timed out. Please try again.'
                    : error.message.includes('Failed to fetch')
                        ? 'Network error. Please check your connection.'
                        : `Audio generation failed: ${error.message}`;

            alert(errorMsg);
            setTimeout(() => state.hasShownErrorAlert = false, 5000);
        } else if (!isAbortError) {
            state.isPlaying = false;
            updatePlayButton();
        }
    }
}

async function generateAudio(startChar, endChar, useCache = true) {
    const cacheKey = `${startChar}-${endChar}`;
    if (useCache && state.audioCache.has(cacheKey)) {
        logCache('Hit:', cacheKey);
        return state.audioCache.get(cacheKey);
    }

    const controller = new AbortController();
    state.inFlightControllers.add(controller);

    try {
        const headers = { 'Content-Type': 'application/json' };
        if (!useCache) headers['X-Background-Prefetch'] = '1';

        const res = await fetch(`${API_BASE}/stream/audio`, {
            method: 'POST',
            headers,
            signal: controller.signal,
            body: JSON.stringify({
                ebook_path: EBOOK_PATH,
                start_char: startChar,
                end_char: endChar,
                model: state.settings.preferred_model || 'tts-1',
                voice: state.settings.preferred_voice || 'alloy'
            })
        });

        if (!res.ok) throw new Error(`Audio generation failed: ${await res.text()}`);

        const audioBlob = await res.blob();
        state.audioCache.set(cacheKey, audioBlob);
        logCache(`Stored audio, cache size: ${state.audioCache.size}`);
        
        // Only cleanup when cache exceeds max size to prevent thrashing
        if (state.audioCache.size > CACHE.MAX_SIZE) {
            cleanupAudioCache(state.currentChunk);
        }

        return audioBlob;
    } catch (error) {
        const isAbort = error?.name === 'AbortError' || error.message?.toLowerCase().includes('aborted');
        if (!isAbort) logError('Audio generation error', error);
        throw error;
    } finally {
        state.inFlightControllers.delete(controller);
    }
}

function cleanupAudioCache(centerChunkIndex) {
    // Keep a generous window: 5 behind, 10 ahead of current position
    const minKeep = Math.max(0, centerChunkIndex - 5);
    const maxKeep = Math.min(state.book.total_chunks - 1, centerChunkIndex + 10);

    const chunkMap = new Map();
    state.book.chunks.forEach((chunk, idx) => chunkMap.set(chunk.start_idx, idx));

    for (const [key] of state.audioCache) {
        const startChar = parseInt(key.split('-')[0]);
        const chunkIndex = chunkMap.get(startChar);
        if (chunkIndex !== undefined && (chunkIndex < minKeep || chunkIndex > maxKeep)) {
            state.audioCache.delete(key);
            logCache(`Evicted chunk ${chunkIndex} (keeping ${minKeep}-${maxKeep})`);
        }
    }
}

function prefetchAudio(startChunkIndex, count = CACHE.SIZE) {
    if (!state.book) return;

    for (let i = 0; i < count; i++) {
        const chunkIndex = startChunkIndex + i;
        if (chunkIndex >= state.book.total_chunks) break;

        const chunk = state.book.chunks[chunkIndex];
        const cacheKey = `${chunk.start_idx}-${chunk.end_idx}`;

        if (state.audioCache.has(cacheKey) || state.prefetchInFlight.has(cacheKey)) continue;
        if (state.prefetchInFlight.size >= CACHE.CONCURRENCY) break;

        state.prefetchInFlight.add(cacheKey);
        generateAudio(chunk.start_idx, chunk.end_idx, false)
            .then(() => state.prefetchInFlight.delete(cacheKey))
            .catch(() => state.prefetchInFlight.delete(cacheKey));
    }

    for (let i = 1; i <= 2; i++) {
        const chunkIndex = state.currentChunk - i;
        if (chunkIndex < 0 || state.prefetchInFlight.size >= CACHE.CONCURRENCY) break;

        const chunk = state.book.chunks[chunkIndex];
        const cacheKey = `${chunk.start_idx}-${chunk.end_idx}`;

        if (!state.audioCache.has(cacheKey) && !state.prefetchInFlight.has(cacheKey)) {
            state.prefetchInFlight.add(cacheKey);
            generateAudio(chunk.start_idx, chunk.end_idx, false)
                .then(() => state.prefetchInFlight.delete(cacheKey))
                .catch(() => state.prefetchInFlight.delete(cacheKey));
        }
    }
}

// ========== NAVIGATION ==========
async function jumpToChunk(chunkIndex) {
    if (chunkIndex < 0 || chunkIndex >= state.book.total_chunks) return;

    console.log('[STREAM] Jumping to chunk:', chunkIndex);

    state.audioPlaybackId++;
    const thisPlaybackId = state.audioPlaybackId;
    const wasPlaying = state.isPlaying;

    // Fully stop playback to prevent watchdog from restarting old audio
    state.isPlaying = false;
    state.isGeneratingAudio = false;
    updatePlayButton();

    const audio = DOM.audio;
    audio.pause();
    audio.currentTime = 0;

    state.currentChunk = chunkIndex;

    // Check if chunk is already loaded in DOM - fast path
    const existingChunk = document.querySelector(`.chunk-container[data-chunk-index="${chunkIndex}"]`);
    const needsLoading = !existingChunk || existingChunk.dataset.loaded === 'false';
    
    if (needsLoading) {
        // Only load chunks if we need to
        await loadChunksAround(chunkIndex, LOAD.RADIUS);
    }
    
    highlightCurrentChunk();
    updateProgress();
    await scrollToCurrentChunk(); // await to ensure scrolling completes before continuing
    saveProgress(); // Don't await - let it run in background

    // Only restart playback inside playNextSegment to avoid watchdog restarting old audio
    if (wasPlaying && state.audioPlaybackId === thisPlaybackId) {
        await playNextSegment(true); // Pass true to indicate we want to play
    }
}

function highlightCurrentChunk() {
    const previousWasVisible = state.scroll.previousChunk >= 0 &&
        state.scroll.previousChunk !== state.currentChunk &&
        isElementPartiallyVisible(document.querySelector(`.chunk-container[data-chunk-index="${state.scroll.previousChunk}"]`));

    document.querySelectorAll('.chunk-container').forEach(chunk => {
        chunk.classList.remove('current', 'played');
        const chunkIndex = parseInt(chunk.dataset.chunkIndex);
        if (chunkIndex === state.currentChunk) {
            chunk.classList.add('current');
        } else if (chunkIndex < state.currentChunk) {
            chunk.classList.add('played');
        }
    });

    const isSequential = Math.abs(state.currentChunk - state.scroll.previousChunk) === 1;
    const noRecentManualScroll = (Date.now() - state.scroll.lastManual) > SCROLL.MANUAL_TIMEOUT;
    const currentChunk = document.querySelector(`.chunk-container[data-chunk-index="${state.currentChunk}"]`);

    if (state.autoScrollEnabled && currentChunk && previousWasVisible && isSequential && noRecentManualScroll) {
        scrollToCurrentChunk();
    }

    state.scroll.previousChunk = state.currentChunk;
}

function isElementPartiallyVisible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const containerRect = DOM.textDisplay.getBoundingClientRect();
    return rect.bottom > containerRect.top && rect.top < containerRect.bottom;
}

async function scrollToCurrentChunk() {
    state.autoScrollEnabled = true;

    console.log(`[STREAM] Scrolling to current chunk ${state.currentChunk}`);

    // Check if chunk exists in DOM
    let currentChunk = document.querySelector(`.chunk-container[data-chunk-index="${state.currentChunk}"]`);
    
    if (!currentChunk) {
        // Chunk not in DOM, need to load it with surrounding chunks
        console.log(`[STREAM] Chunk ${state.currentChunk} not in DOM, loading chunks around it`);
        await loadChunksAround(state.currentChunk, LOAD.RADIUS);
        currentChunk = document.querySelector(`.chunk-container[data-chunk-index="${state.currentChunk}"]`);
        
        if (!currentChunk) {
            console.warn(`[STREAM] Chunk ${state.currentChunk} still not in DOM after loading`);
            return;
        }
    }

    // Now ensure the chunk content is loaded
    if (currentChunk.dataset.loaded === 'false') {
        await loadSingleChunk(state.currentChunk);
        currentChunk = document.querySelector(`.chunk-container[data-chunk-index="${state.currentChunk}"]`);
    }

    if (currentChunk) {
        performScroll(currentChunk);
    }
}

function performScroll(element) {
    state.scroll.autoInProgress = true;
    element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => state.scroll.autoInProgress = false, SCROLL.SCROLL_DELAY);
}

// ========== PROGRESS & TIME ==========
function updateProgress() {
    if (!state.book) return;

    const speed = parseFloat(DOM.speedControl?.value || 1);

    if (state.progressMode === 'chapter') {
        const chapter = getCurrentChapter();
        if (!chapter) return updateProgressBookMode(speed);

        const chapterChunks = chapter.end_chunk - chapter.start_chunk + 1;
        const chapterCurrentChunk = state.currentChunk - chapter.start_chunk;
        const progressPercent = (chapterCurrentChunk / chapterChunks) * 100;

        const currentChar = state.currentChunk < state.book.chunks.length ? state.book.chunks[state.currentChunk].start_idx : chapter.end_idx;
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
    const currentChar = state.currentChunk < state.book.chunks.length ? state.book.chunks[state.currentChunk].start_idx : state.book.total_chars;
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

function getCurrentChapter() {
    if (!state.book || state.currentChunk >= state.book.chunks.length) return null;
    const currentChar = state.book.chunks[state.currentChunk].start_idx;
    return state.book.chapters.find(ch => currentChar >= ch.start_idx && currentChar < ch.end_idx);
}

async function seekToPosition(percent) {
    if (!state.book) return;

    let targetChunk;
    if (state.progressMode === 'chapter') {
        const chapter = getCurrentChapter();
        if (!chapter) {
            targetChunk = Math.floor((percent / 100) * state.book.total_chunks);
        } else {
            const chapterChunks = chapter.end_chunk - chapter.start_chunk + 1;
            targetChunk = chapter.start_chunk + Math.floor((percent / 100) * chapterChunks);
        }
    } else {
        targetChunk = Math.floor((percent / 100) * state.book.total_chunks);
    }


    targetChunk = Math.max(0, Math.min(targetChunk, state.book.total_chunks - 1));

    console.log('[STREAM] Seeking to chunk:', targetChunk);

    await jumpToChunk(targetChunk);
}

function changeSpeed() {
    DOM.audio.playbackRate = parseFloat(DOM.speedControl.value);
    updateProgress();
}

function updatePlayButton() {
    DOM.playBtn.textContent = state.isPlaying ? '⏸' : '▶';
}

// ========== MODALS ==========
function showChapters() {
    showModal('chapters', () => {
        const list = DOM.modalList('chapter');
        list.innerHTML = '';

        state.book.chapters.forEach((chapter, index) => {
            const chapterStartChunk = state.book.chunks.findIndex(
                chunk => chunk.start_idx <= chapter.start_idx && chunk.end_idx > chapter.start_idx
            );

            const item = document.createElement('div');
            item.className = 'chapter-item';

            const isInChapter = state.currentChunk >= 0 && state.book.chunks[state.currentChunk] &&
                state.book.chunks[state.currentChunk].start_idx >= chapter.start_idx &&
                state.book.chunks[state.currentChunk].start_idx < chapter.end_idx;

            if (isInChapter) item.classList.add('active');

            const timeEstimate = chapter.length / CHARS.PER_SECOND;
            item.innerHTML = `
                <div class="chapter-name">Chapter ${index + 1}</div>
                <div class="chapter-info">${chapter.name.substring(0, 100)}${chapter.name.length > 100 ? '...' : ''}</div>
                <div class="chapter-info">${formatNumber(chapter.length)} characters • ~${formatTime(timeEstimate)}</div>
            `;

            item.onclick = async () => {
                closeModal('chapters');
                if (chapterStartChunk >= 0) await jumpToChunk(chapterStartChunk);
            };

            list.appendChild(item);
        });
    });
}

const closeChaptersModal = () => closeModal('chapters');

async function showSettings() {
    showModal('settings', () => {
        const modelSelect = document.getElementById('modelSelect');
        modelSelect.innerHTML = '';

        for (const [modelName, modelData] of Object.entries(state.models)) {
            const option = document.createElement('option');
            option.value = modelName;
            option.textContent = modelName;
            if (modelName === state.settings.preferred_model) option.selected = true;
            modelSelect.appendChild(option);
        }

        updateVoiceOptions();

        document.getElementById('fontSizeSelect').value = state.settings.font_size || 16;
        document.getElementById('fontFamilySelect').value = state.settings.font_family || 'system';
        document.getElementById('progressModeSelect').value = state.progressMode;
        document.getElementById('timeModeSelect').value = state.timeMode;

        const showTitleToggle = document.getElementById('showTitleToggle');
        const showProgressBarToggle = document.getElementById('showProgressBarToggle');
        const showImagesToggle = document.getElementById('showImagesToggle');
        const showSleepTimerToggle = document.getElementById('showSleepTimerToggle');
        
        if (showTitleToggle) showTitleToggle.checked = state.settings.show_title !== undefined ? state.settings.show_title : true;
        if (showProgressBarToggle) showProgressBarToggle.checked = state.settings.show_progress_bar !== undefined ? state.settings.show_progress_bar : true;
        if (showImagesToggle) showImagesToggle.checked = state.showImages;
        if (showSleepTimerToggle) showSleepTimerToggle.checked = state.settings.show_sleep_timer || false;

        // Populate sleep timer input
        const sleepTimerInput = document.getElementById('sleepTimerInput');
        if (sleepTimerInput) {
            sleepTimerInput.value = state.settings.sleep_timer_minutes || 0;
        }

        const saveStreamAudioToggle = document.getElementById('saveStreamAudioToggle');
        if (saveStreamAudioToggle) saveStreamAudioToggle.checked = state.settings.save_stream_audio || false;
        
        // Load cache status
        refreshCacheStatus();
    
    });
}

const closeSettingsModal = () => closeModal('settings');


// ========== CACHE MANAGEMENT ==========
async function refreshCacheStatus() {
    const contentEl = document.getElementById('cacheStatusContent');
    const actionsEl = document.getElementById('cacheActions');
    if (!contentEl) return;
    
    contentEl.innerHTML = '<span style="color:#888;">Loading...</span>';
    
    try {
        const res = await fetch(`${API_BASE}/stream/cache-status?ebook_path=${encodeURIComponent(EBOOK_PATH)}`);
        if (!res.ok) throw new Error('Failed to load cache status');
        
        const data = await res.json();
        
        if (!data.has_cache || data.cached_chunks === 0) {
            contentEl.innerHTML = '<span style="color:#888;">No cached audio for this book</span>';
            if (actionsEl) actionsEl.style.display = 'none';
        } else {
            let html = `<div style="margin-bottom:6px;"><strong>${data.cached_chunks}</strong> cached audio segments (${data.total_size_mb} MB)</div>`;
            
            if (data.model_voice_caches && data.model_voice_caches.length > 0) {
                html += '<div style="font-size:11px; color:#666;">';
                data.model_voice_caches.forEach(cache => {
                    html += `<div style="margin:2px 0;">• ${cache.model_voice}: ${cache.files} files (${cache.size_mb} MB)</div>`;
                });
                html += '</div>';
            }
            
            contentEl.innerHTML = html;
            if (actionsEl) actionsEl.style.display = 'block';
        }
    } catch (error) {
        contentEl.innerHTML = '<span style="color:#f44;">Error loading cache status</span>';
        console.error('Cache status error:', error);
    }
}

async function clearAudioCache() {
    if (!confirm('Clear all cached audio for this book? This cannot be undone.')) return;
    
    const contentEl = document.getElementById('cacheStatusContent');
    const actionsEl = document.getElementById('cacheActions');
    
    try {
        if (contentEl) contentEl.innerHTML = '<span style="color:#888;">Clearing cache...</span>';
        
        const res = await fetch(`${API_BASE}/stream/cache?ebook_path=${encodeURIComponent(EBOOK_PATH)}`, {
            method: 'DELETE'
        });
        
        if (!res.ok) throw new Error('Failed to clear cache');
        
        const data = await res.json();
        showToast(`Cleared ${data.deleted_files} cached files (${data.deleted_size_mb} MB)`);
        
        // Refresh status
        await refreshCacheStatus();
    } catch (error) {
        showToast('Error clearing cache');
        console.error('Clear cache error:', error);
        await refreshCacheStatus();
    }
}

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

async function saveSettings(event) {
    event.preventDefault();

    const showImages = document.getElementById('showImagesToggle')?.checked ?? false;
    const imagesChanged = showImages !== state.showImages;
    
    // Update local state immediately
    state.settings.preferred_model = document.getElementById('modelSelect').value;
    state.settings.preferred_voice = document.getElementById('voiceSelect').value;
    state.settings.font_size = parseInt(document.getElementById('fontSizeSelect').value);
    state.settings.font_family = document.getElementById('fontFamilySelect').value;
    state.settings.show_title = document.getElementById('showTitleToggle')?.checked ?? true;
    state.settings.show_progress_bar = document.getElementById('showProgressBarToggle')?.checked ?? true;
    state.showImages = showImages;
    state.settings.save_stream_audio = document.getElementById('saveStreamAudioToggle')?.checked ?? false;
    
    // Apply settings and close modal immediately
    applyDisplaySettings();
    applyVisibilitySettings();
    closeSettingsModal();
    
    // Save to server in background (don't await)
    fetch(`${API_BASE}/stream/settings`, {
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
            show_images: showImages,
            save_stream_audio: state.settings.save_stream_audio
        })
    }).catch(err => logError('Settings save error', err));
    
    // If images setting changed, re-parse the book and reload chunks
    if (imagesChanged) {
        showToast(showImages ? 'Loading images...' : 'Hiding images...');
        // Clear image cache when disabling images
        if (!showImages) {
            state.imageCache.clear();
        }
        // Re-parse the book with/without images
        await parseBook();
        // Reload current chunk area
        await loadChunksAround(state.currentChunk, LOAD.RADIUS);
        highlightCurrentChunk();
        scrollToCurrentChunk();
    }
}

async function showBookmarks() {
    if (!state.book || !state.progress) return;

    showModal('bookmarks', async () => {
        const list = DOM.modalList('bookmark');
        list.innerHTML = '';

        const bookmarkIndices = state.progress.bookmark_indices || [];
        
        if (bookmarkIndices.length === 0) {
            list.innerHTML = '<div class="loading">No bookmarks yet. Swipe left on a chunk to add one!</div>';
        } else {
            // Get bookmark data (text previews are stored in the bookmarks dict)
            const bookmarks = state.progress.bookmarks || {};
            
            bookmarkIndices.forEach(chunkIndex => {
                if (chunkIndex >= state.book.chunks.length) return;

                const chunk = state.book.chunks[chunkIndex];
                const item = document.createElement('div');
                item.className = 'chapter-item';
                if (chunkIndex === state.currentChunk) item.classList.add('active');

                let chapterName = 'Unknown Chapter';
                for (const chapter of state.book.chapters) {
                    if (chunk.start_idx >= chapter.start_idx && chunk.start_idx < chapter.end_idx) {
                        chapterName = chapter.name;
                        break;
                    }
                }

                // Get stored text preview from bookmarks dict
                let preview = '';
                if (typeof bookmarks === 'object' && !Array.isArray(bookmarks)) {
                    preview = bookmarks[String(chunkIndex)] || '';
                }
                
                // If no stored preview, try to get from DOM if chunk is loaded
                if (!preview) {
                    const chunkDiv = document.querySelector(`.chunk-container[data-chunk-index="${chunkIndex}"]`);
                    if (chunkDiv?.dataset.loaded === 'true') {
                        const text = chunkDiv.textContent || '';
                        preview = text.substring(0, 100);
                    }
                }
                
                // Truncate and add ellipsis if needed
                if (preview.length > 100) {
                    preview = preview.substring(0, 100) + '...';
                } else if (!preview) {
                    preview = '(No preview available)';
                }

                item.innerHTML = `
                    <div class="chapter-name">⭐ Chunk ${chunkIndex + 1} • ${chapterName}</div>
                    <div class="chapter-info">${preview}</div>
                `;

                item.onclick = async () => {
                    closeModal('bookmarks');
                    await jumpToChunk(chunkIndex);
                };

                list.appendChild(item);
            });
        }
    });
}

const closeBookmarksModal = () => closeModal('bookmarks');

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
        
        // Load show_images setting
        state.showImages = state.settings.show_images === true;

        applyVisibilitySettings();
    } catch (error) {
        logError('Settings load error', error);
        state.settings = {
            font_size: 16,
            font_family: 'system',
            preferred_model: null,
            preferred_voice: null,
            progress_mode: 'book',
            time_mode: 'total',
            show_title: true,
            show_progress_bar: true,
            show_images: false,
            sleep_timer_minutes: 0,
            show_sleep_timer: false
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
        // Ensure bookmark_indices exists for backwards compatibility
        if (!state.progress.bookmark_indices) {
            // If we have old-style bookmarks (array), use them as indices
            if (Array.isArray(state.progress.bookmarks)) {
                state.progress.bookmark_indices = state.progress.bookmarks;
            } else {
                state.progress.bookmark_indices = Object.keys(state.progress.bookmarks || {}).map(k => parseInt(k)).sort((a, b) => a - b);
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
            body: JSON.stringify({
                ebook_path: EBOOK_PATH,
                chunk_index: state.currentChunk
            })
        });
    } catch (error) {
        logError('Progress save error', error);
    }
}

async function toggleCurrentBookmark() {
    await toggleBookmark(state.currentChunk);
}

async function toggleBookmark(chunkIndex) {
    try {
        // Get text preview from the chunk element (if loaded)
        let textPreview = '';
        const chunkDiv = document.querySelector(`.chunk-container[data-chunk-index="${chunkIndex}"]`);
        if (chunkDiv?.dataset.loaded === 'true') {
            const text = chunkDiv.textContent || '';
            textPreview = text.substring(0, 150).trim();
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

// Helper to check if a chunk is bookmarked
function isBookmarked(chunkIndex) {
    if (!state.progress) return false;
    if (state.progress.bookmark_indices) {
        return state.progress.bookmark_indices.includes(chunkIndex);
    }
    // Fallback for old format
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

function toggleProgressMode() {
    const select = document.getElementById('progressModeSelect');
    state.progressMode = select ? select.value : (state.progressMode === 'book' ? 'chapter' : 'book');
    state.settings.progress_mode = state.progressMode;
    saveSettingsToServer();
    updateProgress();
}

function toggleTimeMode() {
    const select = document.getElementById('timeModeSelect');
    state.timeMode = select ? select.value : (state.timeMode === 'total' ? 'remaining' : 'total');
    state.settings.time_mode = state.timeMode;
    saveSettingsToServer();
    updateProgress();
}

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

function toggleTitle() {
    state.settings.show_title = document.getElementById('showTitleToggle').checked;
    applyVisibilitySettings();
    saveSettingsToServer();
}

function toggleProgressBar() {
    state.settings.show_progress_bar = document.getElementById('showProgressBarToggle').checked;
    applyVisibilitySettings();
    saveSettingsToServer();
}

async function toggleImages() {
    const showImages = document.getElementById('showImagesToggle').checked;
    const imagesChanged = showImages !== state.showImages;
    
    state.showImages = showImages;
    state.settings.show_images = showImages;
    saveSettingsToServer();
    
    if (imagesChanged) {
        showToast(showImages ? 'Loading images...' : 'Hiding images...');
        // Clear image cache when disabling images
        if (!showImages) {
            state.imageCache.clear();
        }
        // Mark all chunks as needing reload
        document.querySelectorAll('.chunk-container').forEach(chunk => {
            chunk.dataset.loaded = 'false';
            chunk.textContent = 'Loading...';
            chunk.style.minHeight = '200px';
        });
        // Reload current chunks
        await loadChunksAround(state.currentChunk, LOAD.RADIUS);
        highlightCurrentChunk();
        scrollToCurrentChunk();
    }
}

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
                sleep_timer_minutes: state.settings.sleep_timer_minutes,
                show_sleep_timer: state.settings.show_sleep_timer
            })
        });
    } catch (error) {
        logError('Error saving settings', error);
    }
}

// ========== SLEEP TIMER ==========
function initSleepTimer() {
    // Load sleep timer setting from settings
    const sleepTimerMinutes = state.settings.sleep_timer_minutes || 0;
    const showSleepTimer = state.settings.show_sleep_timer || false;
    
    state.sleepTimer.minutes = sleepTimerMinutes;
    state.sleepTimer.enabled = sleepTimerMinutes > 0;
    state.sleepTimer.showTimer = showSleepTimer;
    state.sleepTimer.lastActivityTime = Date.now();

    // Populate the input and checkbox if they exist
    const input = document.getElementById('sleepTimerInput');
    if (input) {
        input.value = String(sleepTimerMinutes);
    }
    
    const checkbox = document.getElementById('showSleepTimerToggle');
    if (checkbox) {
        checkbox.checked = showSleepTimer;
    }
    
    createSleepTimerDisplay();
    updateSleepTimerDisplay();
    
    // Setup user interaction listeners if sleep timer is enabled
    if (state.sleepTimer.enabled) {
        setupSleepTimerListeners();
        startSleepTimer();
    }
}

function createSleepTimerDisplay() {
    // Create the timer display element if it doesn't exist
    let timerDisplay = document.getElementById('sleepTimerDisplay');
    if (!timerDisplay) {
        timerDisplay = document.createElement('div');
        timerDisplay.id = 'sleepTimerDisplay';
        timerDisplay.style.cssText = `
            position: fixed;
            top: 60px;
            right: 10px;
            background: rgba(0, 0, 0, 0.7);
            color: #fff;
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 14px;
            z-index: 9999;
            display: none;
            font-family: monospace;
            min-width: 80px;
            text-align: center;
        `;
        document.body.appendChild(timerDisplay);
    }
    return timerDisplay;
}

function setupSleepTimerListeners() {
    // Only set up listeners once to avoid duplicates
    if (state.sleepTimer.listenersSetup) return;
    
    let lastResetTime = 0;
    const RESET_THROTTLE = 500; // Only allow reset once per 500ms max
    
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

    // Reset timer on scroll, but only if it's not auto-scroll
    const handleScrollReset = () => {
        if (!state.scroll.autoInProgress) {
            resetSleepTimer();
        }
    };

    // Store reference to reset functions
    state.sleepTimer.resetFunction = resetSleepTimer;
    state.sleepTimer.scrollResetFunction = handleScrollReset;

    // Track various user interactions (less frequent ones without throttle, frequent ones we throttle)
    document.addEventListener('click', resetSleepTimer, true);
    document.addEventListener('scroll', handleScrollReset, true);
    document.addEventListener('keydown', resetSleepTimer, true);
    document.addEventListener('touchstart', resetSleepTimer, true);
    document.addEventListener('touchend', resetSleepTimer, true);
    document.addEventListener('input', resetSleepTimer, true);
    document.addEventListener('change', resetSleepTimer, true);
    
    // Also listen on the text display
    const textDisplay = DOM.textDisplay;
    if (textDisplay) {
        textDisplay.addEventListener('scroll', handleScrollReset, true);
    }
    
    state.sleepTimer.listenersSetup = true;
    log('Sleep timer listeners set up');
}

function startSleepTimer() {
    if (!state.sleepTimer.enabled || state.sleepTimer.minutes === 0) return;

    // Clear any existing timeout and update interval
    if (state.sleepTimer.timeoutId) {
        clearTimeout(state.sleepTimer.timeoutId);
    }
    if (state.sleepTimer.updateIntervalId) {
        clearInterval(state.sleepTimer.updateIntervalId);
    }

    const timeoutMs = state.sleepTimer.minutes * 60 * 1000;
    log(`Sleep timer started: ${state.sleepTimer.minutes} minutes`);

    state.sleepTimer.timeoutId = setTimeout(() => {
        if (state.isPlaying) {
            log('Sleep timer triggered - pausing playback');
            stopPlaying();
            showToast(`Sleep timer triggered - paused after ${state.sleepTimer.minutes} minutes of inactivity`);
        }
        if (state.sleepTimer.updateIntervalId) {
            clearInterval(state.sleepTimer.updateIntervalId);
        }
        updateSleepTimerDisplay();
    }, timeoutMs);
    
    // Update display every second if showing timer
    if (state.sleepTimer.showTimer) {
        state.sleepTimer.updateIntervalId = setInterval(() => {
            updateSleepTimerDisplay();
        }, 1000);
    }
    
    updateSleepTimerDisplay();
}

function updateSleepTimer() {
    const input = document.getElementById('sleepTimerInput');
    const checkbox = document.getElementById('showSleepTimerToggle');
    
    if (!input) return;

    const minutes = Math.max(0, parseInt(input.value) || 0);
    const showTimer = checkbox ? checkbox.checked : false;
    
    // Update input to reflect the sanitized value
    input.value = minutes;
    
    state.sleepTimer.minutes = minutes;
    state.sleepTimer.enabled = minutes > 0;
    state.sleepTimer.showTimer = showTimer;
    state.settings.sleep_timer_minutes = minutes;
    state.settings.show_sleep_timer = showTimer;

    // Clear existing timer
    if (state.sleepTimer.timeoutId) {
        clearTimeout(state.sleepTimer.timeoutId);
        state.sleepTimer.timeoutId = null;
    }
    if (state.sleepTimer.updateIntervalId) {
        clearInterval(state.sleepTimer.updateIntervalId);
        state.sleepTimer.updateIntervalId = null;
    }

    // Setup or remove listeners based on new setting
    if (state.sleepTimer.enabled) {
        setupSleepTimerListeners();
        state.sleepTimer.lastActivityTime = Date.now();
        startSleepTimer();
        showToast(`Sleep timer set to ${minutes} minutes${showTimer ? ' (displaying)' : ''}`);
    } else {
        // Reset listeners flag when disabling
        state.sleepTimer.listenersSetup = false;
        showToast('Sleep timer disabled');
    }

    updateSleepTimerDisplay();
    
    // Save to server in background
    fetch(`${API_BASE}/stream/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            sleep_timer_minutes: minutes,
            show_sleep_timer: showTimer
        })
    }).catch(err => logError('Failed to save sleep timer setting', err));
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
    
    // Update on-screen timer display
    if (timerDisplay) {
        if (state.sleepTimer.showTimer && state.sleepTimer.enabled && state.sleepTimer.minutes > 0) {
            // Calculate time remaining
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
    // Clear all sleep timer intervals and timeouts
    if (state.sleepTimer.timeoutId) {
        clearTimeout(state.sleepTimer.timeoutId);
        state.sleepTimer.timeoutId = null;
    }
    if (state.sleepTimer.updateIntervalId) {
        clearInterval(state.sleepTimer.updateIntervalId);
        state.sleepTimer.updateIntervalId = null;
    }
    
    // Hide the timer display
    const timerDisplay = document.getElementById('sleepTimerDisplay');
    if (timerDisplay) {
        timerDisplay.style.display = 'none';
    }
    
    // Reset listeners flag so they can be re-setup when page loads again
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

    // Only prevent default for clear horizontal swipes, allow vertical scrolling
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

// ========== NAVIGATION ==========
function goBack() {
    const parts = EBOOK_PATH?.split('/');
    if (parts?.length > 1) {
        const directory = parts.slice(0, -1).join('/');
        shutdownAndNavigate(`/?tab=files&path=${encodeURIComponent(directory)}`);
    } else {
        shutdownAndNavigate('/?tab=files');
    }
}

function shutdownAndNavigate(targetHref) {
    hideLoading();
    hideAudioStatus();
    
    // Clean up sleep timer
    cleanupSleepTimer();
    
    // Reset all blocking flags
    state.isJumping = false;
    state.isGeneratingAudio = false;
    state.scroll.isLoadingChunks = false;
    state.isUserStopping = true;

    state.inFlightControllers.forEach(c => { try { c.abort(); } catch (e) { } });
    state.inFlightControllers.clear();

    if (state.currentAudioBlobUrl) {
        try { URL.revokeObjectURL(state.currentAudioBlobUrl); } catch (e) { }
        state.currentAudioBlobUrl = null;
    }
    state.audioCache.clear();

    setTimeout(() => window.location.href = targetHref, 50);
}
