// Audio player management

// ========== STATE MANAGEMENT ==========
const state = {
    audiobook: {
        id: null,
        chunks: [],
        currentChunk: -1,
        totalDuration: 0
    },
    lrc: {
        data: [],
        fullData: [],
        loadedRange: { start: -1, end: -1 },
        lastHighlightedIndex: -1
    },
    bookmarks: [],
    touch: {
        startX: 0,
        startY: 0,
        startTime: 0,
        longPressTimer: null,
        longPressTarget: null,
        longPressTriggered: false
    },
    scroll: {
        auto: true,
        userScrolling: false,
        timeout: null,
        suppressAuto: false
    },
    playback: {
        isPlaying: false,
        isTransitioning: false,
        currentBlobUrl: null
    },
    cache: {
        chunks: new Map(),
        preloadInProgress: false
    },
    intervals: {
        positionSave: null,
        chunkUpdate: null
    },
    progress: {
        mode: 'book',
        timeMode: 'total',
        pendingSeekValue: null  // Track pending seek during drag
    },
    cachedChapters: null,
    images: {
        enabled: false,
        data: {},         // Map of image_id to data URL
        chunkImages: [],  // Map of chunk index to image IDs
        loaded: false
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
const CACHE_SIZE = 5;
const LRC_WINDOW_SIZE = 250;
const LRC_BUFFER_RADIUS = 250;
const LRC_CLEANUP_MULT = 3;

// ========== DOM HELPERS ==========
const DOM = {
    get player() { return document.getElementById('audioPlayer'); },
    get lrcDisplay() { return document.getElementById('lrcDisplay'); },
    get progressBar() { return document.getElementById('globalProgressBar'); },
    get currentTime() { return document.getElementById('currentTime'); },
    get totalTime() { return document.getElementById('totalTime'); },
    get playerSection() { return document.getElementById('playerSection'); },
    get playerTitle() { return document.getElementById('playerTitle'); },
    get playPauseBtn() { return document.getElementById('playPauseBtn'); }
};

// ========== UTILITY FUNCTIONS ==========
const formatTime = (seconds) => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    const pad = (n) => String(n).padStart(2, '0');
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
};

const showToast = (message) => {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        toast.style.cssText = `position:fixed;bottom:80px;left:50%;transform:translateX(-50%);
            background:var(--toast-bg);color:var(--toast-text);padding:10px 20px;border-radius:20px;
            z-index:10000;opacity:0;transition:opacity 0.3s;`;
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.opacity = '1';
    setTimeout(() => toast.style.opacity = '0', 2000);
};

const isElementPartiallyVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const containerRect = DOM.lrcDisplay.getBoundingClientRect();
    return rect.bottom > containerRect.top && rect.top < containerRect.bottom;
};

// ========== PLAYBACK TRACKING ==========
async function trackAudiobookPlayed(audiobookPath) {
    try {
        console.log('[TRACKING] Starting tracking for:', audiobookPath);
        const prefs = await apiCall('/audiobooks/preferences/get');
        if (!prefs.audiobooks) prefs.audiobooks = {};
        prefs.audiobooks[audiobookPath] = { last_played: Date.now() };

        await apiCall('/audiobooks/preferences/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(prefs)
        });
        console.log(`[TRACKING] Successfully recorded play time for: ${audiobookPath}`);
    } catch (error) {
        console.error('[TRACKING] Failed to track audiobook play:', error);
    }
}

