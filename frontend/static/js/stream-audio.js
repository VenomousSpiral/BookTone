// Audio playback, controls, caching, and prefetching
// Depends on: stream-state.js (state, DOM, API_BASE, log, etc.)

let audioContext = null;

function isIOSMediaMode() {
    const userAgent = navigator.userAgent || '';
    const platform = navigator.platform || '';
    return /iPad|iPhone|iPod/.test(userAgent) ||
        (platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

function getAudioContext() {
    if (!audioContext || audioContext.state === 'closed') {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return null;
        audioContext = new AudioContextClass();
    }
    return audioContext;
}

async function resumeAudioContext() {
    const context = getAudioContext();
    if (!context) return null;
    if (context.state === 'suspended') await context.resume();
    return context.state === 'running' ? context : null;
}

function canUseWebAudio() {
    return !isIOSMediaMode() && !document.hidden &&
        !!(window.AudioContext || window.webkitAudioContext);
}

function getAudioCacheKey(chunkIndex) {
    const chunk = state.book?.chunks?.[chunkIndex];
    return chunk?._content_hash || `idx-${chunkIndex}`;
}

function revokeMediaPrefetch(cacheKey) {
    const entry = state.mediaPrefetchCache.get(cacheKey);
    if (!entry) return;
    try { entry.element.removeAttribute('src'); entry.element.load(); } catch (e) {}
    try { URL.revokeObjectURL(entry.url); } catch (e) {}
    state.mediaPrefetchCache.delete(cacheKey);
}

function isPooledMediaUrl(url) {
    for (const entry of state.mediaPrefetchCache.values()) {
        if (entry.url === url) return true;
    }
    return false;
}

function warmMediaElement(cacheKey, audioBlob) {
    if (state.mediaPrefetchCache.has(cacheKey)) return;
    const element = document.createElement('audio');
    element.preload = 'auto';
    element.playsInline = true;
    const url = URL.createObjectURL(audioBlob);
    element.src = url;
    element.load();
    state.mediaPrefetchCache.set(cacheKey, { element, url });
    element.addEventListener('error', () => {
        if (state.mediaPrefetchCache.get(cacheKey)?.element === element) {
            revokeMediaPrefetch(cacheKey);
        }
    }, { once: true });
    log('media-prefetch-start', cacheKey);
}

async function decodeAudioBlob(cacheKey, audioBlob) {
    if (state.audioBufferCache.has(cacheKey)) return state.audioBufferCache.get(cacheKey);
    if (!canUseWebAudio()) return null;

    const existing = state.audioDecodeInFlight.get(cacheKey);
    if (existing) return existing;

    const decodePromise = (async () => {
        const context = await resumeAudioContext();
        if (!context) return null;
        log('audio-decode-start', cacheKey);
        const decoded = await context.decodeAudioData(await audioBlob.arrayBuffer());
        state.audioBufferCache.set(cacheKey, decoded);
        log('audio-decode-finished', cacheKey);
        return decoded;
    })();

    state.audioDecodeInFlight.set(cacheKey, decodePromise);
    try {
        return await decodePromise;
    } catch (error) {
        logError(`Audio decode failed for ${cacheKey}`, error);
        return null;
    } finally {
        state.audioDecodeInFlight.delete(cacheKey);
    }
}

async function prepareAudio(cacheKey, audioBlob) {
    if (isIOSMediaMode() || document.hidden) {
        warmMediaElement(cacheKey, audioBlob);
        return null;
    }
    return decodeAudioBlob(cacheKey, audioBlob);
}

function stopWebAudioSource() {
    const segment = state.currentAudioSegment;
    if (!segment?.source) return;
    try { segment.source.onended = null; segment.source.stop(); } catch (e) {}
    try { segment.source.disconnect(); } catch (e) {}
    segment.source = null;
}

function getWebAudioOffset(segment) {
    const context = getAudioContext();
    if (!context || segment?.startedAt == null) return segment?.offset || 0;
    const speed = segment.playbackRate || 1;
    return Math.min(segment.duration, (context.currentTime - segment.startedAt) * speed + (segment.offset || 0));
}

// ========== AUDIO PLAYER SETUP ==========
function setupAudioPlayer() {
    const audio = DOM.audio;

    audio.addEventListener('timeupdate', handleChunkTransition);

    audio.addEventListener('error', async (e) => {
        if (state.isUserStopping || state.isGeneratingAudio) {
            _log('[STREAM] Ignoring audio error during stop or generation');
            return;
        }

        logError('Audio element error', e);

        const errorCode = audio.error?.code;
        const errorMsg = audio.error?.message || 'Unknown error';
        log(`Audio error details - code: ${errorCode}, message: ${errorMsg}`);

        // Guard against concurrent transitions — if we're mid-transition, let that handler recover.
        if (state.isTransitioning) {
            _log('[STREAM] Audio error during transition, skipping recovery');
            return;
        }

        const thisPlaybackId = state.audioPlaybackId;

        // Attempt to skip forward through consecutive broken chunks using CAS guard.
        let skippedChunks = 0;
        const maxSkipRetries = Math.min(5, (state.book?.total_chunks || 1) - state.currentChunk);

        while (skippedChunks < maxSkipRetries && state.isPlaying && state.audioPlaybackId === thisPlaybackId) {
            if (!state.book?.lrc_data) break;

            const totalChunks = state.book.audio_chunks?.length || state.book.total_chunks || 0;
            const nextChunk = state.currentChunk + 1;

            if (nextChunk >= totalChunks) break; // No more chunks to skip to.

            skippedChunks++;
            log(`Skipping broken chunk ${state.currentChunk}, trying chunk ${nextChunk} (${skippedChunks}/${maxSkipRetries})`);
            showToast(skippedChunks === 1 ? 'Skipping unplayable audio segment...' : `Skipped ${skippedChunks} segments...`);

            state.isTransitioning = true;

            // Clean up old source and set next chunk.
            try { audio.pause(); } catch (e2) {}
            if (state.currentAudioBlobUrl && !isPooledMediaUrl(state.currentAudioBlobUrl)) {
                URL.revokeObjectURL(state.currentAudioBlobUrl);
                state.currentAudioBlobUrl = null;
            }
            state.currentAudioSegment = null;

            // Check CAS before advancing — another operation may have moved past us.
            if (state.audioPlaybackId !== thisPlaybackId || !state.isPlaying) {
                _log('[STREAM] Playback ID changed during skip, aborting');
                state.isTransitioning = false;
                return;
            }

            state.currentChunk = nextChunk;

            try {
                await playCurrentChunk();
                if (state.audioPlaybackId === thisPlaybackId && state.isPlaying) {
                    log(`[STREAM] Successfully recovered from audio error after skipping ${skippedChunks} chunk(s)`);
                    showToast('Playback resumed');
                }
            } catch (err) {
                const errName = err?.name || '';
                if (state.audioPlaybackId !== thisPlaybackId || !state.isPlaying) {
                    _log('[STREAM] Playback stopped during skip recovery, ignoring');
                } else if (skippedChunks < maxSkipRetries && (errName === 'AbortError' || errMsg.includes?.('abort') || err.message?.includes?.('aborted'))) {
                    // Abort error — chunk might become available; try next one.
                    _log(`[STREAM] Chunk ${nextChunk} aborted, trying next`);
                } else if (skippedChunks < maxSkipRetries) {
                    // Brief pause before retrying the next chunk to avoid hammering.
                    await new Promise(r => setTimeout(r, 200));
                    continue; // Try next chunk in the loop.
                }
            } finally {
                state.isTransitioning = false;
            }

            break; // Success or final attempt — exit skip loop.
        }

        if (state.isPlaying && !DOM.audio.src) {
            // All skip attempts exhausted and still playing with no audio source.
            _log('[STREAM] Exhausted all skip retries, stopping playback');
            state.hasShownErrorAlert = true;
            stopPlaying();
            showToast('Audio segment failed — playback stopped. You can restart to retry.');
        }
    });

    audio.addEventListener('pause', () => {
        // Browser fired pause during playback — stop unless we're actively generating.
        if (state.currentAudioSegment?.mode === 'web-audio') return;
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
        if (audio.error) return;  // Element has error — don't try to play.
        if (state.currentAudioSegment?.mode === 'web-audio') return;

        if (state.isPlaying && audio.paused) {
            const thisPlaybackId = state.audioPlaybackId;
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

    document.addEventListener('visibilitychange', async () => {
        const segment = state.currentAudioSegment;
        if (!document.hidden || segment?.mode !== 'web-audio' || !state.isPlaying) return;

        const offset = getWebAudioOffset(segment);
        const cacheKey = getAudioCacheKey(segment.chunkIndex);
        const audioBlob = state.audioCache.get(cacheKey);
        stopWebAudioSource();
        if (!audioBlob) return;

        state.audioOutputMode = 'media-element';
        try {
            await playMediaChunk(segment.chunkIndex, cacheKey, audioBlob, segment.playbackId, offset);
            log('audio-output-mode-changed', 'media-element');
        } catch (error) {
            logError('Failed to switch Web Audio playback to media element', error);
        }
    });
}

function startAudioWatchdog() {
    if (state.audioWatchdogInterval) clearInterval(state.audioWatchdogInterval);
    state.audioWatchdogInterval = setInterval(() => {
        const audio = DOM.audio;

        // Guard: don't run watchdog if element is in error state.
        if (!audio || !state.isPlaying || audio.error || state.isGeneratingAudio ||
            state.currentAudioSegment?.mode === 'web-audio') return;

        const audioMatchesCurrentChunk = state.currentAudioSegment?.chunkIndex === state.currentChunk;
        if (audio?.src && audioMatchesCurrentChunk) {
            if (audio.paused && audio.readyState >= 2) {
                audio.play().catch(err => {
                    // Keep trying silently — watchdog will retry next interval.
                });
            }
        }
    }, 500);
}

async function handleChunkTransition() {
    const audio = DOM.audio;
    if (!state.isPlaying || !audio?.src || state.isTransitioning) return;
    if (state.currentAudioSegment?.mode === 'web-audio') return;

    // Guard: don't transition if element is in error state.
    if (audio.error) {
        _log('[STREAM] Ignoring chunk transition — audio has error');
        return;
    }

    const currentTime = audio.currentTime;
    const duration = audio.duration;

    // Only trigger when we're near the end of playback OR duration is 0 (short clip).
    if (!(duration && currentTime >= duration - 0.5) && !(duration === 0)) return;

    state.isTransitioning = true;
    const thisPlaybackId = state.audioPlaybackId; // Capture CAS token.

    try {
        if (!state.isPlaying || audio.error) return;
        if (state.currentAudioSegment?.playbackId !== state.audioPlaybackId) return;

        if (state.currentAudioSegment?.chunkIndex !== state.currentChunk) {
            _log('[STREAM] Ignoring transition - audio chunk mismatch');
            return;
        }

        if (state.currentChunk >= state.book.total_chunks - 1) {
            stopPlaying();
            showToast('Finished reading the book! 📚');
            return;
        }

        // Verify we haven't been superseded by an error handler or jump.
        if (state.audioPlaybackId !== thisPlaybackId || state.currentAudioSegment?.playbackId !== thisPlaybackId) {
            _log('[STREAM] Playback ID changed during transition, aborting');
            return;
        }

        state.currentChunk++;
        saveProgress();
        await playNextSegment();
    } finally {
        state.isTransitioning = false;
    }
}

// ========== PLAYBACK CONTROLS ==========
async function startPlaying() {
    // Guard against double-start.
    if (state.isGeneratingAudio || state.isPlaying) return;

    state.isPlaying = true;
    state.hasShownErrorAlert = false;  // Reset alert flag on fresh play attempt.
    updatePlayButton();

    try {
        await resumeAudioContext();
        await loadChunksAround(state.currentChunk, LOAD.RADIUS);
        if (state.isPlaying && !state.isGeneratingAudio) {  // Verify still valid after loading.
            await playNextSegment(true);
        }
    } catch (error) {
        state.isPlaying = false;
        updatePlayButton();
        logError('startPlaying error', error);
        showToast(`Failed to start playback: ${error.message}`);
    }
}

async function pausePlaying() {
    if (!state.isPlaying && !state.isGeneratingAudio) return;  // Guard double-pause.

    state.isPlaying = false;
    state.isGeneratingAudio = false;
    updatePlayButton();
    if (state.currentAudioSegment?.mode === 'web-audio') {
        state.currentAudioSegment.offset = getWebAudioOffset(state.currentAudioSegment);
        stopWebAudioSource();
    } else {
        if (state.currentAudioSegment) {
            state.currentAudioSegment.offset = DOM.audio.currentTime || 0;
        }
        try { DOM.audio.pause(); } catch(e) {}
    }
    await saveProgress();
}

async function stopPlaying() {
    // Guard: prevent double-stop from cascading.
    const wasPlaying = state.isPlaying;
    if (!wasPlaying && !state.isGeneratingAudio) return;

    state.isUserStopping = true;  // Signal to error handler that this is intentional.
    state.isPlaying = false;
    state.isGeneratingAudio = false;
    state.audioPlaybackId++;      // Invalidate all in-flight operations.
    await saveProgress();
    updatePlayButton();

    const audio = DOM.audio;
    stopWebAudioSource();
    try { audio.pause(); } catch(e) {}
    if (audio && !state.currentAudioBlobUrl) {
        audio.src = '';  // Only clear src if we don't own the blob URL.
    }

    if (state.currentAudioBlobUrl && !isPooledMediaUrl(state.currentAudioBlobUrl)) {
        try { URL.revokeObjectURL(state.currentAudioBlobUrl); } catch(e) {}
        state.currentAudioBlobUrl = null;
    }

    if (state.currentAudioSegment?.url && state.currentAudioSegment.url !== state.currentAudioBlobUrl &&
        !isPooledMediaUrl(state.currentAudioSegment.url)) {
        try { URL.revokeObjectURL(state.currentAudioSegment.url); } catch(e) {}
    }

    state.currentAudioSegment = null;
    state.audioOutputMode = 'media-element';
    state.hasShownErrorAlert = false;  // Reset so user can retry cleanly.
}

async function playWebAudioChunk(chunkIndex, cacheKey, decoded, playbackId, offset = 0) {
    let context;
    try {
        context = await resumeAudioContext();
    } catch (error) {
        logError('Unable to resume Web Audio context; using media fallback', error);
        return false;
    }
    if (!context || !state.isPlaying || state.audioPlaybackId !== playbackId) return false;

    const audio = DOM.audio;
    try { audio.pause(); } catch (e) {}
    stopWebAudioSource();

    const source = context.createBufferSource();
    source.buffer = decoded;
    source.playbackRate.value = parseFloat(DOM.speedControl.value);
    const duration = decoded.duration;
    const startOffset = Math.min(Math.max(0, offset), Math.max(0, duration - 0.001));
    const segment = {
        chunkIndex,
        mode: 'web-audio',
        source,
        buffer: decoded,
        playbackId,
        startedAt: context.currentTime,
        offset: startOffset,
        duration,
        playbackRate: source.playbackRate.value
    };
    source.connect(context.destination);
    source.onended = () => {
        if (state.currentAudioSegment !== segment || !state.isPlaying ||
            state.audioPlaybackId !== playbackId) return;
        if (state.currentChunk >= state.book.total_chunks - 1) {
            stopPlaying();
            showToast('Finished reading the book! 📚');
            return;
        }
        state.currentChunk++;
        saveProgress();
        playNextSegment().catch(error => logError('Web Audio transition failed', error));
    };
    state.currentAudioSegment = segment;
    state.audioOutputMode = 'web-audio';
    source.start(0, segment.offset);
    log('web-audio-source-started', cacheKey);
    return true;
}

async function playMediaChunk(chunkIndex, cacheKey, audioBlob, playbackId, offset = 0) {
    const audio = DOM.audio;
    if (!state.isPlaying || state.audioPlaybackId !== playbackId) return false;

    stopWebAudioSource();
    if (state.currentAudioBlobUrl && !isPooledMediaUrl(state.currentAudioBlobUrl)) {
        URL.revokeObjectURL(state.currentAudioBlobUrl);
    }

    const pooled = state.mediaPrefetchCache.get(cacheKey);
    const audioUrl = pooled?.url || URL.createObjectURL(audioBlob);
    state.currentAudioBlobUrl = audioUrl;
    state.currentAudioSegment = {
        chunkIndex,
        mode: 'media-element',
        url: audioUrl,
        playbackId
    };
    state.audioOutputMode = 'media-element';

    audio.src = audioUrl;
    audio.playbackRate = parseFloat(DOM.speedControl.value);
    if (offset > 0) {
        const setOffset = () => {
            try { audio.currentTime = offset; } catch (e) {}
        };
        if (audio.readyState >= 1) setOffset();
        else audio.addEventListener('loadedmetadata', setOffset, { once: true });
    }

    try {
        await audio.play();
    } catch (err) {
        const errName = err?.name || '';
        const errMsg = err?.message || String(err);
        if (!(errMsg.includes('AbortError') || errMsg.includes('NotAllowed') ||
              errMsg.includes('abort') || errName === 'AbortError')) throw err;
    }
    return true;
}

async function playCurrentChunk() {
    if (!state.isPlaying) return;

    // CAS: Capture playback ID at entry — if someone else moved past us, bail.
    const thisPlaybackId = state.audioPlaybackId;
    const thisIsGenerating = state.isGeneratingAudio; // Should be false for playCurrentChunk caller.

    const chunk = state.book.chunks[state.currentChunk];
    if (!chunk) throw new Error(`No chunk found at index ${state.currentChunk}`);

    // CAS: use content hash as stable key (falls back to index if no hash)
    const cacheKey = getAudioCacheKey(state.currentChunk);

    let audioBlob;
    if (state.audioCache.has(cacheKey)) {
        audioBlob = state.audioCache.get(cacheKey);
    } else {
        audioBlob = await generateAudio(chunk.start_idx, chunk.end_idx);
        state.audioCache.set(cacheKey, audioBlob);
    }
    const decoded = state.audioBufferCache.get(cacheKey) ||
        await decodeAudioBlob(cacheKey, audioBlob);

    // CAS: verify we're still the active playback before committing.
    if (state.audioPlaybackId !== thisPlaybackId || !state.isPlaying) {
        _log('[STREAM] playCurrentChunk superseded by newer operation');
        return;
    }

    if (decoded && canUseWebAudio() &&
        await playWebAudioChunk(state.currentChunk, cacheKey, decoded, thisPlaybackId)) return;
    await playMediaChunk(state.currentChunk, cacheKey, audioBlob, thisPlaybackId);
}

async function playNextSegment(shouldPlay = false) {
    if (shouldPlay) {
        state.isPlaying = true;
        updatePlayButton();
    }

    if (!state.isPlaying || state.isGeneratingAudio) return;

    // Double-check: don't start generating if we've moved past this chunk.
    const thisPlaybackId = state.audioPlaybackId;

    if (state.currentChunk >= state.book.total_chunks) {
        stopPlaying();
        showToast('Finished reading the book! 📚');
        return;
    }

    try {
        state.isGeneratingAudio = true;
        const chunk = state.book.chunks[state.currentChunk];
        // CAS: use content hash as stable key (falls back to index if no hash)
        const cacheKey = getAudioCacheKey(state.currentChunk);
        const resumeOffset = state.currentAudioSegment?.mode === 'web-audio' &&
            state.currentAudioSegment.chunkIndex === state.currentChunk
            ? state.currentAudioSegment.offset || 0
            : state.currentAudioSegment?.mode === 'media-element' &&
                state.currentAudioSegment.chunkIndex === state.currentChunk
                ? state.currentAudioSegment.offset || 0
                : 0;

        if (state.currentAudioSegment && state.currentAudioSegment.chunkIndex !== state.currentChunk) {
            if (state.currentAudioSegment.mode === 'web-audio') stopWebAudioSource();
            else {
                try { DOM.audio.pause(); } catch(e2) {}
                DOM.audio.currentTime = 0;
            }
        }

        if (!state.audioCache.has(cacheKey)) showAudioStatus('Generating audio...');

        const audioBlob = await generateAudio(chunk.start_idx, chunk.end_idx);
        const decoded = state.audioBufferCache.get(cacheKey) ||
            await decodeAudioBlob(cacheKey, audioBlob);
        hideAudioStatus();
        state.isGeneratingAudio = false;

        // CAS: verify we're still the active playback.
        if (state.audioPlaybackId !== thisPlaybackId || !state.isPlaying) {
            _log('[STREAM] playNextSegment superseded by newer operation');
            return;
        }

        if (!(decoded && canUseWebAudio() &&
            await playWebAudioChunk(state.currentChunk, cacheKey, decoded, thisPlaybackId, resumeOffset))) {
            await playMediaChunk(state.currentChunk, cacheKey, audioBlob, thisPlaybackId, resumeOffset);
        }

        highlightCurrentChunk();
        updateProgress();

        setTimeout(() => prefetchAudio(state.currentChunk + 1, 3), 0);

    } catch (error) {
        hideAudioStatus();
        state.isGeneratingAudio = false;

        // CAS: check if we're still the active playback.
        if (state.audioPlaybackId !== thisPlaybackId || !state.isPlaying) {
            _log('[STREAM] playNextSegment error, but superseded — ignoring');
            return;
        }

        const isAbortError = error?.message?.includes('aborted') || error?.name === 'AbortError';
        if (isAbortError) return; // Expected during navigation/stop.

        if (!state.hasShownErrorAlert && state.isPlaying) {
            state.hasShownErrorAlert = true;

            const errorMsg = error?.message?.includes('Connection refused') || error?.message?.includes('ECONNREFUSED')
                ? 'Cannot connect to TTS service. Please check if the service is running.'
                : error?.message?.includes('timeout') || error?.message?.includes('timed out')
                    ? 'Audio generation timed out. Please try again.'
                    : error?.message?.includes('Failed to fetch')
                        ? 'Network error. Please check your connection.'
                        : `Audio generation failed: ${error.message}`;

            alert(errorMsg);
        }

        state.isPlaying = false;
        updatePlayButton();
    }
}

async function generateAudio(startChar, endChar, useCache = true, forChunkIndex = null) {
    // CAS: find the chunk to get its content hash for stable caching
    let cacheKey;
    const chunkIndex = forChunkIndex !== null ? forChunkIndex : state.currentChunk;
    if (state.book?.chunks && chunkIndex !== undefined) {
        const chunk = state.book.chunks[chunkIndex];
        cacheKey = chunk?._content_hash || `idx-${chunkIndex}`;
    } else {
        cacheKey = `${startChar}-${endChar}`; // fallback
    }
    if (useCache && state.audioCache.has(cacheKey)) {
        logCache('Hit:', cacheKey);
        const cachedBlob = state.audioCache.get(cacheKey);
        await prepareAudio(cacheKey, cachedBlob);
        return cachedBlob;
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
                voice: state.settings.preferred_voice || 'alloy',
                use_cached_audio: state.useCachedAudio
            })
        });

        if (!res.ok) throw new Error(`Audio generation failed: ${await res.text()}`);

        const audioBlob = await res.blob();
        state.audioCache.set(cacheKey, audioBlob);
        await prepareAudio(cacheKey, audioBlob);
        logCache(`Stored audio, cache size: ${state.audioCache.size}`);

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
    const minKeep = Math.max(0, centerChunkIndex - 5);
    const maxKeep = Math.min(state.book.total_chunks - 1, centerChunkIndex + 10);

    // CAS: use content hash for eviction decisions when available
    const chunkMap = new Map();
    state.book.chunks.forEach((chunk, idx) => {
        if (chunk._content_hash) {
            chunkMap.set(chunk._content_hash, idx);
        } else {
            chunkMap.set(`idx-${idx}`, idx);
        }
    });

    for (const [key] of state.audioCache) {
        const chunkIndex = chunkMap.get(key);
        if (chunkIndex !== undefined && (chunkIndex < minKeep || chunkIndex > maxKeep)) {
            state.audioCache.delete(key);
            state.audioBufferCache.delete(key);
            state.audioDecodeInFlight.delete(key);
            revokeMediaPrefetch(key);
            logCache(`Evicted chunk ${chunkIndex} (keeping ${minKeep}-${maxKeep})`);
        }
    }
}

function prefetchAudio(startChunkIndex, count = CACHE.SIZE) {
    if (!state.book || !state.isPlaying) return;  // Don't prefetch when not playing.

    for (let i = 0; i < count; i++) {
        const chunkIndex = startChunkIndex + i;
        if (chunkIndex >= state.book.total_chunks) break;

        const chunk = state.book.chunks[chunkIndex];
        // CAS: use content hash as stable key (falls back to index if no hash)
        const cacheKey = chunk._content_hash || `idx-${chunkIndex}`;

        if (state.audioCache.has(cacheKey) || state.prefetchInFlight.has(cacheKey)) {
            if (state.audioCache.has(cacheKey)) {
                prepareAudio(cacheKey, state.audioCache.get(cacheKey)).catch(error => {
                    logError(`Audio prefetch preparation failed for ${cacheKey}`, error);
                });
            }
            continue;
        }
        if (state.prefetchInFlight.size >= CACHE.CONCURRENCY) break;

        state.prefetchInFlight.add(cacheKey);
        generateAudio(chunk.start_idx, chunk.end_idx, false, chunkIndex)
            .then(() => state.prefetchInFlight.delete(cacheKey))
            .catch(() => state.prefetchInFlight.delete(cacheKey));
    }

    for (let i = 1; i <= 2; i++) {
        const chunkIndex = state.currentChunk - i;
        if (chunkIndex < 0 || state.prefetchInFlight.size >= CACHE.CONCURRENCY) break;

        const chunk = state.book.chunks[chunkIndex];
        // CAS: use content hash as stable key (falls back to index if no hash)
        const cacheKey = chunk._content_hash || `idx-${chunkIndex}`;

        if (!state.audioCache.has(cacheKey) && !state.prefetchInFlight.has(cacheKey)) {
            state.prefetchInFlight.add(cacheKey);
            generateAudio(chunk.start_idx, chunk.end_idx, false, chunkIndex)
                .then(() => state.prefetchInFlight.delete(cacheKey))
                .catch(() => state.prefetchInFlight.delete(cacheKey));
        }
    }
}

// ========== CONTROLS (audio-related) ==========
function togglePlay() {
    if (state.isPlaying) {
        pausePlaying();
    } else {
        startPlaying();
    }
}

function changeSpeed() {
    const audio = DOM.audio;
    if (audio) {
        audio.playbackRate = parseFloat(DOM.speedControl.value);
    }
    if (state.currentAudioSegment?.mode === 'web-audio') {
        const rate = parseFloat(DOM.speedControl.value);
        state.currentAudioSegment.source.playbackRate.value = rate;
        state.currentAudioSegment.playbackRate = rate;
    }
    updateProgress();
}

function clearAudioCache() {
    if (!confirm('Clear all cached audio for this book?')) return;

    fetch(`${API_BASE}/stream/clear-cache`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ebook_path: EBOOK_PATH })
    })
    .then(() => {
        clearInMemoryAudioCaches();
        showToast('Cache cleared');
        refreshCacheStatus();
    })
    .catch(err => {
        showToast('Error clearing cache: ' + err.message);
        console.error('Cache clear error:', err);
    });
}

function clearInMemoryAudioCaches() {
    state.audioCache.clear();
    state.audioBufferCache.clear();
    state.audioDecodeInFlight.clear();
    for (const key of Array.from(state.mediaPrefetchCache.keys())) revokeMediaPrefetch(key);
}

function skipAudioGeneration() {
    state.isGeneratingAudio = false;
    hideAudioStatus();
    showToast('Audio generation skipped');
}
