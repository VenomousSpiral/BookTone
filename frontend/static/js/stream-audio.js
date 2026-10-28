// Audio playback, controls, caching, and prefetching
// Depends on: stream-state.js (state, DOM, API_BASE, log, etc.)

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
            if (state.currentAudioBlobUrl) {
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
}

function startAudioWatchdog() {
    if (state.audioWatchdogInterval) clearInterval(state.audioWatchdogInterval);
    state.audioWatchdogInterval = setInterval(() => {
        const audio = DOM.audio;

        // Guard: don't run watchdog if element is in error state.
        if (!audio || !state.isPlaying || audio.error || state.isGeneratingAudio) return;

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
    try { DOM.audio.pause(); } catch(e) {}
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
    try { audio.pause(); } catch(e) {}
    if (audio && !state.currentAudioBlobUrl) {
        audio.src = '';  // Only clear src if we don't own the blob URL.
    }

    if (state.currentAudioBlobUrl) {
        try { URL.revokeObjectURL(state.currentAudioBlobUrl); } catch(e) {}
        state.currentAudioBlobUrl = null;
    }

    if (state.currentAudioSegment?.url && state.currentAudioSegment.url !== state.currentAudioBlobUrl) {
        try { URL.revokeObjectURL(state.currentAudioSegment.url); } catch(e) {}
    }

    state.currentAudioSegment = null;
    state.hasShownErrorAlert = false;  // Reset so user can retry cleanly.
}

async function playCurrentChunk() {
    const audio = DOM.audio;
    if (!state.isPlaying) return;

    // CAS: Capture playback ID at entry — if someone else moved past us, bail.
    const thisPlaybackId = state.audioPlaybackId;
    const thisIsGenerating = state.isGeneratingAudio; // Should be false for playCurrentChunk caller.

    const chunk = state.book.chunks[state.currentChunk];
    if (!chunk) throw new Error(`No chunk found at index ${state.currentChunk}`);

    // CAS: use content hash as stable key (falls back to index if no hash)
    const cacheKey = chunk._content_hash || `idx-${state.currentChunk}`;

    let audioBlob;
    if (state.audioCache.has(cacheKey)) {
        audioBlob = state.audioCache.get(cacheKey);
    } else {
        audioBlob = await generateAudio(chunk.start_idx, chunk.end_idx);
        state.audioCache.set(cacheKey, audioBlob);
    }

    // CAS: verify we're still the active playback before committing.
    if (state.audioPlaybackId !== thisPlaybackId || !state.isPlaying) {
        _log('[STREAM] playCurrentChunk superseded by newer operation');
        return;
    }

    if (state.currentAudioBlobUrl) {
        URL.revokeObjectURL(state.currentAudioBlobUrl);
    }

    const audioUrl = URL.createObjectURL(audioBlob);
    state.currentAudioBlobUrl = audioUrl;
    state.currentAudioSegment = {
        chunkIndex: state.currentChunk,
        url: audioUrl,
        playbackId: thisPlaybackId  // Use captured ID, not current one.
    };

    audio.src = audioUrl;
    audio.playbackRate = parseFloat(DOM.speedControl.value);

    try {
        await audio.play();
    } catch (err) {
        const errName = err?.name || '';
        const errMsg = err?.message || String(err);
        // These are expected in some contexts — don't treat as hard failures.
        if (!(errMsg.includes('AbortError') || errMsg.includes('NotAllowed') ||
              errMsg.includes('abort') || errName === 'AbortError')) {
            log(`playCurrentChunk.play() error: ${errMsg}`);
            throw err; // Re-throw so the caller can handle it.
        }
    }
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
        const cacheKey = chunk._content_hash || `idx-${state.currentChunk}`;

        const audio = DOM.audio;
        if (state.currentAudioSegment && state.currentAudioSegment.chunkIndex !== state.currentChunk) {
            try { audio.pause(); } catch(e2) {}
            audio.currentTime = 0;
        }

        if (!state.audioCache.has(cacheKey)) showAudioStatus('Generating audio...');

        const audioBlob = await generateAudio(chunk.start_idx, chunk.end_idx);
        hideAudioStatus();
        state.isGeneratingAudio = false;

        // CAS: verify we're still the active playback.
        if (state.audioPlaybackId !== thisPlaybackId || !state.isPlaying) {
            _log('[STREAM] playNextSegment superseded by newer operation');
            return;
        }

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
            // Audio play failed — let the error event handler deal with recovery.
            _log(`[STREAM] audio.play() threw in playNextSegment: ${err?.message || err}`);
            // Don't set hasShownErrorAlert here; only show alert if all retries exhaust.
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
                voice: state.settings.preferred_voice || 'alloy',
                use_cached_audio: state.useCachedAudio
            })
        });

        if (!res.ok) throw new Error(`Audio generation failed: ${await res.text()}`);

        const audioBlob = await res.blob();
        state.audioCache.set(cacheKey, audioBlob);
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

        if (state.audioCache.has(cacheKey) || state.prefetchInFlight.has(cacheKey)) continue;
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
        state.audioCache.clear();
        showToast('Cache cleared');
        refreshCacheStatus();
    })
    .catch(err => {
        showToast('Error clearing cache: ' + err.message);
        console.error('Cache clear error:', err);
    });
}

function skipAudioGeneration() {
    state.isGeneratingAudio = false;
    hideAudioStatus();
    showToast('Audio generation skipped');
}