// ========== CHUNK MANAGEMENT ==========
async function loadAudioChunk(audiobookId, chunkIndex, useCache = true, retryCount = 0) {
    const MAX_RETRIES = 3;
    console.log(`[CHUNK] Loading chunk ${chunkIndex} (useCache: ${useCache}, attempt: ${retryCount + 1})`);

    if (useCache && state.cache.chunks.has(chunkIndex)) {
        console.log(`[CHUNK CACHE] Hit for chunk ${chunkIndex}`);
        return URL.createObjectURL(state.cache.chunks.get(chunkIndex));
    }

    try {
        const response = await fetch(`/api/audiobooks/${audiobookId}/audio/${chunkIndex}?t=${Date.now()}`, {
            cache: 'no-store',
            headers: {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache'
            }
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const blob = await response.blob();
        state.cache.chunks.set(chunkIndex, blob);
        console.log(`[CHUNK CACHE] Stored chunk ${chunkIndex}, cache size: ${state.cache.chunks.size}`);

        cleanupChunkCache(chunkIndex);

        const blobUrl = URL.createObjectURL(blob);
        console.log(`[CHUNK] Created blob URL for chunk ${chunkIndex}:`, blobUrl);
        return blobUrl;
    } catch (error) {
        console.error(`[CHUNK] Failed to load chunk ${chunkIndex} (attempt ${retryCount + 1}):`, error);
        
        if (retryCount < MAX_RETRIES) {
            console.log(`[CHUNK] Retrying chunk ${chunkIndex} in ${(retryCount + 1) * 500}ms...`);
            await new Promise(resolve => setTimeout(resolve, (retryCount + 1) * 500));
            return loadAudioChunk(audiobookId, chunkIndex, useCache, retryCount + 1);
        }
        throw error;
    }
}

function cleanupChunkCache(currentIndex) {
    const minKeep = Math.max(0, currentIndex - 2);
    const maxKeep = Math.min(state.audiobook.chunks.length - 1, currentIndex + CACHE_SIZE);

    for (const [index] of state.cache.chunks) {
        if (index < minKeep || index > maxKeep) {
            state.cache.chunks.delete(index);
            console.log(`[CHUNK CACHE] Removed chunk ${index} from cache`);
        }
    }
}

async function preloadAdjacentChunks(centerIndex) {
    if (state.cache.preloadInProgress || !state.audiobook.id) return;

    state.cache.preloadInProgress = true;
    console.log(`[CHUNK CACHE] Preloading chunks around ${centerIndex}`);

    try {
        const promises = [];

        for (let i = 1; i <= CACHE_SIZE; i++) {
            const nextIndex = centerIndex + i;
            if (nextIndex < state.audiobook.chunks.length && !state.cache.chunks.has(nextIndex)) {
                promises.push(
                    loadAudioChunk(state.audiobook.id, nextIndex, false)
                        .then(url => URL.revokeObjectURL(url))
                        .catch(err => console.warn(`[CHUNK CACHE] Failed to preload chunk ${nextIndex}:`, err))
                );
            }
        }

        for (let i = 1; i <= 2; i++) {
            const prevIndex = centerIndex - i;
            if (prevIndex >= 0 && !state.cache.chunks.has(prevIndex)) {
                promises.push(
                    loadAudioChunk(state.audiobook.id, prevIndex, false)
                        .then(url => URL.revokeObjectURL(url))
                        .catch(err => console.warn(`[CHUNK CACHE] Failed to preload chunk ${prevIndex}:`, err))
                );
            }
        }

        await Promise.all(promises);
        console.log(`[CHUNK CACHE] Preload complete, cache size: ${state.cache.chunks.size}`);
    } finally {
        state.cache.preloadInProgress = false;
    }
}

async function loadAudioChunksMetadata(audiobookId) {
    console.log('[CHUNKS] Loading chunks metadata');
    try {
        const data = await apiCall(`/audiobooks/${audiobookId}/chunks`);
        state.audiobook.chunks = data.chunks || [];
        state.audiobook.totalDuration = data.total_duration || 0;
        console.log(`[CHUNKS] Loaded ${state.audiobook.chunks.length} chunks, total duration: ${state.audiobook.totalDuration}s`);
        return state.audiobook.chunks;
    } catch (error) {
        console.error('[CHUNKS] Failed to load chunks metadata:', error);
        state.audiobook.chunks = [];
        state.audiobook.totalDuration = 0;
        return [];
    }
}

function getChunkForGlobalTime(globalTime) {
    // Use epsilon for floating-point comparison tolerance (1ms)
    const EPSILON = 0.001;

    for (let i = 0; i < state.audiobook.chunks.length; i++) {
        const chunk = state.audiobook.chunks[i];
        const chunkEnd = chunk.start_time + chunk.duration;

        // Check if globalTime is within chunk boundaries with tolerance
        if (globalTime >= chunk.start_time - EPSILON && globalTime <= chunkEnd + EPSILON) {
            const localTime = Math.max(0, globalTime - chunk.start_time);
            console.log(`[CHUNK MAP] Global ${globalTime}s → Chunk ${i} (${chunk.start_time}s-${chunkEnd}s) at local ${localTime}s`);
            return { chunkIndex: i, localTime: localTime };
        }
    }

    if (state.audiobook.chunks.length > 0) {
        const lastChunk = state.audiobook.chunks[state.audiobook.chunks.length - 1];
        const lastChunkEnd = lastChunk.start_time + lastChunk.duration;

        // If seeking beyond the last chunk, cap at the end of last chunk
        if (globalTime >= lastChunkEnd) {
            console.log(`[CHUNK MAP] Global ${globalTime}s is beyond last chunk, capping at chunk ${state.audiobook.chunks.length - 1} end (${lastChunkEnd}s)`);
            return { chunkIndex: state.audiobook.chunks.length - 1, localTime: lastChunk.duration };
        }

        // If we still haven't found it, find the closest chunk
        // This can happen if there are gaps in chunk times or rounding errors
        let closestChunk = 0;
        let smallestDiff = Math.abs(globalTime - state.audiobook.chunks[0].start_time);
        
        for (let i = 1; i < state.audiobook.chunks.length; i++) {
            const chunk = state.audiobook.chunks[i];
            const diffToStart = Math.abs(globalTime - chunk.start_time);
            const diffToEnd = Math.abs(globalTime - (chunk.start_time + chunk.duration));
            const minDiff = Math.min(diffToStart, diffToEnd);
            
            if (minDiff < smallestDiff) {
                smallestDiff = minDiff;
                closestChunk = i;
            }
        }
        
        const chunk = state.audiobook.chunks[closestChunk];
        const localTime = Math.max(0, Math.min(chunk.duration, globalTime - chunk.start_time));
        console.warn(`[CHUNK MAP] Global ${globalTime}s not found in any chunk, using closest chunk ${closestChunk} at local ${localTime}s`);
        return { chunkIndex: closestChunk, localTime: localTime };
    }

    return { chunkIndex: 0, localTime: 0 };
}

function getGlobalTime(chunkIndex, localTime) {
    if (chunkIndex >= 0 && chunkIndex < state.audiobook.chunks.length) {
        return state.audiobook.chunks[chunkIndex].start_time + localTime;
    }
    return 0;
}

async function switchToChunk(chunkIndex, seekTime = 0, forcePlayState = null, retryCount = 0) {
    const MAX_RETRIES = 3;
    
    if (state.playback.isTransitioning && retryCount === 0) {
        console.log('[CHUNK] Already transitioning, ignoring request');
        return;
    }

    if (chunkIndex < 0 || chunkIndex >= state.audiobook.chunks.length) {
        console.warn(`[CHUNK] Invalid chunk index: ${chunkIndex}`);
        return;
    }

    const audioPlayer = DOM.player;
    const wasPlaying = forcePlayState !== null ? forcePlayState : !audioPlayer.paused;

    console.log(`[CHUNK] Switching to chunk ${chunkIndex}, seek to ${seekTime}s, wasPlaying: ${wasPlaying}, attempt: ${retryCount + 1}`);
    state.playback.isTransitioning = true;

    try {
        if (state.playback.currentBlobUrl) {
            URL.revokeObjectURL(state.playback.currentBlobUrl);
            state.playback.currentBlobUrl = null;
        }

        state.playback.currentBlobUrl = await loadAudioChunk(state.audiobook.id, chunkIndex);
        audioPlayer.src = state.playback.currentBlobUrl;

        await new Promise((resolve, reject) => {
            const timeoutId = setTimeout(() => reject(new Error('Timeout loading chunk')), 8000);
            
            const onReady = () => {
                clearTimeout(timeoutId);
                audioPlayer.removeEventListener('error', onError);
                resolve();
            };
            
            const onError = (e) => {
                clearTimeout(timeoutId);
                audioPlayer.removeEventListener('loadeddata', onReady);
                reject(new Error('Audio load error'));
            };

            if (audioPlayer.readyState >= 2) {
                onReady();
            } else {
                audioPlayer.addEventListener('loadeddata', onReady, { once: true });
                audioPlayer.addEventListener('error', onError, { once: true });
            }
        });

        audioPlayer.currentTime = seekTime;
        state.audiobook.currentChunk = chunkIndex;

        if (wasPlaying) {
            console.log('[CHUNK] Resuming playback');
            await audioPlayer.play();
        } else {
            console.log('[CHUNK] Not resuming (was paused)');
        }

        updatePlayPauseButton();
        preloadAdjacentChunks(chunkIndex);

        console.log(`[CHUNK] Successfully switched to chunk ${chunkIndex} at ${seekTime}s`);
    } catch (error) {
        console.error(`[CHUNK] Failed to switch to chunk ${chunkIndex} (attempt ${retryCount + 1}):`, error);
        
        // Retry logic
        if (retryCount < MAX_RETRIES) {
            console.log(`[CHUNK] Retrying switch to chunk ${chunkIndex} in ${(retryCount + 1) * 500}ms...`);
            state.playback.isTransitioning = false;
            
            // Clear the cached chunk in case it was corrupted
            state.cache.chunks.delete(chunkIndex);
            
            await new Promise(resolve => setTimeout(resolve, (retryCount + 1) * 500));
            return switchToChunk(chunkIndex, seekTime, wasPlaying, retryCount + 1);
        }
        
        // All retries failed - log but don't alert (user can click again)
        console.error(`[CHUNK] All retries failed for chunk ${chunkIndex}`);
    } finally {
        state.playback.isTransitioning = false;
    }
}

async function handleChunkTransition() {
    const audioPlayer = DOM.player;
    const localTime = audioPlayer.currentTime;
    const duration = audioPlayer.duration;

    if (duration && localTime >= duration - 0.2 && !state.playback.isTransitioning) {
        const nextChunkIndex = state.audiobook.currentChunk + 1;

        if (nextChunkIndex < state.audiobook.chunks.length) {
            console.log(`[CHUNK] Near end of chunk ${state.audiobook.currentChunk}, transitioning to ${nextChunkIndex}, wasPlaying: ${state.playback.isPlaying}`);
            await switchToChunk(nextChunkIndex, 0, state.playback.isPlaying);
        } else {
            console.log('[CHUNK] Reached end of last chunk');
        }
    }
}

// ========== LRC MANAGEMENT ==========
async function loadLRC(audiobookId) {
    try {
        const response = await fetch(`/api/audiobooks/${audiobookId}/lrc?t=${Date.now()}`, {
            cache: 'no-store',
            headers: {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache'
            }
        });
        const lrcText = await response.text();

        state.lrc.fullData = parseLRC(lrcText);
        console.log(`[LRC] Loaded ${state.lrc.fullData.length} total lines`);
        loadLRCWindow(0);
    } catch (error) {
        console.error('Error loading LRC:', error);
        state.lrc.fullData = [];
        state.lrc.data = [];
        DOM.lrcDisplay.innerHTML = '<div class="lrc-line">No lyrics available</div>';
    }
}

function parseLRC(lrcText) {
    const lines = lrcText.split('\n');
    const parsed = [];

    for (const line of lines) {
        const match = line.match(/\[(\d+):(\d+\.\d+)\](.*)/);
        if (match) {
            const minutes = parseInt(match[1]);
            const seconds = parseFloat(match[2]);
            const text = match[3];
            const timestamp = minutes * 60 + seconds;
            parsed.push({ timestamp, text });
        }
    }

    return parsed;
}

function loadLRCWindow(globalTime) {
    let currentIndex = 0;
    for (let i = 0; i < state.lrc.fullData.length; i++) {
        if (state.lrc.fullData[i].timestamp <= globalTime) {
            currentIndex = i;
        } else {
            break;
        }
    }

    const start = Math.max(0, currentIndex - LRC_BUFFER_RADIUS);
    const end = Math.min(state.lrc.fullData.length, currentIndex + LRC_BUFFER_RADIUS);

    const container = DOM.lrcDisplay;

    // Ensure scroll listener is attached (needed for initial load)
    container.removeEventListener('scroll', handleUserScroll);
    container.addEventListener('scroll', handleUserScroll);

    let scrollAnchorIndex = null;
    let scrollAnchorOffset = 0;

    // Find scroll anchor before modifying DOM
    const visibleLines = Array.from(container.querySelectorAll('.lrc-line')).filter(el => {
        const rect = el.getBoundingClientRect();
        const containerRect = container.getBoundingClientRect();
        return rect.top < containerRect.bottom && rect.bottom > containerRect.top;
    });

    if (visibleLines.length > 0) {
        const topVisible = visibleLines[0];
        scrollAnchorIndex = parseInt(topVisible.dataset.index);
        scrollAnchorOffset = topVisible.getBoundingClientRect().top - container.getBoundingClientRect().top;
    }

    // Get existing lines
    const existingLines = new Set(
        Array.from(container.querySelectorAll('.lrc-line')).map(el => parseInt(el.dataset.index))
    );

    // Remove lines outside the cleanup range
    const cleanupThreshold = LRC_BUFFER_RADIUS * LRC_CLEANUP_MULT;
    Array.from(container.querySelectorAll('.lrc-line')).forEach(line => {
        const idx = parseInt(line.dataset.index);
        if (Math.abs(idx - currentIndex) > cleanupThreshold) {
            line.remove();
            existingLines.delete(idx);
        }
    });

    // Add new lines incrementally
    for (let i = start; i < end; i++) {
        if (!existingLines.has(i)) {
            const lineData = state.lrc.fullData[i];
            const lineElement = createLRCLine(lineData, i);

            // Find correct position to insert
            const allLines = Array.from(container.querySelectorAll('.lrc-line'));
            let inserted = false;

            for (const existing of allLines) {
                const existingIndex = parseInt(existing.dataset.index);
                if (i < existingIndex) {
                    existing.before(lineElement);
                    inserted = true;
                    break;
                }
            }

            if (!inserted) {
                container.appendChild(lineElement);
            }
        }
    }

    state.lrc.loadedRange = { start, end };
    console.log(`[LRC] Updated loaded range: ${start}-${end} (currentIndex: ${currentIndex})`);

    // Restore scroll position
    if (scrollAnchorIndex !== null) {
        const anchorElement = document.querySelector(`.lrc-line[data-index="${scrollAnchorIndex}"]`);
        if (anchorElement) {
            const currentOffset = anchorElement.getBoundingClientRect().top - container.getBoundingClientRect().top;
            const scrollAdjustment = currentOffset - scrollAnchorOffset;
            if (Math.abs(scrollAdjustment) > 1) {
                container.scrollTop += scrollAdjustment;
            }
        }
    }
}

function createLRCLine(lineData, index) {
    const div = document.createElement('div');
    div.className = 'lrc-line';
    div.dataset.index = index;
    div.dataset.timestamp = lineData.timestamp;
    div.style.userSelect = 'none';
    div.style.webkitUserSelect = 'none';

    // Check if this line should have images
    if (state.images.enabled && state.images.chunkImages.length > 0) {
        // Find which text chunk this LRC line belongs to
        const textChunkIndex = findTextChunkForLrcIndex(index);
        if (textChunkIndex !== -1) {
            const chunkImageData = state.images.chunkImages[textChunkIndex];
            // Only show images at the start of each text chunk
            if (chunkImageData && chunkImageData.images && chunkImageData.images.length > 0 && isFirstLrcLineInChunk(index, textChunkIndex)) {
                const imagesContainer = document.createElement('div');
                imagesContainer.className = 'lrc-images';
                
                for (const imageId of chunkImageData.images) {
                    if (state.images.data[imageId]) {
                        const img = createImageElement(imageId, state.images.data[imageId]);
                        imagesContainer.appendChild(img);
                    }
                }
                
                if (imagesContainer.children.length > 0) {
                    div.appendChild(imagesContainer);
                }
            }
        }
    }

    // Add text
    const textSpan = document.createElement('span');
    textSpan.textContent = lineData.text;
    div.appendChild(textSpan);

    if (state.bookmarks.includes(index)) {
        div.classList.add('bookmarked');
    }

    div.onclick = (e) => {
        // Don't seek if clicking on an image
        if (e.target.classList.contains('lrc-image')) return;
        // Don't seek if long press was just triggered
        if (state.touch.longPressTriggered) {
            console.log('[DEBUG] Ignoring click after long press');
            state.touch.longPressTriggered = false;
            return;
        }
        console.log(`[DEBUG] Lyric clicked: index=${index}, timestamp=${lineData.timestamp}`);
        seekToTimestamp(lineData.timestamp);
    };

    div.addEventListener('touchstart', handleTouchStart);
    div.addEventListener('touchmove', handleTouchMove);
    div.addEventListener('touchend', handleTouchEnd);

    return div;
}

// Helper to find which text chunk an LRC line belongs to
function findTextChunkForLrcIndex(lrcIndex) {
    // LRC lines typically map 1:1 with text chunks in this system
    // But we may have multiple LRC lines per chunk depending on chunk size
    if (state.images.chunkImages.length === 0) return -1;
    
    // Simple mapping: each text chunk corresponds to multiple LRC lines
    // Calculate based on total LRC lines / total chunks
    const totalLrcLines = state.lrc.fullData.length;
    const totalChunks = state.images.chunkImages.length;
    
    if (totalChunks === 0 || totalLrcLines === 0) return -1;
    
    const linesPerChunk = Math.ceil(totalLrcLines / totalChunks);
    return Math.min(Math.floor(lrcIndex / linesPerChunk), totalChunks - 1);
}

// Check if this LRC line is the first one in its text chunk
function isFirstLrcLineInChunk(lrcIndex, textChunkIndex) {
    const totalLrcLines = state.lrc.fullData.length;
    const totalChunks = state.images.chunkImages.length;
    
    if (totalChunks === 0 || totalLrcLines === 0) return false;
    
    const linesPerChunk = Math.ceil(totalLrcLines / totalChunks);
    const expectedFirstLine = textChunkIndex * linesPerChunk;
    
    return lrcIndex === expectedFirstLine;
}

function createImageElement(imageId, dataUrl) {
    const img = document.createElement('img');
    img.src = dataUrl;
    img.className = 'lrc-image';
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

async function loadAudiobookImages(audiobookId) {
    try {
        console.log('[IMAGES] Loading images for audiobook:', audiobookId);
        
        // First, get image metadata (which chunks have images)
        const metadata = await apiCall(`/audiobooks/${audiobookId}/images`);
        
        if (!metadata.has_images) {
            console.log('[IMAGES] No images in this audiobook');
            state.images.chunkImages = [];
            return;
        }
        
        state.images.chunkImages = metadata.chunks;
        
        // Collect all unique image IDs
        const imageIds = new Set();
        for (const chunk of metadata.chunks) {
            if (chunk.images) {
                chunk.images.forEach(id => imageIds.add(id));
            }
        }
        
        console.log(`[IMAGES] Found ${imageIds.size} unique images across ${metadata.chunks.length} chunks`);
        
        // Load each image
        for (const imageId of imageIds) {
            try {
                const imageData = await apiCall(`/audiobooks/${audiobookId}/image/${imageId}`);
                if (imageData && imageData.data) {
                    state.images.data[imageId] = imageData.data;
                }
            } catch (err) {
                console.warn(`[IMAGES] Failed to load image ${imageId}:`, err);
            }
        }
        
        state.images.loaded = true;
        console.log('[IMAGES] All images loaded');
        
        // Reload LRC to display images
        if (state.lrc.fullData.length > 0) {
            const currentTime = getGlobalTime(state.audiobook.currentChunk, DOM.player.currentTime);
            loadLRCWindow(currentTime);
        }
    } catch (error) {
        console.error('[IMAGES] Failed to load images:', error);
        state.images.chunkImages = [];
    }
}

function displayLRC(indexOffset = 0) {
    // This function is now only used for initial load
    const container = DOM.lrcDisplay;
    container.innerHTML = '';

    container.removeEventListener('scroll', handleUserScroll);
    container.addEventListener('scroll', handleUserScroll);
    container.style.userSelect = 'none';
    container.style.webkitUserSelect = 'none';

    for (let i = 0; i < state.lrc.data.length; i++) {
        const line = state.lrc.data[i];
        const actualIndex = indexOffset + i;
        container.appendChild(createLRCLine(line, actualIndex));
    }
}

function handleUserScroll() {
    // Don't process if we just initiated an auto-scroll
    // (scrollIntoView triggers scroll events that shouldn't be treated as user interaction)
    if (state.scroll.auto) return;
    
    state.scroll.userScrolling = true;
    state.scroll.auto = false;

    if (state.scroll.timeout) clearTimeout(state.scroll.timeout);

    state.scroll.timeout = setTimeout(() => {
        state.scroll.userScrolling = false;

        const container = DOM.lrcDisplay;
        const scrollTop = container.scrollTop;
        const scrollHeight = container.scrollHeight;
        const clientHeight = container.clientHeight;
        const distanceFromTop = scrollTop;
        const distanceFromBottom = scrollHeight - scrollTop - clientHeight;

        // Get currently loaded lines in DOM
        const loadedLines = Array.from(container.querySelectorAll('.lrc-line'))
            .map(el => parseInt(el.dataset.index))
            .sort((a, b) => a - b);

        if (loadedLines.length === 0) {
            checkAutoSnapBack();
            return;
        }

        const [firstLoaded, lastLoaded] = [loadedLines[0], loadedLines[loadedLines.length - 1]];
        const threshold = clientHeight * 2;

        let shouldLoad = false;
        let targetTimestamp = 0;

        if (distanceFromTop < threshold && firstLoaded > 0) {
            // Near top - load earlier lines
            const targetIndex = Math.max(0, firstLoaded - 100);
            targetTimestamp = state.lrc.fullData[targetIndex]?.timestamp || 0;
            shouldLoad = true;
            console.log(`[LRC] Near top (${distanceFromTop}px), loading from index ${targetIndex}`);
        } else if (distanceFromBottom < threshold && lastLoaded < state.lrc.fullData.length - 1) {
            // Near bottom - load later lines  
            const targetIndex = Math.min(state.lrc.fullData.length - 1, lastLoaded + 100);
            targetTimestamp = state.lrc.fullData[targetIndex]?.timestamp || 0;
            shouldLoad = true;
            console.log(`[LRC] Near bottom (${distanceFromBottom}px), loading from index ${targetIndex}`);
        }

        if (shouldLoad) {
            loadLRCWindow(targetTimestamp);
        } else {
            checkAutoSnapBack();
        }
    }, 150);
}

function isElementInViewport(el) {
    const rect = el.getBoundingClientRect();
    const container = DOM.lrcDisplay;
    const containerRect = container.getBoundingClientRect();
    return rect.top >= containerRect.top && rect.bottom <= containerRect.bottom;
}

function checkAutoSnapBack() {
    if (!state.scroll.userScrolling && !state.scroll.auto) {
        const activeLine = document.querySelector('.lrc-line.active');
        if (activeLine && isElementInViewport(activeLine)) {
            state.scroll.auto = true;
        }
    }
}

function scrollToCurrentLine() {
    state.scroll.auto = true;
    state.scroll.userScrolling = false;

    const audioPlayer = DOM.player;
    const currentTime = getGlobalTime(state.audiobook.currentChunk, audioPlayer.currentTime);
    let currentIndex = 0;
    for (let i = 0; i < state.lrc.fullData.length; i++) {
        if (state.lrc.fullData[i].timestamp <= currentTime) {
            currentIndex = i;
        } else {
            break;
        }
    }

    if (currentIndex < state.lrc.loadedRange.start || currentIndex >= state.lrc.loadedRange.end) {
        loadLRCWindow(currentTime);
    }

    setTimeout(() => {
        const activeLine = document.querySelector('.lrc-line.active');
        if (activeLine) {
            console.log('[SCROLL] Found active line, scrolling to index:', activeLine.dataset.index);
            state.scroll.auto = true; // Ensure auto flag is set right before scroll
            activeLine.scrollIntoView({ behavior: 'smooth', block: 'center' });
            // Keep auto flag for a bit longer after scroll to prevent timer resets
            setTimeout(() => {
                state.scroll.auto = false;
            }, 500);
        } else {
            console.warn('[SCROLL] No active line found after loading window');
            updateLRCHighlight();
            setTimeout(() => {
                const retryActiveLine = document.querySelector('.lrc-line.active');
                if (retryActiveLine) {
                    console.log('[SCROLL] Found active line on retry, scrolling');
                    state.scroll.auto = true; // Ensure auto flag is set right before scroll
                    retryActiveLine.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    // Keep auto flag for a bit longer after scroll to prevent timer resets
                    setTimeout(() => {
                        state.scroll.auto = false;
                    }, 500);
                }
            }, 100);
        }
    }, 150);
}

function updateLRCHighlight() {
    const audioPlayer = DOM.player;

    // Calculate global time
    const currentTime = getGlobalTime(state.audiobook.currentChunk, audioPlayer.currentTime);

    // Always update progress bar first
    if (state.audiobook.totalDuration) {
        updateProgressDisplay(currentTime);
    }

    // If no LRC data loaded yet, skip highlighting
    if (state.lrc.fullData.length === 0) {
        return;
    }

    // Check if user is seeking with progress bar
    const progressBar = DOM.progressBar;
    const isSeeking = progressBar && progressBar.dataset.seeking === 'true';

    // Find the current line in the FULL data
    let currentIndexInFull = -1;
    for (let i = 0; i < state.lrc.fullData.length; i++) {
        if (state.lrc.fullData[i].timestamp <= currentTime) {
            currentIndexInFull = i;
        } else {
            break;
        }
    }

    // Edge detection for window reload, matching stream.js
    // Only reload if within threshold AND window range will actually change
    if (!isSeeking) {
        const EDGE_THRESHOLD = 100;
        const distanceFromStart = currentIndexInFull - state.lrc.loadedRange.start;
        const distanceFromEnd = state.lrc.loadedRange.end - currentIndexInFull;
        const newStart = Math.max(0, currentIndexInFull - LRC_WINDOW_SIZE);
        const newEnd = Math.min(state.lrc.fullData.length, currentIndexInFull + LRC_WINDOW_SIZE);

        console.log(`[LRC EDGE] current=${currentIndexInFull}, range=${state.lrc.loadedRange.start}-${state.lrc.loadedRange.end}, distStart=${distanceFromStart}, distEnd=${distanceFromEnd}`);

        // Only reload if near edge AND window range will change
        if (((distanceFromStart < EDGE_THRESHOLD && state.lrc.loadedRange.start > 0) ||
            (distanceFromEnd < EDGE_THRESHOLD && state.lrc.loadedRange.end < state.lrc.fullData.length)) &&
            (state.lrc.loadedRange.start !== newStart || state.lrc.loadedRange.end !== newEnd)) {
            console.log(`[LRC] Near edge (${distanceFromStart} from start, ${distanceFromEnd} from end), loading new window`);
            loadLRCWindow(currentTime);
        }
    }

    // Update highlights - match by the data-index which corresponds to fullLRCData index
    const lines = document.querySelectorAll('.lrc-line');
    if (lines.length === 0) {
        return; // No lines loaded yet
    }

    let activeLineElement = null;
    const highlightChanged = currentIndexInFull !== state.lrc.lastHighlightedIndex;

    // Before updating highlights, check if the PREVIOUS highlighted line was visible
    let previousLineWasVisible = false;
    if (highlightChanged && state.lrc.lastHighlightedIndex >= 0) {
        const previousLine = document.querySelector(`.lrc-line[data-index="${state.lrc.lastHighlightedIndex}"]`);
        if (previousLine) {
            previousLineWasVisible = isElementPartiallyVisible(previousLine);
        }
    }

    lines.forEach((line) => {
        const lineIndex = parseInt(line.dataset.index);

        if (lineIndex === currentIndexInFull) {
            line.classList.add('active');
            activeLineElement = line;
        } else {
            line.classList.remove('active');
        }

        if (lineIndex < currentIndexInFull) {
            line.classList.add('past');
        } else {
            line.classList.remove('past');
        }
    });

    // Auto-scroll logic: scroll if the PREVIOUS line was at least partially visible
    if (highlightChanged && activeLineElement && state.scroll.auto && !state.scroll.userScrolling) {
        if (previousLineWasVisible) {
            // Previous line was visible, smoothly scroll to new line
            setTimeout(() => {
                const currentActiveLine = document.querySelector('.lrc-line.active');
                if (currentActiveLine) {
                    currentActiveLine.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }, 50);
        }
        // If previous line was not visible, user has scrolled away, don't interrupt them
    }

    state.lrc.lastHighlightedIndex = currentIndexInFull;
}

// ========== PROGRESS DISPLAY ==========
function updateProgressDisplay(currentTime) {
    state.progress.mode === 'chapter' ? updateProgressChapterMode(currentTime) : updateProgressBookMode(currentTime);
}

function updateProgressBookMode(currentTime) {
    const remaining = state.audiobook.totalDuration - currentTime;

    if (state.progress.timeMode === 'remaining') {
        DOM.currentTime.textContent = formatTime(currentTime);
        DOM.totalTime.textContent = `-${formatTime(remaining)} left`;
    } else {
        DOM.currentTime.textContent = formatTime(currentTime);
        DOM.totalTime.textContent = formatTime(state.audiobook.totalDuration);
    }

    if (DOM.progressBar && DOM.progressBar.dataset.seeking !== 'true' && state.progress.pendingSeekValue === null) {
        DOM.progressBar.value = (currentTime / state.audiobook.totalDuration) * 1000;
    }
}

async function updateProgressChapterMode(currentTime) {
    if (!state.audiobook.id) return;

    try {
        if (!state.cachedChapters) {
            const audiobook = await apiCall(`/audiobooks/${state.audiobook.id}`);
            state.cachedChapters = audiobook.chapters || [];
        }

        const chapters = state.cachedChapters;
        let currentChapter = null;

        for (const chapter of chapters) {
            const nextChapter = chapters[chapters.indexOf(chapter) + 1];
            const chapterEnd = nextChapter ? nextChapter.timestamp : state.audiobook.totalDuration;

            if (currentTime >= chapter.timestamp && currentTime < chapterEnd) {
                currentChapter = {
                    ...chapter,
                    startTime: chapter.timestamp,
                    endTime: chapterEnd,
                    duration: chapterEnd - chapter.timestamp
                };
                break;
            }
        }

        if (currentChapter) {
            const chapterElapsed = currentTime - currentChapter.startTime;
            const chapterRemaining = currentChapter.endTime - currentTime;

            if (state.progress.timeMode === 'remaining') {
                DOM.currentTime.textContent = formatTime(chapterElapsed);
                DOM.totalTime.textContent = `-${formatTime(chapterRemaining)} left (Ch)`;
            } else {
                DOM.currentTime.textContent = formatTime(chapterElapsed);
                DOM.totalTime.textContent = formatTime(currentChapter.duration) + ' (Ch)';
            }

            if (DOM.progressBar && DOM.progressBar.dataset.seeking !== 'true' && state.progress.pendingSeekValue === null) {
                const chapterProgress = (chapterElapsed / currentChapter.duration) * 1000;
                DOM.progressBar.value = Math.max(0, Math.min(1000, chapterProgress));
            }
        }
    } catch (error) {
        console.error('Error updating chapter progress:', error);
        updateProgressBookMode(currentTime);
    }
}

// ========== PLAYBACK CONTROLS ==========
async function playAudiobook(audiobookId) {
    try {
        const audiobook = await apiCall(`/audiobooks/${audiobookId}`);

        if (audiobook.status !== 'completed' && audiobook.status !== 'in_progress') {
            alert('Audiobook is not ready yet!');
            return;
        }

        trackAudiobookPlayed(audiobookId).catch(err => {
            console.warn('[TRACKING] Skipping tracking:', err.message);
        });

        state.audiobook.id = audiobookId;
        const audioPlayer = DOM.player;

        await loadAudioChunksMetadata(audiobookId);

        if (state.audiobook.chunks.length === 0) {
            alert('No audio chunks found! This audiobook may be from an old version.');
            return;
        }

        console.log(`[PLAYER] Using chunked audio system with ${state.audiobook.chunks.length} chunks`);

        let startChunkIndex = 0;
        let startLocalTime = 0;

        if (audiobook.last_position > 0) {
            const chunkInfo = getChunkForGlobalTime(audiobook.last_position);
            startChunkIndex = chunkInfo.chunkIndex;
            startLocalTime = chunkInfo.localTime;
            console.log(`[PLAYER] Restoring to chunk ${startChunkIndex} at ${startLocalTime}s (global: ${audiobook.last_position}s)`);
        }

        await switchToChunk(startChunkIndex, startLocalTime);

        audioPlayer.removeEventListener('timeupdate', handleChunkTransition);
        audioPlayer.addEventListener('timeupdate', handleChunkTransition);

        audioPlayer.addEventListener('play', () => {
            state.playback.isPlaying = true;
            updatePlayPauseButton();
        });
        audioPlayer.addEventListener('pause', () => {
            state.playback.isPlaying = false;
            updatePlayPauseButton();
        });

        if (audiobook.status === 'in_progress') {
            if (state.intervals.chunkUpdate) clearInterval(state.intervals.chunkUpdate);
            state.intervals.chunkUpdate = setInterval(async () => {
                const oldChunkCount = state.audiobook.chunks.length;
                await loadAudioChunksMetadata(audiobookId);

                if (state.audiobook.chunks.length > oldChunkCount) {
                    console.log(`[CHUNKS] New chunks available: ${state.audiobook.chunks.length} (was ${oldChunkCount})`);
                    const currentTime = getGlobalTime(state.audiobook.currentChunk, audioPlayer.currentTime);

                    let currentIndex = -1;
                    for (let i = 0; i < state.lrc.fullData.length; i++) {
                        if (state.lrc.fullData[i].timestamp <= currentTime) {
                            currentIndex = i;
                        } else {
                            break;
                        }
                    }

                    // Check if user is near end of playback OR has scrolled near the end
                    const isPlayingNearEnd = currentIndex >= state.lrc.fullData.length - 5;

                    // Check if user has scrolled near the end
                    const container = DOM.lrcDisplay;
                    const scrollHeight = container.scrollHeight;
                    const scrollTop = container.scrollTop;
                    const clientHeight = container.clientHeight;
                    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
                    const isScrolledNearEnd = distanceFromBottom < clientHeight * 0.5; // Within half a screen of bottom

                    if (isPlayingNearEnd || isScrolledNearEnd) {
                        console.log(`[LRC] Reloading LRC - Playing near end: ${isPlayingNearEnd}, Scrolled near end: ${isScrolledNearEnd}`);
                        await loadLRC(audiobookId);
                    } else {
                        console.log(`[LRC] At line ${currentIndex}/${state.lrc.fullData.length}, not near end, skipping reload`);
                    }
                }
            }, 5000);
        }

        await loadLRC(audiobookId);
        await loadBookmarks(audiobookId);
        
        // Load images if enabled
        if (state.images.enabled) {
            await loadAudiobookImages(audiobookId);
        }

        DOM.playerSection.classList.remove('hidden');
        DOM.playerTitle.textContent = audiobook.title;
        
        // Initialize sleep timer now that player is visible and active
        initSleepTimer();

        audioPlayer.removeEventListener('timeupdate', updateLRCHighlight);
        audioPlayer.addEventListener('timeupdate', updateLRCHighlight);

        if (state.intervals.positionSave) clearInterval(state.intervals.positionSave);

        state.intervals.positionSave = setInterval(() => {
            if (state.audiobook.id && !audioPlayer.paused) {
                const globalTime = getGlobalTime(state.audiobook.currentChunk, audioPlayer.currentTime);
                savePosition(globalTime);
            }
        }, 3000);

        audioPlayer.removeEventListener('pause', handlePause);
        audioPlayer.addEventListener('pause', handlePause);

        updateLRCHighlight();
        setTimeout(() => scrollToCurrentLine(), 150);
    } catch (error) {
        console.error('Error playing audiobook:', error);
    }
}

function handlePause() {
    if (state.audiobook.id) {
        const globalTime = getGlobalTime(state.audiobook.currentChunk, DOM.player.currentTime);
        savePosition(globalTime);
    }
}

function togglePlayPause() {
    const audioPlayer = DOM.player;
    if (audioPlayer.paused) {
        audioPlayer.play();
    } else {
        audioPlayer.pause();
    }
}

function changeVolume(value) {
    DOM.player.volume = value / 100;
}

function updatePlayPauseButton() {
    if (DOM.playPauseBtn) {
        DOM.playPauseBtn.textContent = state.playback.isPlaying ? '⏸' : '▶';
    }
}

async function seekToTimestamp(timestamp) {
    console.log(`[SEEK] Seeking to timestamp ${timestamp}s`);

    state.scroll.suppressAuto = true;
    const chunkInfo = getChunkForGlobalTime(timestamp);

    const audioPlayer = DOM.player;
    const wasPlaying = !audioPlayer.paused;

    if (chunkInfo.chunkIndex !== state.audiobook.currentChunk) {
        console.log(`[SEEK] Switching to chunk ${chunkInfo.chunkIndex}, local time ${chunkInfo.localTime}s`);
        await switchToChunk(chunkInfo.chunkIndex, chunkInfo.localTime, wasPlaying);
    } else {
        console.log(`[SEEK] Same chunk, seeking to ${chunkInfo.localTime}s`);
        audioPlayer.currentTime = chunkInfo.localTime;
    }

    loadLRCWindow(timestamp);
    updateLRCHighlight();

    setTimeout(() => {
        scrollToCurrentLine();
        setTimeout(() => {
            state.scroll.suppressAuto = false;
        }, 500);
    }, 150);
}

function seekToGlobalProgress(value) {
    // Store the pending seek value - actual seek happens on finishProgressSeek
    state.progress.pendingSeekValue = value;
    
    // Update the time display immediately for visual feedback
    const targetTime = (value / 1000) * state.audiobook.totalDuration;
    if (DOM.currentTime && state.audiobook.totalDuration) {
        DOM.currentTime.textContent = formatTime(targetTime);
        if (state.progress.timeMode === 'remaining') {
            const remaining = state.audiobook.totalDuration - targetTime;
            DOM.totalTime.textContent = `-${formatTime(remaining)} left`;
        }
    }
}

// Called when user finishes dragging the progress bar
function finishProgressSeek() {
    if (state.progress.pendingSeekValue !== null) {
        const targetTime = (state.progress.pendingSeekValue / 1000) * state.audiobook.totalDuration;
        state.progress.pendingSeekValue = null;
        seekToTimestamp(targetTime);
    }
}

async function seekToChapterProgress(value) {
    if (!state.audiobook.id) return;

    try {
        if (!state.cachedChapters) {
            const audiobook = await apiCall(`/audiobooks/${state.audiobook.id}`);
            state.cachedChapters = audiobook.chapters || [];
        }

        const chapters = state.cachedChapters;
        const currentTime = getGlobalTime(state.audiobook.currentChunk, DOM.player.currentTime);

        let currentChapter = null;
        for (const chapter of chapters) {
            const nextChapter = chapters[chapters.indexOf(chapter) + 1];
            const chapterEnd = nextChapter ? nextChapter.timestamp : state.audiobook.totalDuration;

            if (currentTime >= chapter.timestamp && currentTime < chapterEnd) {
                currentChapter = {
                    ...chapter,
                    startTime: chapter.timestamp,
                    endTime: chapterEnd,
                    duration: chapterEnd - chapter.timestamp
                };
                break;
            }
        }

        if (currentChapter) {
            const targetTime = currentChapter.startTime + (value / 1000) * currentChapter.duration;
            seekToTimestamp(targetTime);
        }
    } catch (error) {
        console.error('Error seeking chapter progress:', error);
    }
}

function closePlayer() {
    if (state.intervals.positionSave) {
        clearInterval(state.intervals.positionSave);
        state.intervals.positionSave = null;
    }

    if (state.intervals.chunkUpdate) {
        clearInterval(state.intervals.chunkUpdate);
        state.intervals.chunkUpdate = null;
    }

    const audioPlayer = DOM.player;
    if (state.audiobook.id && !audioPlayer.paused) {
        const globalTime = getGlobalTime(state.audiobook.currentChunk, audioPlayer.currentTime);
        savePosition(globalTime);
    }

    audioPlayer.pause();
    audioPlayer.src = '';

    if (state.playback.currentBlobUrl) {
        URL.revokeObjectURL(state.playback.currentBlobUrl);
        state.playback.currentBlobUrl = null;
    }

    state.cache.chunks.clear();
    state.audiobook.id = null;
    state.audiobook.chunks = [];
    state.audiobook.currentChunk = -1;
    state.lrc.data = [];
    state.lrc.fullData = [];
    state.bookmarks = [];

    DOM.playerSection.classList.add('hidden');
    DOM.lrcDisplay.innerHTML = '';

    state.cachedChapters = null;
    
    // Clean up sleep timer
    cleanupSleepTimer();

    // Refresh the audiobooks list to update the "recently played" sort order
    if (typeof refreshAudiobooks === 'function') {
        refreshAudiobooks();
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
    
    // Reset listeners flag so they can be re-setup when player opens again
    state.sleepTimer.listenersSetup = false;
}

function savePosition(position) {
    if (!state.audiobook.id) return;
    apiCall(`/audiobooks/${state.audiobook.id}/position`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ position: position })
    }).catch(error => console.error('Error saving position:', error));
}

// ========== BOOKMARKS ==========
async function loadBookmarks(audiobookId) {
    try {
        const data = await apiCall(`/audiobooks/${audiobookId}/bookmarks`);
        state.bookmarks = data.bookmarks || [];
    } catch (error) {
        console.error('Error loading bookmarks:', error);
        state.bookmarks = [];
    }
}

async function toggleBookmark(lineIndex) {
    if (!state.audiobook.id) return;

    try {
        const response = await apiCall(`/audiobooks/${state.audiobook.id}/bookmark`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chunk_index: lineIndex })
        });

        state.bookmarks = response.bookmarks || [];

        document.querySelectorAll('.lrc-line').forEach(line => {
            const idx = parseInt(line.dataset.index);
            if (state.bookmarks.includes(idx)) {
                line.classList.add('bookmarked');
            } else {
                line.classList.remove('bookmarked');
            }
        });

        showToast(state.bookmarks.includes(lineIndex) ? 'Bookmark added' : 'Bookmark removed');
    } catch (error) {
        console.error('Error toggling bookmark:', error);
        showToast('Failed to update bookmark');
    }
}

