// stream-cache.js — Audiobook cache management for the stream page
// Depends on: stream-state.js (state, API_BASE, EBOOK_PATH, showToast, etc.)

// ========== AUDIOWBOOK PANEL (in settings modal) ==========

async function loadAudiobookProfiles() {
    try {
        const res = await fetch(`${API_BASE}/stream/cache-info?ebook_path=${encodeURIComponent(EBOOK_PATH)}`);
        if (!res.ok) throw new Error('Failed to load audiobook cache info');
        const data = await res.json();
        renderAudiobookPanel(data);
    } catch (error) {
        console.error('[AUDIOPROFILE] Load error:', error);
        const contentEl = document.getElementById('audiobookContent');
        if (contentEl) contentEl.innerHTML = '<span style="color:#888;">No audiobook cache</span>';
    }
}

function renderAudiobookPanel(data) {
    const contentEl = document.getElementById('audiobookContent');
    if (!contentEl) return;

    const caches = data.caches || [];

    if (caches.length === 0) {
        contentEl.innerHTML = `
            <div style="margin-bottom:10px;">
                <span style="color:#888;">No audiobook cache found</span>
            </div>
            <button type="button" onclick="startAudiobookGeneration()" class="btn btn-primary" style="padding:6px 12px;font-size:12px;width:100%;">
                \uD83C\uDFB5 Generate Audiobook
            </button>
        `;
        return;
    }

    let html = '';

    if (caches.length > 1) {
        html += '<select id="profileSelector" onchange="switchProfile(this.value)" style="width:100%;padding:6px;margin-bottom:8px;font-size:12px;background:var(--bg-tertiary);color:var(--text-primary);border:1px solid var(--border-color);border-radius:4px;">';
        caches.forEach(c => {
            const statusIcon = c.status === 'completed' ? '\uD83D\uDFE2' : c.status === 'in_progress' ? '\u23F3' : c.status === 'paused' ? '\u23F8\uFE0F' : '\u274C';
            html += `<option value="${c.model}_${c.voice}">${c.model} / ${c.voice} (${c.completed_chunks}/${c.total_chunks}) ${statusIcon}</option>`;
        });
        html += '</select>';
    }

    const activeCache = caches[0];
    const statusText = activeCache.status === 'completed' ? '\u2705 All chunks generated' :
                       activeCache.status === 'in_progress' ? `\u23F3 Generating... ${Math.round(activeCache.progress)}%` :
                       activeCache.status === 'paused' ? '\u23F8\uFE0F Paused' :
                       activeCache.status === 'failed' ? '\u274C Failed' : activeCache.status;

    html += `<div style="margin-bottom:8px;font-size:12px;">`;
    html += `<strong>${statusText}</strong><br>`;
    html += `<span style="color:#666;">${activeCache.model} / ${activeCache.voice} \u00B7 ${activeCache.completed_chunks}/${activeCache.total_chunks} chunks \u00B7 ${activeCache.size_mb} MB</span>`;
    html += `</div>`;

    html += '<div style="display:flex;gap:6px;flex-wrap:wrap;">';

    if (activeCache.status === 'completed') {
        html += `<button type="button" onclick="prepareDownload('${activeCache.model}', '${activeCache.voice}')" class="btn" style="padding:4px 8px;font-size:11px;">\u2B07\uFE0F Download OPUS</button>`;
        html += `<button type="button" onclick="downloadSource()" class="btn" style="padding:4px 8px;font-size:11px;">\u2B07\uFE0F Source</button>`;
        html += `<button type="button" onclick="handleCacheRegenerate('${EBOOK_PATH}', '${activeCache.model}', '${activeCache.voice}')" class="btn" style="padding:4px 8px;font-size:11px;">\uD83D\uDD04 Regenerate</button>`;
    }

    if (activeCache.status === 'in_progress') {
        html += `<button type="button" onclick="handleCachePause('${EBOOK_PATH}', '${activeCache.model}', '${activeCache.voice}')" class="btn" style="padding:4px 8px;font-size:11px;">\u23F8\uFE0F Pause</button>`;
    } else if (activeCache.status === 'paused' || activeCache.status === 'failed') {
        html += `<button type="button" onclick="handleCacheResume('${EBOOK_PATH}', '${activeCache.model}', '${activeCache.voice}')" class="btn" style="padding:4px 8px;font-size:11px;">\u25B6\uFE0F Resume</button>`;
    }

    html += `<button type="button" onclick="handleCacheDelete('${EBOOK_PATH}', '${activeCache.model}', '${activeCache.voice}')" class="btn btn-danger" style="padding:4px 8px;font-size:11px;">\uD83D\uDDD1\uFE0F</button>`;
    html += '</div>';

    if (caches.length > 1) {
        html += '<div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--border-color);">';
        caches.forEach(c => {
            const icon = c.status === 'completed' ? '\u2705' : c.status === 'in_progress' ? '\u23F3' : c.status === 'paused' ? '\u23F8\uFE0F' : '\u274C';
            html += `<div style="font-size:11px;padding:3px 0;">${icon} ${c.model}/${c.voice} \u00B7 ${c.completed_chunks}/${c.total_chunks} \u00B7 ${c.size_mb} MB</div>`;
        });
        html += '</div>';
    }

    contentEl.innerHTML = html;

    if (activeCache.status === 'in_progress') {
        if (state.audiobookPollInterval) clearInterval(state.audiobookPollInterval);
        state.audiobookPollInterval = setInterval(loadAudiobookProfiles, 3000);
    } else {
        if (state.audiobookPollInterval) {
            clearInterval(state.audiobookPollInterval);
            state.audiobookPollInterval = null;
        }
    }
}

// ========== CACHE OPERATIONS ==========

