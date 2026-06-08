// stream-cache.js — Audiobook cache management for the stream page
// Depends on: stream-state.js (state, API_BASE, EBOOK_PATH, showToast, etc.)

// ========== FORMAT SELECTION MODAL + PROGRESS TRACKING ──────────────────

let currentDownloadJobId = null;
let downloadPollInterval = null;

/** Show format selection modal when user clicks "Download" */
function showFormatSelectionModal(model, voice) {
    const overlay = document.createElement('div');
    overlay.className = 'modal';  // reuse existing .modal CSS from stream.html  
    overlay.id = 'formatSelectModal';
    
    overlay.innerHTML = `
        <div class="modal-content" style="max-width:400px;">
            <h2>📥 Download Format</h2>
            <p style="font-size:13px;color:var(--text-secondary);margin-bottom:16px;">Choose your preferred format:</p>
            
            <!-- OPUS -->
            <div class="format-option" onclick="selectFormat('${model}','${voice}','opus')" 
                 style="padding:14px 16px;margin-bottom:8px;border:2px solid var(--border-color);border-radius:8px;cursor:pointer;display:flex;align-items:center;gap:12px;">
                <div>
                    <strong>OPUS (.opus)</strong><br>
                    <span style="font-size:12px;color:var(--text-secondary)">Lossless concat • Smallest size</span>
                </div>
            </div>
            
            <!-- M4B -->  
            <div class="format-option" onclick="selectFormat('${model}','${voice}','m4b')"
                 style="padding:14px 16px;margin-bottom:8px;border:2px solid var(--border-color);border-radius:8px;cursor:pointer;display:flex;align-items:center;gap:12px;">
                <div>
                    <strong>M4B with Chapters (.m4b)</strong><br>
                    <span style="font-size:12px;color:var(--text-secondary)">AAC re-encode • Chapter metadata embedded</span>
                </div>
            </div>
            
            <!-- MP3 -->
            <div class="format-option" onclick="selectFormat('${model}','${voice}','mp3')"
                 style="padding:14px 16px;margin-bottom:8px;border:2px solid var(--border-color);border-radius:8px;cursor:pointer;display:flex;align-items:center;gap:12px;">
                <div>
                    <strong>MPEG-3 (.mp3)</strong><br>  
                    <span style="font-size:12px;color:var(--text-secondary)">Re-encoded mono • Universal compatibility</span>
                </div>
            </div>
            
            <!-- Cancel -->
            <button type="button" onclick="closeFormatModal()" class="btn btn-primary" 
                    style="margin-top:8px;width:100%;padding:10px;">Cancel</button>
        </div>
    `;
    
    document.body.appendChild(overlay);
}

function closeFormatModal() {
    const el = document.getElementById('formatSelectModal');
    if (el) el.remove();
}

/** Called when user selects a format — starts conversion + shows progress */
async function selectFormat(model, voice, formatType) {
    closeFormatModal();
    
    showToast(`Starting ${formatType.toUpperCase()} download...`);
    showProgressOverlay(formatType);
    
    try {
        // 1. Start the job  
        const params = new URLSearchParams({
            ebook_path: EBOOK_PATH, model: model, voice: voice, format_type: formatType
        });
        
        const res = await fetch(`${API_BASE}/stream/download-start?${params}`, { method: 'POST' });
        
        if (!res.ok) throw new Error('Failed to start download');  
        const job = await res.json();
        
        currentDownloadJobId = job.job_id;
        
        // 2. Start polling for progress updates (every second during conversion)
        pollProgress(job.job_id);
    } catch (error) {
        hideProgressOverlay();
        showToast('Error: ' + error.message, true);
        console.error('[DOWNLOAD] Error starting download:', error);
    }
}