// ========== TOUCH HANDLING ==========
function handleTouchStart(e) {
    const touch = e.touches[0];
    state.touch.startX = touch.clientX;
    state.touch.startY = touch.clientY;
    state.touch.startTime = Date.now();
    state.touch.longPressTarget = e.currentTarget; // Store the element reference
    state.touch.longPressTriggered = false;
    
    console.log('[TOUCH] Touch start, setting up long press timer');
    
    // Start long press timer (500ms)
    clearTimeout(state.touch.longPressTimer);
    state.touch.longPressTimer = setTimeout(() => {
        console.log('[TOUCH] Long press timer triggered!');
        state.touch.longPressTriggered = true;
        navigator.vibrate?.(50);
        // Use the stored target instead of e.currentTarget
        showLyricContextMenu(state.touch.longPressTarget);
    }, 500);
}

function handleTouchMove(e) {
    const touch = e.touches[0];
    const deltaX = touch.clientX - state.touch.startX;
    const deltaY = Math.abs(touch.clientY - state.touch.startY);

    // Cancel long press if user moves too much
    if (Math.abs(deltaX) > 10 || deltaY > 10) {
        clearTimeout(state.touch.longPressTimer);
    }

    if (Math.abs(deltaX) > deltaY && Math.abs(deltaX) > 10) {
        e.preventDefault();
    }
}

