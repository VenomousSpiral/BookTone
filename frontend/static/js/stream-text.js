// Book parsing, chunk loading, lazy loading, and text display
// Depends on: stream-state.js (state, DOM, API_BASE, EBOOK_PATH, log, etc.)

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
        updateAudiobookIndicator();
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
    _log('[STREAM] Loading text chunks on-demand...');

    DOM.textDisplay.innerHTML = '';

    for (let i = 0; i < Math.min(LOAD.INITIAL, state.book.chunks.length); i++) {
        DOM.textDisplay.appendChild(createChunkElement(i));
    }

    await loadChunksAround(state.currentChunk, LOAD.RADIUS);
    setupChunkObserver();
    DOM.textDisplay.addEventListener('scroll', handleScroll);

    _log('[STREAM] Initial chunks created');
    updateProgress();
}

function createChunkElement(chunkIndex) {
    const div = document.createElement('div');
    div.className = 'chunk-container';
    div.dataset.chunkIndex = chunkIndex;
    div.dataset.loaded = 'false';
    div.style.minHeight = '120px';
    div.onclick = () => loadAndJumpToChunk(chunkIndex);

    if (isBookmarked(chunkIndex)) {
        div.classList.add('bookmarked');
    }

    div.addEventListener('touchstart', handleTouchStart);
    div.addEventListener('touchmove', handleTouchMove);
    div.addEventListener('touchend', handleTouchEnd);

    div.style.userSelect = 'none';
    div.style.webkitUserSelect = 'none';

    if (state.chunkObserver) {
        state.chunkObserver.observe(div);
    }

    return div;
}

// ========== INTERSECTION OBSERVER FOR LAZY LOADING ==========
function setupChunkObserver() {
    if (state.chunkObserver) {
        state.chunkObserver.disconnect();
    }

    state.chunkObserver = new IntersectionObserver((entries) => {
        const unloadedVisible = [];

        for (const entry of entries) {
            if (entry.isIntersecting) {
                const el = entry.target;
                if (el.dataset.loaded === 'false') {
                    unloadedVisible.push(parseInt(el.dataset.chunkIndex));
                }
            }
        }

        if (unloadedVisible.length > 0) {
            loadChunksBatch(unloadedVisible);
        }
    }, {
        root: DOM.textDisplay,
        rootMargin: '2000px 0px 2000px 0px',
        threshold: 0
    });

    DOM.textDisplay.querySelectorAll('.chunk-container').forEach(el => {
        state.chunkObserver.observe(el);
    });
}

function handleScroll() {
    if (state.scroll.autoInProgress) return;
    state.scroll.lastManual = Date.now();

    if (state.scroll.timeout) clearTimeout(state.scroll.timeout);
    state.scroll.timeout = setTimeout(() => {
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
            targetChunk = Math.max(0, firstLoaded - LOAD.RADIUS);
            shouldLoad = true;
        } else if (distanceFromBottom < threshold && lastLoaded < state.book.total_chunks - 1) {
            targetChunk = Math.min(state.book.total_chunks - 1, lastLoaded + LOAD.RADIUS);
            shouldLoad = true;
        }

        if (shouldLoad) {
            loadChunksAround(targetChunk, LOAD.RADIUS);
        }
    }, SCROLL.DEBOUNCE);
}

