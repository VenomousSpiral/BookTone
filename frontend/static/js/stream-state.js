// Shared state, constants, DOM helpers, and utilities
// This file loads FIRST - all other stream modules depend on it

const API_BASE = '/api';

// ========== NO-OP LOGGING (F-1) ==========
const _log = () => {};  // no-op for console.log
const _warn = () => {}; // no-op for console.warn
// KEEP console.error(...) — these are for real errors only

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
    loadingChunks: new Set(),
    progressMode: 'book',
    timeMode: 'total',
    showImages: false,
    imageCache: new Map(),
    touch: { startX: 0, startY: 0, startTime: 0 },
    scroll: {
        timeout: null,
        autoInProgress: false,
        previousChunk: -1,
        lastManual: 0
    },
    chunkObserver: null,
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
const LOAD = { INITIAL: 50, RADIUS: 150, BATCH: 100, CLEANUP_MULT: 3 };
const SCROLL = { THRESHOLD_MULT: 6, DEBOUNCE: 16, SCROLL_DELAY: 600, MANUAL_TIMEOUT: 2000 };

// ========== DOM HELPERS ==========
const DOM = {
    get audio() { return document.getElementById('audioPlayer'); },
    get playBtn() { return document.getElementById('playButton'); },
    get textDisplay() { return document.getElementById('textDisplay'); },
    get speedControl() { return document.getElementById('speedControl'); },
    get bookTitle() { return document.getElementById('bookTitle'); },
    get totalProgress() { return document.getElementById('totalProgress'); },
    get currentPosition() { return document.getElementById('currentPosition'); },
    get progressBar() { return document.getElementById('progressBar'); },
    get timeEstimate() { return document.getElementById('timeEstimate'); },
    get loadingOverlay() { return document.getElementById('loadingOverlay'); },
    modal: (type) => document.getElementById(`${type}Modal`),
    modalList: (type) => document.getElementById(`${type}List`)
};

// ========== UTILITY FUNCTIONS ==========
const log = (msg, data = '') => _log(`[STREAM] ${msg}`, data);
const logCache = (msg, data = '') => _log(`[STREAM CACHE] ${msg}`, data);
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