function handleTouchEnd(e) {
    clearTimeout(state.touch.longPressTimer);
    
    // If long press was triggered, prevent the click and swipe actions
    if (state.touch.longPressTriggered) {
        console.log('[TOUCH] Long press ended, preventing other actions');
        e.preventDefault();
        e.stopPropagation();
        // Keep the flag true momentarily so onclick can check it
        setTimeout(() => {
            state.touch.longPressTriggered = false;
        }, 100);
        return;
    }
    
    const deltaX = e.changedTouches[0].clientX - state.touch.startX;
    const deltaY = Math.abs(e.changedTouches[0].clientY - state.touch.startY);
    const deltaTime = Date.now() - state.touch.startTime;

    console.log('[TOUCH] deltaX:', deltaX, 'deltaY:', deltaY, 'deltaTime:', deltaTime);

    if (deltaX < -30 && deltaY < 50 && deltaTime < 500) {
        const lineIndex = parseInt(e.currentTarget.dataset.index);
        console.log('[TOUCH] Swipe left detected! Toggling bookmark for line:', lineIndex);
        navigator.vibrate?.(50);
        toggleBookmark(lineIndex);
        e.preventDefault();
    }
}

function showLyricContextMenu(element) {
    console.log('[CONTEXT MENU] Showing context menu for element:', element);
    
    if (!element) {
        console.error('[CONTEXT MENU] Invalid element: null');
        return;
    }
    
    const lineIndex = parseInt(element.dataset.index);
    const timestamp = parseFloat(element.dataset.timestamp);
    
    console.log('[CONTEXT MENU] Line index:', lineIndex, 'Timestamp:', timestamp);
    
    // Find which chunk this timestamp belongs to
    const chunkInfo = getChunkForGlobalTime(timestamp);
    
    console.log('[CONTEXT MENU] Chunk info:', chunkInfo);
    
    // Create/show modal
    let modal = document.getElementById('lyricContextMenu');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'lyricContextMenu';
        modal.className = 'modal';
        document.body.appendChild(modal);
        console.log('[CONTEXT MENU] Created new modal');
    }
    
    modal.style.display = 'flex';
    modal.classList.add('active');
    modal.innerHTML = `
        <div class="modal-content" style="max-width: 400px;">
            <h3>Line Options</h3>
            <p style="color: var(--text-secondary); margin: 10px 0;">
                Line ${lineIndex + 1} at ${formatTime(timestamp)}<br>
                Audio Chunk: ${chunkInfo.chunkIndex + 1}
            </p>
            <div style="display: flex; flex-direction: column; gap: 10px;">
                <button class="btn" onclick="regenerateChunkAtLine(${lineIndex}, ${timestamp}, ${chunkInfo.chunkIndex})">🔄 Regenerate This Audio Chunk</button>
                <button class="btn" onclick="seekToTimestamp(${timestamp}); closeLyricContextMenu()">▶️ Play From Here</button>
                <button class="btn" onclick="toggleBookmark(${lineIndex}); closeLyricContextMenu()">${state.bookmarks.includes(lineIndex) ? '🔖 Remove Bookmark' : '📌 Add Bookmark'}</button>
                <button class="btn btn-danger" onclick="closeLyricContextMenu()">✕ Cancel</button>
            </div>
        </div>
    `;
    
    // Close on background click
    modal.onclick = (e) => {
        if (e.target === modal) closeLyricContextMenu();
    };
}