// ========== BATCH TEXT LOADING ==========
async function loadChunksBatch(chunkIndices) {
    const toLoad = chunkIndices.filter(idx => {
        if (state.loadingChunks.has(idx)) return false;
        const el = document.querySelector(`.chunk-container[data-chunk-index="${idx}"]`);
        return el && el.dataset.loaded === 'false';
    });

    if (toLoad.length === 0) return;

    toLoad.forEach(idx => state.loadingChunks.add(idx));

    try {
        const withImages = state.showImages;
        const res = await fetch(`${API_BASE}/stream/text-batch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ebook_path: EBOOK_PATH,
                chunk_indices: toLoad,
                with_images: withImages
            })
        });

        if (!res.ok) throw new Error('Batch text load failed');

        const result = await res.json();
        const chunksData = result.chunks;

        for (const idx of toLoad) {
            const data = chunksData[String(idx)];
            if (!data) continue;

            const chunkDiv = document.querySelector(`.chunk-container[data-chunk-index="${idx}"]`);
            if (!chunkDiv || chunkDiv.dataset.loaded === 'true') continue;

            applyChunkContent(chunkDiv, idx, data);
        }
    } catch (error) {
        logError('Batch chunk load error', error);
        for (const idx of toLoad) {
            loadSingleChunk(idx).catch(() => {});
        }
    } finally {
        toLoad.forEach(idx => state.loadingChunks.delete(idx));
    }
}

function applyChunkContent(chunkDiv, chunkIndex, data) {
    chunkDiv.innerHTML = '';

    if (state.showImages && data.image_data && data.image_data.length > 0 && data.display_text) {
        let text = data.display_text;
        const fragment = document.createDocumentFragment();

        const markerPositions = data.image_data
            .map(imgData => ({
                pos: text.indexOf(imgData.marker),
                marker: imgData.marker,
                length: imgData.marker.length,
                id: imgData.id
            }))
            .filter(m => m.pos >= 0)
            .sort((a, b) => a.pos - b.pos);

        let lastPos = 0;
        for (const markerInfo of markerPositions) {
            if (markerInfo.pos > lastPos) {
                const textBefore = text.substring(lastPos, markerInfo.pos);
                fragment.appendChild(document.createTextNode(textBefore));
            }

            const imageWrapper = document.createElement('div');
            imageWrapper.className = 'inline-image-wrapper';
            fragment.appendChild(imageWrapper);

            loadImage(markerInfo.id).then(img => {
                if (img) imageWrapper.appendChild(img);
            });

            lastPos = markerInfo.pos + markerInfo.length;
        }

        if (lastPos < text.length) {
            fragment.appendChild(document.createTextNode(text.substring(lastPos)));
        }

        chunkDiv.appendChild(fragment);
    } else {
        const textNode = document.createTextNode(data.text);
        chunkDiv.appendChild(textNode);
    }

    chunkDiv.dataset.loaded = 'true';
    chunkDiv.style.minHeight = '';

    if (isBookmarked(chunkIndex)) {
        chunkDiv.classList.add('bookmarked');
    }

    if (state.chunkObserver) {
        state.chunkObserver.unobserve(chunkDiv);
    }
}

async function loadChunksAround(centerChunk, radius = 25) {
    const startChunk = Math.max(0, centerChunk - radius);
    const endChunk = Math.min(state.book.chunks.length - 1, centerChunk + radius);

    _log(`[STREAM] Loading chunks ${startChunk} to ${endChunk} around chunk ${centerChunk}`);

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
            if (state.chunkObserver) state.chunkObserver.unobserve(element);
            element.remove();
        }
    }

    for (let i = startChunk; i <= endChunk; i++) {
        if (!existingChunks.has(i)) {
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
            if (Math.abs(scrollAdjustment) > 1) {
                textDisplay.scrollTop += scrollAdjustment;
            }
        }
    }

    const chunksToLoad = [];
    for (let i = startChunk; i <= endChunk; i++) {
        const chunkDiv = textDisplay.querySelector(`.chunk-container[data-chunk-index="${i}"]`);
        if (chunkDiv?.dataset.loaded === 'false' && !state.loadingChunks.has(i)) {
            chunksToLoad.push(i);
        }
    }

    chunksToLoad.sort((a, b) => Math.abs(a - centerChunk) - Math.abs(b - centerChunk));

    for (let i = 0; i < chunksToLoad.length; i += LOAD.BATCH) {
        const batch = chunksToLoad.slice(i, i + LOAD.BATCH);
        await loadChunksBatch(batch);
    }
}

async function loadSingleChunk(chunkIndex) {
    try {
        const chunkDiv = document.querySelector(`.chunk-container[data-chunk-index="${chunkIndex}"]`);
        if (!chunkDiv || chunkDiv.dataset.loaded === 'true') return;
        if (state.loadingChunks.has(chunkIndex)) return;

        state.loadingChunks.add(chunkIndex);

        const withImages = state.showImages ? '&with_images=true' : '';
        const res = await fetch(`${API_BASE}/stream/text?ebook_path=${encodeURIComponent(EBOOK_PATH)}&chunk_index=${chunkIndex}${withImages}`);
        if (!res.ok) throw new Error('Failed to load chunk text');

        const data = await res.json();
        applyChunkContent(chunkDiv, chunkIndex, data);
    } catch (error) {
        logError('Chunk load error', error);
        const chunkDiv = document.querySelector(`.chunk-container[data-chunk-index="${chunkIndex}"]`);
        if (chunkDiv) {
            chunkDiv.textContent = `[Error loading chunk ${chunkIndex}]`;
            chunkDiv.style.minHeight = '';
        }
    } finally {
        state.loadingChunks.delete(chunkIndex);
    }
}

async function loadImage(imageId) {
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
    if (state.isJumping) {
        _log('[STREAM] Already jumping, ignoring click');
        return;
    }
    state.isJumping = true;

    const safetyTimeout = setTimeout(() => {
        if (state.isJumping) {
            _warn('[STREAM] isJumping flag stuck, resetting');
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

async function jumpToChunk(chunkIndex) {
    if (chunkIndex < 0 || chunkIndex >= state.book.total_chunks) return;

    _log('[STREAM] Jumping to chunk:', chunkIndex);

    state.audioPlaybackId++;
    const thisPlaybackId = state.audioPlaybackId;
    const wasPlaying = state.isPlaying;

    state.isPlaying = false;
    state.isGeneratingAudio = false;
    updatePlayButton();

    const audio = DOM.audio;
    audio.pause();
    audio.currentTime = 0;

    state.currentChunk = chunkIndex;

    const existingChunk = document.querySelector(`.chunk-container[data-chunk-index="${chunkIndex}"]`);
    const needsLoading = !existingChunk || existingChunk.dataset.loaded === 'false';

    if (needsLoading) {
        await loadChunksAround(chunkIndex, LOAD.RADIUS);
    }

    highlightCurrentChunk();
    updateProgress();
    await scrollToCurrentChunk();
    saveProgress();

    if (wasPlaying && state.audioPlaybackId === thisPlaybackId) {
        await playNextSegment(true);
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

    _log(`[STREAM] Scrolling to current chunk ${state.currentChunk}`);

    let currentChunk = document.querySelector(`.chunk-container[data-chunk-index="${state.currentChunk}"]`);

    if (!currentChunk) {
        _log(`[STREAM] Chunk ${state.currentChunk} not in DOM, loading chunks around it`);
        await loadChunksAround(state.currentChunk, LOAD.RADIUS);
        currentChunk = document.querySelector(`.chunk-container[data-chunk-index="${state.currentChunk}"]`);

        if (!currentChunk) {
            _warn(`[STREAM] Chunk ${state.currentChunk} still not in DOM after loading`);
            return;
        }
    }

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

function seekToPosition(percent) {
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

    _log('[STREAM] Seeking to chunk:', targetChunk);

    jumpToChunk(targetChunk);
}

function toggleImages() {
    const toggle = document.getElementById('showImagesToggle');
    state.showImages = toggle?.checked || false;
    state.settings.show_images = state.showImages;
    saveSettingsToServer();
    parseBook();
}