async function startAudiobookGeneration() {
    const model = state.settings.preferred_model;
    const voice = state.settings.preferred_voice;

    if (!model || !voice) {
        showToast('Please select a model and voice first');
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/stream/generate-cache?ebook_path=${encodeURIComponent(EBOOK_PATH)}&model=${encodeURIComponent(model)}&voice=${encodeURIComponent(voice)}`, {
            method: 'POST'
        });

        if (!res.ok) throw new Error('Failed to start generation');

        const data = await res.json();
        showToast(`Generation started: ${data.model}/${data.voice}`);
        await loadAudiobookProfiles();
    } catch (error) {
        showToast('Error starting generation: ' + error.message);
        console.error('[AUDIOPROFILE] Start error:', error);
    }
}

/** Pause generation for a cache entry */
async function handleCachePause(ebookPath, model, voice) {
    try {
        await fetch(`${API_BASE}/stream/cache-pause?ebook_path=${encodeURIComponent(ebookPath)}&model=${model}&voice=${voice}`, {
            method: 'POST'
        });
        showToast('Generation paused');
        await loadAudiobookProfiles();
    } catch (error) {
        showToast('Error: ' + error.message);
    }
}

/** Resume generation for a cache entry */
async function handleCacheResume(ebookPath, model, voice) {
    try {
        const res = await fetch(`${API_BASE}/stream/cache-resume?ebook_path=${encodeURIComponent(ebookPath)}&model=${model}&voice=${voice}`, {
            method: 'POST'
        });
        if (!res.ok) throw new Error('Failed to resume');
        showToast('Resuming generation...');
        await loadAudiobookProfiles();
    } catch (error) {
        showToast('Error: ' + error.message);
    }
}

/** Delete a cache entry */
async function handleCacheDelete(ebookPath, model, voice) {
    if (!confirm(`Delete cache for ${model}/${voice}?`)) return;
    try {
        const res = await fetch(`${API_BASE}/stream/cache?ebook_path=${encodeURIComponent(ebookPath)}&model=${model}&voice=${voice}`, {
            method: 'DELETE'
        });
        if (!res.ok) throw new Error('Failed to delete');
        showToast('Cache deleted');
        await loadAudiobookProfiles();
    } catch (error) {
        showToast('Error: ' + error.message);
    }
}

/** Regenerate all chunks for a cache entry */
async function handleCacheRegenerate(ebookPath, model, voice) {
    if (!confirm('Regenerate all chunks? This will overwrite existing cache.')) return;
    try {
        const res = await fetch(`${API_BASE}/stream/generate-cache?ebook_path=${encodeURIComponent(ebookPath)}&model=${encodeURIComponent(model)}&voice=${encodeURIComponent(voice)}`, {
            method: 'POST'
        });
        if (!res.ok) throw new Error('Failed to regenerate');
        showToast('Regeneration started');
        await loadAudiobookProfiles();
    } catch (error) {
        showToast('Error: ' + error.message);
    }
}

// ========== DOWNLOADS ==========

async function prepareDownload(model, voice) {
    showToast('Preparing download...');

    try {
        const res = await fetch(`${API_BASE}/stream/prepare-download?ebook_path=${encodeURIComponent(EBOOK_PATH)}&model=${model}&voice=${voice}`, {
            method: 'POST'
        });
        if (!res.ok) throw new Error('Failed to prepare download');

        const data = await res.json();
        if (data.status === 'ready') {
            showToast('Download ready!');
            downloadCombined(model, voice);
        } else {
            pollDownloadStatus(model, voice);
        }
    } catch (error) {
        showToast('Error: ' + error.message);
    }
}

async function pollDownloadStatus(model, voice) {
    const poll = async () => {
        try {
            const res = await fetch(`${API_BASE}/stream/download-status?ebook_path=${encodeURIComponent(EBOOK_PATH)}&model=${model}&voice=${voice}`);
            if (!res.ok) throw new Error('Failed to get status');

            const data = await res.json();
            if (data.status === 'ready') {
                showToast('Download ready!');
                downloadCombined(model, voice);
            } else {
                setTimeout(poll, 2000);
            }
        } catch (error) {
            console.error('[DOWNLOAD] Poll error:', error);
            setTimeout(poll, 5000);
        }
    };
    poll();
}

async function downloadCombined(model, voice) {
    const url = `${API_BASE}/stream/download?ebook_path=${encodeURIComponent(EBOOK_PATH)}&model=${model}&voice=${voice}`;
    const ebookName = EBOOK_PATH.split('/').pop().replace(/\.[^.]+$/, '');
    const safeName = ebookName.replace(/[^a-zA-Z0-9\s-]/g, '_');
    const a = document.createElement('a');
    a.href = url;
    a.download = `${safeName}.opus`;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    showToast('Download started');
}

async function downloadSource() {
    const url = `${API_BASE}/stream/download-source?ebook_path=${encodeURIComponent(EBOOK_PATH)}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = EBOOK_PATH;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    showToast('Source download started');
}

/** Switch the preferred audiobook profile */
async function switchProfile(modelVoiceKey) {
    const parts = modelVoiceKey.split('_');
    const model = parts[0];
    const voice = parts.slice(1).join('_');

    state.settings.preferred_model = model;
    state.settings.preferred_voice = voice;

    const modelSelect = document.getElementById('modelSelect');
    const voiceSelect = document.getElementById('voiceSelect');
    if (modelSelect) modelSelect.value = model;
    if (voiceSelect) updateVoiceOptions();

    showToast(`Switched to ${model}/${voice}`);
}

// ========== CACHE STATUS (in settings modal) ==========

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
                    html += `<div style="margin:2px 0;">\u2022 ${cache.model}/${cache.voice}: ${cache.files} files (${cache.size_mb} MB)</div>`;
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