function closeLyricContextMenu() {
    const modal = document.getElementById('lyricContextMenu');
    if (modal) {
        modal.style.display = 'none';
        modal.classList.remove('active');
    }
}

async function regenerateChunkAtLine(lineIndex, timestamp, chunkIndex) {
    if (!state.audiobook.id) return;
    
    if (!confirm(`Regenerate audio chunk ${chunkIndex + 1}?\n\nThis will delete and recreate the audio for this section. The audiobook will continue playing from the current position after regeneration.`)) {
        closeLyricContextMenu();
        return;
    }
    
    try {
        closeLyricContextMenu();
        showToast(`Regenerating chunk ${chunkIndex + 1}...`);
        
        await apiCall(`/audiobooks/${state.audiobook.id}/regenerate-chunk/${chunkIndex}`, {
            method: 'POST'
        });
        
        showToast(`Chunk ${chunkIndex + 1} is being regenerated. Refresh the page when generation completes.`);
    } catch (error) {
        console.error('Error regenerating chunk:', error);
        showToast('Failed to regenerate chunk');
    }
}

// ========== MODALS ==========
async function showChapters() {
    if (!state.audiobook.id) return;

    try {
        const audiobook = await apiCall(`/audiobooks/${state.audiobook.id}`);

        if (!audiobook.chapters || audiobook.chapters.length === 0) {
            showToast('No chapters found');
            return;
        }

        const chaptersList = document.getElementById('chaptersList');
        chaptersList.innerHTML = '';

        audiobook.chapters.forEach((chapter, i) => {
            const item = document.createElement('div');
            item.className = 'bookmark-item';
            item.style.cssText = `padding:12px;margin:8px 0;background-color:var(--bg-tertiary);
                border-left:4px solid var(--accent);border-radius:5px;cursor:pointer;transition:all 0.3s;`;

            item.innerHTML = `
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div style="flex:1;">
                        <div style="color:var(--accent);font-size:12px;margin-bottom:4px;">
                            📖 Chapter ${i + 1} • ${formatTime(chapter.timestamp)}
                        </div>
                        <div style="color:var(--text-primary);font-weight:500;">
                            ${chapter.name}
                        </div>
                    </div>
                </div>
            `;

            item.onclick = () => {
                seekToTimestamp(chapter.timestamp);
                closeChaptersModal();
                setTimeout(() => scrollToCurrentLine(), 150);
            };

            item.onmouseenter = () => item.style.backgroundColor = 'var(--bg-secondary)';
            item.onmouseleave = () => item.style.backgroundColor = 'var(--bg-tertiary)';

            chaptersList.appendChild(item);
        });

        document.getElementById('chaptersModal').classList.add('active');
    } catch (error) {
        console.error('Error loading chapters:', error);
        showToast('Failed to load chapters');
    }
}