/** Show a progress overlay with real-time updates */
function showProgressOverlay(formatType) {
    const existing = document.getElementById('downloadProgressModal');
    if (existing) existing.remove();  // safety: remove any leftover
    
    const overlay = document.createElement('div');
    overlay.id = 'downloadProgressModal';
    
    overlay.innerHTML = `
        <div class="loading-overlay" style="display:flex;">
            <div class="loading-spinner">
                <div id="progressBarOuter" style="width:100%;margin-bottom:12px;display:none;">
                    <div style="background:var(--bg-tertiary);border-radius:4px;height:8px;overflow:hidden;margin-bottom:6px;">
                        <div id="progressBarInner" style="height:100%;width:0%;background:#ff950a;transition:width 0.3s;"></div>
                    </div>
                    <div style="display:flex;justify-content:space-between;font-size:12px;">
                        <span id="progressPct">0%</span>  
                        <span id="formatBadge">${formatType.toUpperCase()}</span>
                    </div>
                </div>
                <div class="spinner"></div>
                <div id="progressMessage" style="margin-top:12px;">Starting conversion...</div>
            </div>
        </div>
    `;
    
    document.body.appendChild(overlay);
}

/** Update the progress overlay with job data */  
function updateProgressUI(job) {
    const pctEl = document.getElementById('progressPct');
    const barInner = document.getElementById('progressBarInner');  
    const msgEl = document.getElementById('progressMessage');
    const barOuter = document.getElementById('progressBarOuter');
    
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

/** Poll download-progress endpoint every second during conversion */
function pollProgress(jobId) {
    const poll = async () => {
        try {
            const res = await fetch(`${API_BASE}/stream/download-progress/${jobId}`);  
            
            if (!res.ok) throw new Error('Failed to get progress');  
            
            const job = await res.json();
            updateProgressUI(job);
            
            // Completed or failed — stop polling, handle result  
            if (job.status === 'ready') {
                clearInterval(downloadPollInterval);
                hideProgressOverlay();
                
                showToast(`Download ready! Starting download as .${job.format_type}`);
                setTimeout(() => downloadByJobId(job.job_id), 500);
                currentDownloadJobId = null;
            } else if (job.status === 'failed') {
                clearInterval(downloadPollInterval);  
                hideProgressOverlay();
                
                const errorMsg = job.error_message || 'Conversion failed';
                showToast(`Conversion failed: ${errorMsg}`, true);
                console.log('[DOWNLOAD] Job failed:', job);
                currentDownloadJobId = null;
            }
        } catch (error) {
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

function hideProgressOverlay() {
    const el = document.getElementById('downloadProgressModal');
    if (el) el.remove();
    if (downloadPollInterval) { clearInterval(downloadPollInterval); downloadPollInterval = null; }
}


// ========== AUDIOWBOOK PANEL (in settings modal) ────────────────────────

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
                🎵 Generate Audiobook
            </button>
        `;
        return;
    }

    let html = '';

    if (caches.length > 1) {
        html += '<select id="profileSelector" onchange="switchProfile(this.value)" style="width:100%;padding:6px;margin-bottom:8px;font-size:12px;background:var(--bg-tertiary);color:var(--text-primary);border:1px solid var(--border-color);border-radius:4px;">';
        caches.forEach(c => {
            const statusIcon = c.status === 'completed' ? '🟢' : c.status === 'in_progress' ? '⏳' : c.status === 'paused' ? '⏸️' : '❌';
            html += `<option value="${c.model}_${c.voice}">${c.model} / ${c.voice} (${c.completed_chunks}/${c.total_chunks}) ${statusIcon}</option>`;
        });
        html += '</select>';
    }

    const activeCache = caches[0];
    const statusText = activeCache.status === 'completed' ? '✅ All chunks generated' :
                       activeCache.status === 'in_progress' ? `⏳ Generating... ${Math.round(activeCache.progress)}%` :
                       activeCache.status === 'paused' ? '⏸️ Paused' :
                       activeCache.status === 'failed' ? '❌ Failed' : activeCache.status;

    html += `<div style="margin-bottom:8px;font-size:12px;">`;
    html += `<strong>${statusText}</strong><br>`;
    html += `<span style="color:#666;">${activeCache.model} / ${activeCache.voice} · ${activeCache.completed_chunks}/${activeCache.total_chunks} chunks · ${activeCache.size_mb} MB</span>`;
    html += `</div>`;

    html += '<div style="display:flex;gap:6px;flex-wrap:wrap;">';

    if (activeCache.status === 'completed') {
        // CHANGED: Use format selection modal instead of direct OPUS download
        html += `<button type="button" onclick="showFormatSelectionModal('${activeCache.model}', '${activeCache.voice}')" class="btn" style="padding:4px 8px;font-size:11px;">📥 Download...</button>`;
        html += `<button type="button" onclick="downloadSource()" class="btn" style="padding:4px 8px;font-size:11px;">⬇️ Source</button>`;
        html += `<button type="button" onclick="handleCacheRegenerate('${EBOOK_PATH}', '${activeCache.model}', '${activeCache.voice}')" class="btn" style="padding:4px 8px;font-size:12px;">🔄 Regenerate</button>`;
    }

    if (activeCache.status === 'in_progress') {
        html += `<button type="button" onclick="handleCachePause('${EBOOK_PATH}', '${activeCache.model}', '${activeCache.voice}')" class="btn" style="padding:4px 8px;font-size:12px;">⏸️ Pause</button>`;
    } else if (activeCache.status === 'paused' || activeCache.status === 'failed') {
        html += `<button type="button" onclick="handleCacheResume('${EBOOK_PATH}', '${activeCache.model}', '${activeCache.voice}')" class="btn" style="padding:4px 8px;font-size:12px;">▶️ Resume</button>`;
    }

    html += `<button type="button" onclick="handleCacheDelete('${EBOOK_PATH}', '${activeCache.model}', '${activeCache.voice}')" class="btn btn-danger" style="padding:4px 8px;font-size:12px;">🗑️</button>`;
    html += '</div>';

    if (caches.length > 1) {
        html += '<div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--border-color);">';
        caches.forEach(c => {
            const icon = c.status === 'completed' ? '✅' : c.status === 'in_progress' ? '⏳' : c.status === 'paused' ? '⏸️' : '❌';
            html += `<div style="font-size:12px;padding:3px 0;">${icon} ${c.model}/${c.voice} · ${c.completed_chunks}/${c.total_chunks} · ${c.size_mb} MB</div>`;
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

// ========== CACHE OPERATIONS ────────────────────────────────────────────

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
    const res = await fetch(`${API_BASE}/stream/cache-resume?ebook_path=${encodeURIComponent(ebookPath)}&model=${model}&voice=${voice}`, {
        method: 'POST'
    });
    if (!res.ok) throw new Error('Failed to resume');
    showToast('Resuming generation...');
    await loadAudiobookProfiles();
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
        showToast('Error starting regeneration: ' + error.message);
    }
}

// ========== DOWNLOADS ───────────────────────────────────────────────────

async function downloadSource() {
    const url = `${API_BASE}/stream/download-source?ebook_path=${encodeURIComponent(EBOOK_PATH)}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = EBOOK_PATH.split('/').pop();  // Just the filename part  
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


// ========== CACHE STATUS (in settings modal) ────────────────────────────

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
                html += '<div style="font-size:12px; color:#666;">';
                data.model_voice_caches.forEach(cache => {
                    html += `<div style="margin:2px 0;">• ${cache.model}/${cache.voice}: ${cache.files} files (${cache.size_mb} MB)</div>`;
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

// ========== EXPORTS ─────────────────────────────────────────────────────

window.showFormatSelectionModal = showFormatSelectionModal;
window.closeFormatModal = closeFormatModal;
window.selectFormat = selectFormat;
window.downloadByJobId = downloadByJobId;
window.loadAudiobookProfiles = loadAudiobookProfiles;
window.startAudiobookGeneration = startAudiobookGeneration;
window.handleCachePause = handleCachePause;
window.handleCacheResume = handleCacheResume;
window.handleCacheDelete = handleCacheDelete;
window.handleCacheRegenerate = handleCacheRegenerate;
window.downloadSource = downloadSource;
window.switchProfile = switchProfile;
