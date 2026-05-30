// Audio playback, controls, caching, and prefetching
// Depends on: stream-state.js (state, DOM, API_BASE, log, etc.)

// ========== AUDIO PLAYER SETUP ==========
function setupAudioPlayer() {
    const audio = DOM.audio;

    audio.addEventListener('timeupdate', handleChunkTransition);

    audio.addEventListener('error', async (e) => {
        if (state.isUserStopping) {
            _log('[STREAM] Ignoring audio error during user-initiated stop');
            return;
        }

        logError('Audio element error', e);

        const errorCode = audio.error?.code;
        const errorMsg = audio.error?.message || 'Unknown error';
        log(`Audio error details - code: ${errorCode}, message: ${errorMsg}`);

        if (state.isPlaying && state.book?.lrc_data) {
            const totalChunks = state.book.audio_chunks?.length || 0;
            const nextChunk = state.currentChunk + 1;

            if (nextChunk < totalChunks) {
                log(`Skipping broken chunk ${state.currentChunk}, moving to chunk ${nextChunk}`);
                showToast(`Skipping unplayable audio segment...`);

                audio.src = '';
                state.currentAudioSegment = null;

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

            if (state.currentAudioSegment?.chunkIndex !== state.currentChunk) {
                _log('[STREAM] Ignoring transition - audio chunk mismatch');
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

async function playCurrentChunk() {
    const audio = DOM.audio;
    const chunk = state.book.chunks[state.currentChunk];
    const cacheKey = `${chunk.start_idx}-${chunk.end_idx}`;

    let audioBlob;
    if (state.audioCache.has(cacheKey)) {
        audioBlob = state.audioCache.get(cacheKey);
    } else {
        audioBlob = await generateAudio(chunk.start_idx, chunk.end_idx);
        state.audioCache.set(cacheKey, audioBlob);
    }

    if (state.currentAudioBlobUrl) {
        URL.revokeObjectURL(state.currentAudioBlobUrl);
    }

    const audioUrl = URL.createObjectURL(audioBlob);
    state.currentAudioBlobUrl = audioUrl;
    state.currentAudioSegment = {
        chunkIndex: state.currentChunk,
        url: audioUrl,
        playbackId: state.audioPlaybackId
    };

    audio.src = audioUrl;
    audio.playbackRate = parseFloat(DOM.speedControl.value);
    await audio.play();
}

async function playNextSegment(shouldPlay = false) {
    if (shouldPlay) {
        state.isPlaying = true;
        updatePlayButton();
    }

    if (!state.isPlaying) return;

    if (state.isGeneratingAudio) {
        _log('[STREAM] Already generating audio, skipping duplicate call');
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