const closeChaptersModal = () => document.getElementById('chaptersModal').classList.remove('active');

function showBookmarks() {
    if (state.bookmarks.length === 0) {
        showToast('No bookmarks yet');
        return;
    }

    const bookmarksList = document.getElementById('bookmarksList');
    bookmarksList.innerHTML = '';

    state.bookmarks.forEach((lineIndex, i) => {
        if (lineIndex >= 0 && lineIndex < state.lrc.fullData.length) {
            const line = state.lrc.fullData[lineIndex];
            const item = document.createElement('div');
            item.className = 'bookmark-item';
            item.style.cssText = `padding:12px;margin:8px 0;background-color:var(--bg-tertiary);
                border-left:4px solid gold;border-radius:5px;cursor:pointer;transition:all 0.3s;`;

            let displayText = line.text;
            if (displayText.length > 100) {
                displayText = displayText.substring(0, 100) + '...';
            }

            item.innerHTML = `
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div style="flex:1;">
                        <div style="color:gold;font-size:12px;margin-bottom:4px;">
                            ★ Bookmark ${i + 1} • ${formatTime(line.timestamp)}
                        </div>
                        <div style="color:var(--text-primary);">
                            ${displayText}
                        </div>
                    </div>
                    <button class="btn-small btn-danger" style="margin-left:10px;" onclick="event.stopPropagation();removeBookmarkFromModal(${lineIndex})">
                        Remove
                    </button>
                </div>
            `;

            item.onclick = () => {
                seekToTimestamp(line.timestamp);
                closeBookmarksModal();
                setTimeout(() => scrollToCurrentLine(), 150);
            };

            item.onmouseenter = () => item.style.backgroundColor = 'var(--bg-secondary)';
            item.onmouseleave = () => item.style.backgroundColor = 'var(--bg-tertiary)';

            bookmarksList.appendChild(item);
        }
    });

    document.getElementById('bookmarksModal').classList.add('active');
}

const closeBookmarksModal = () => document.getElementById('bookmarksModal').classList.remove('active');

async function removeBookmarkFromModal(lineIndex) {
    await toggleBookmark(lineIndex);
    if (state.bookmarks.length === 0) {
        closeBookmarksModal();
        showToast('No bookmarks left');
    } else {
        showBookmarks();
        setTimeout(() => scrollToCurrentLine(), 150);
    }
}

// ========== SETTINGS ==========
const showSettingsModal = () => document.getElementById('settingsModal').classList.add('active');
const closeSettingsModal = () => document.getElementById('settingsModal').classList.remove('active');

function loadFontSettings() {
    apiCall('/audiobooks/preferences/get')
        .then(prefs => {
            const fontSize = prefs.font_size || '16';
            const fontFamily = prefs.font_family || 'system';

            state.progress.mode = prefs.progress_mode || 'book';
            state.progress.timeMode = prefs.time_mode || 'total';

            const showTitle = prefs.show_title !== undefined ? prefs.show_title : true;
            const showProgressBar = prefs.show_progress_bar !== undefined ? prefs.show_progress_bar : true;
            const showAudioBar = prefs.show_audio_bar !== undefined ? prefs.show_audio_bar : true;
            const showImages = prefs.show_images === true;
            
            state.images.enabled = showImages;

            document.getElementById('fontSizeSelect').value = fontSize;
            document.getElementById('fontFamilySelect').value = fontFamily;

            const progressModeSelect = document.getElementById('progressModeSelect');
            const timeModeSelect = document.getElementById('timeModeSelect');

            if (progressModeSelect) progressModeSelect.value = state.progress.mode;
            if (timeModeSelect) timeModeSelect.value = state.progress.timeMode;

            applyVisibilitySettings(showTitle, showProgressBar, showAudioBar);

            setTimeout(() => {
                const showTitleToggle = document.getElementById('showTitleToggle');
                const showProgressBarToggle = document.getElementById('showProgressBarToggle');
                const showAudioBarToggle = document.getElementById('showAudioBarToggle');
                const showImagesToggle = document.getElementById('showImagesToggle');
                const sleepTimerInput = document.getElementById('sleepTimerInput');
                const showSleepTimerToggle = document.getElementById('showSleepTimerToggle');

                if (showTitleToggle) showTitleToggle.checked = showTitle;
                if (showProgressBarToggle) showProgressBarToggle.checked = showProgressBar;
                if (showAudioBarToggle) showAudioBarToggle.checked = showAudioBar;
                if (showImagesToggle) showImagesToggle.checked = showImages;
                if (sleepTimerInput) sleepTimerInput.value = prefs.sleep_timer_minutes || 0;
                if (showSleepTimerToggle) showSleepTimerToggle.checked = prefs.show_sleep_timer || false;
            }, 100);

            applyFontSettings(fontSize, fontFamily);
        })
        .catch(error => {
            console.error('Error loading preferences:', error);
            applyVisibilitySettings(true, true, true);
            applyFontSettings('16', 'system');
        });}


function applyVisibilitySettings(showTitle, showProgressBar, showAudioBar) {
    const playerHeader = document.getElementById('playerHeader');
    const progressBarContainer = document.getElementById('progressBarContainer');
    const playerControls = document.getElementById('playerControls');
    const playPauseBtn = DOM.playPauseBtn;
    const jumpToCurrentBtn = document.getElementById('backToCurrentBtn');
    const bottomActions = document.getElementById('bottomActionsContainer');

    if (playerHeader) playerHeader.style.display = showTitle ? 'block' : 'none';
    if (progressBarContainer) progressBarContainer.style.display = showProgressBar ? 'block' : 'none';

    if (playerControls) {
        if (showAudioBar) {
            playerControls.style.display = 'block';
            if (bottomActions) bottomActions.style.display = 'none';
        } else {
            playerControls.style.display = 'none';
            if (bottomActions && playPauseBtn && jumpToCurrentBtn) {
                bottomActions.style.display = 'flex';
                bottomActions.style.flexDirection = 'row';
                bottomActions.style.alignItems = 'center';

                if (!bottomActions.contains(playPauseBtn)) {
                    bottomActions.appendChild(playPauseBtn);
                    playPauseBtn.className = 'btn';
                    playPauseBtn.style = '';
                }
                if (!bottomActions.contains(jumpToCurrentBtn)) {
                    bottomActions.appendChild(jumpToCurrentBtn);
                    jumpToCurrentBtn.className = 'btn';
                    jumpToCurrentBtn.style = '';
                }
            }
        }
    }
}

function updateFontSettings() {
    const fontSize = document.getElementById('fontSizeSelect').value;
    const fontFamily = document.getElementById('fontFamilySelect').value;

    const showTitleToggle = document.getElementById('showTitleToggle');
    const showProgressBarToggle = document.getElementById('showProgressBarToggle');
    const showAudioBarToggle = document.getElementById('showAudioBarToggle');

    const showTitle = showTitleToggle ? showTitleToggle.checked : true;
    const showProgressBar = showProgressBarToggle ? showProgressBarToggle.checked : true;
    const showAudioBar = showAudioBarToggle ? showAudioBarToggle.checked : true;

    savePreferencesToServer({
        font_size: fontSize,
        font_family: fontFamily,
        progress_mode: state.progress.mode,
        time_mode: state.progress.timeMode,
        show_title: showTitle,
        show_progress_bar: showProgressBar,
        show_audio_bar: showAudioBar
    });

    applyFontSettings(fontSize, fontFamily);
}

async function savePreferencesToServer(prefs) {
    try {
        await apiCall('/audiobooks/preferences/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(prefs)
        });
    } catch (error) {
        console.error('Error saving preferences:', error);
    }
}

function toggleProgressMode() {
    const select = document.getElementById('progressModeSelect');
    state.progress.mode = select ? select.value : (state.progress.mode === 'book' ? 'chapter' : 'book');

    const prefs = gatherPreferences();
    savePreferencesToServer(prefs);

    const currentTime = getGlobalTime(state.audiobook.currentChunk, DOM.player.currentTime);
    updateProgressDisplay(currentTime);

    console.log('[PLAYER] Progress mode:', state.progress.mode);
}

function toggleTimeMode() {
    const select = document.getElementById('timeModeSelect');
    state.progress.timeMode = select ? select.value : (state.progress.timeMode === 'total' ? 'remaining' : 'total');

    const prefs = gatherPreferences();
    savePreferencesToServer(prefs);

    const currentTime = getGlobalTime(state.audiobook.currentChunk, DOM.player.currentTime);
    updateProgressDisplay(currentTime);

    console.log('[PLAYER] Time mode:', state.progress.timeMode);
}

function gatherPreferences() {
    const fontSize = document.getElementById('fontSizeSelect').value;
    const fontFamily = document.getElementById('fontFamilySelect').value;
    const showTitleToggle = document.getElementById('showTitleToggle');
    const showProgressBarToggle = document.getElementById('showProgressBarToggle');
    const showAudioBarToggle = document.getElementById('showAudioBarToggle');
    const showImagesToggle = document.getElementById('showImagesToggle');
    const sleepTimerInput = document.getElementById('sleepTimerInput');
    const showSleepTimerToggle = document.getElementById('showSleepTimerToggle');

    return {
        font_size: fontSize,
        font_family: fontFamily,
        progress_mode: state.progress.mode,
        time_mode: state.progress.timeMode,
        show_title: showTitleToggle ? showTitleToggle.checked : true,
        show_progress_bar: showProgressBarToggle ? showProgressBarToggle.checked : true,
        show_audio_bar: showAudioBarToggle ? showAudioBarToggle.checked : true,
        show_images: showImagesToggle ? showImagesToggle.checked : false,
        sleep_timer_minutes: sleepTimerInput ? parseInt(sleepTimerInput.value) || 0 : 0,
        show_sleep_timer: showSleepTimerToggle ? showSleepTimerToggle.checked : false
    };
}

function applyFontSettings(fontSize, fontFamily) {
    const fontMap = {
        'serif': 'Georgia, "Times New Roman", serif',
        'sans': 'Arial, Helvetica, sans-serif',
        'mono': '"Courier New", Courier, monospace',
        'system': '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif'
    };

    DOM.lrcDisplay.style.fontSize = fontSize + 'px';
    DOM.lrcDisplay.style.fontFamily = fontMap[fontFamily] || fontMap['system'];
}

function toggleTitle() {
    const checkbox = document.getElementById('showTitleToggle');
    const playerHeader = document.getElementById('playerHeader');
    playerHeader.style.display = checkbox.checked ? 'block' : 'none';
    savePreferencesToServer(gatherPreferences());
}

function toggleProgressBar() {
    const checkbox = document.getElementById('showProgressBarToggle');
    const progressBarContainer = document.getElementById('progressBarContainer');
    progressBarContainer.style.display = checkbox.checked ? 'block' : 'none';
    savePreferencesToServer(gatherPreferences());
}

function toggleAudioBar() {
    const checkbox = document.getElementById('showAudioBarToggle');
    const showAudioBar = checkbox.checked;
    applyVisibilitySettings(
        document.getElementById('showTitleToggle')?.checked ?? true,
        document.getElementById('showProgressBarToggle')?.checked ?? true,
        showAudioBar
    );
    savePreferencesToServer(gatherPreferences());
}

async function toggleImages() {
    const checkbox = document.getElementById('showImagesToggle');
    const showImages = checkbox.checked;
    const wasEnabled = state.images.enabled;
    
    state.images.enabled = showImages;
    savePreferencesToServer(gatherPreferences());
    
    if (showImages && !wasEnabled && state.audiobook.id) {
        // Load images if now enabled and we have an audiobook
        showToast('Loading images...');
        await loadAudiobookImages(state.audiobook.id);
    } else if (!showImages && wasEnabled) {
        // Clear images if disabled
        state.images.data = {};
        state.images.chunkImages = [];
        state.images.loaded = false;
        
        // Reload LRC to remove images
        if (state.lrc.fullData.length > 0 && state.audiobook.id) {
            const currentTime = getGlobalTime(state.audiobook.currentChunk, DOM.player.currentTime);
            loadLRCWindow(currentTime);
        }
    }
}

// ========== SLEEP TIMER ==========
function initSleepTimer() {
    // Try to load sleep timer setting from settings
    // For player.js, settings might not be loaded yet, so start with defaults
    const prefs = gatherPreferences();
    const sleepTimerMinutes = prefs.sleep_timer_minutes || 0;
    const showSleepTimer = prefs.show_sleep_timer || false;
    
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
        if (!state.scroll.auto) {
            resetSleepTimer();
        }
    };

    // Store reference to reset functions
    state.sleepTimer.resetFunction = resetSleepTimer;
    state.sleepTimer.scrollResetFunction = handleScrollReset;

    // Track various user interactions (removed mousemove as it fires too frequently)
    document.addEventListener('click', resetSleepTimer, true);
    document.addEventListener('scroll', handleScrollReset, true);
    document.addEventListener('keydown', resetSleepTimer, true);
    document.addEventListener('touchstart', resetSleepTimer, true);
    document.addEventListener('touchend', resetSleepTimer, true);
    document.addEventListener('input', resetSleepTimer, true);
    document.addEventListener('change', resetSleepTimer, true);
    
    // Also listen on LRC display for scrolling
    
    // Also listen on LRC display for scrolling
    const lrcDisplay = DOM.lrcDisplay;
    if (lrcDisplay) {
        lrcDisplay.addEventListener('scroll', handleScrollReset, true);
    }
    
    state.sleepTimer.listenersSetup = true;
    console.log('[SLEEP TIMER] Listeners set up');
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
    console.log(`[SLEEP TIMER] Started: ${state.sleepTimer.minutes} minutes`);

    state.sleepTimer.timeoutId = setTimeout(() => {
        if (state.playback.isPlaying) {
            console.log('[SLEEP TIMER] Triggered - pausing playback');
            togglePlayPause(); // This will pause the playback
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

    // Update preferences
    const prefs = gatherPreferences();
    prefs.sleep_timer_minutes = minutes;
    prefs.show_sleep_timer = showTimer;
    savePreferencesToServer(prefs);

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
            const pad = (n) => String(n).padStart(2, '0');
            const remainingMinutes = Math.floor(remainingMs / 60000);
            const remainingSeconds = Math.floor((remainingMs % 60000) / 1000);
            
            timerDisplay.textContent = `${pad(remainingMinutes)}:${pad(remainingSeconds)}`;
            timerDisplay.style.display = 'block';
        } else {
            timerDisplay.style.display = 'none';
        }
    }
}

// ========== INITIALIZATION ==========
document.addEventListener('DOMContentLoaded', () => {
    loadFontSettings();
    
    // Add global error handler for audio element to suppress transient errors
    // (retry logic will handle actual failures)
    const audioPlayer = DOM.player;
    if (audioPlayer) {
        audioPlayer.addEventListener('error', (e) => {
            // Only log, don't show error - retry logic handles recovery
            console.warn('[AUDIO] Audio element error (will retry if needed):', e.target.error?.message || 'Unknown error');
        });
    }
});
