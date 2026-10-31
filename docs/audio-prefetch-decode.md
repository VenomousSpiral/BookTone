# Low-Latency Audio Chunk Skipping

## Problem

The frontend caches generated audio as `Blob` objects in `state.audioCache`. This prevents a second network request when a prefetched chunk is selected, but it does not cache the browser's decoded media state.

When a cached chunk is selected, the current playback flow still:

1. Retrieves the encoded `audio/opus` blob from `state.audioCache`.
2. Creates a new `blob:` URL.
3. Assigns that URL to the `<audio>` element.
4. Waits for the browser to demux and decode the Opus data.
5. Starts playback after the media element reaches `playing`.

Audio traces show that the cache lookup is nearly immediate, while the delay occurs between `playback-audio-src-set` and `audio-playing-event`. Observed delays have been approximately 600–700 ms even when `targetCacheHit` is `true` and no audio request is made.

## Proposed fix

Cache both representations of each chunk:

- The encoded `Blob`, for compatibility and fallback playback.
- A decoded Web Audio `AudioBuffer`, for low-latency playback.

Prefetch should fetch the blob once, then decode it before marking the chunk ready. A skip to a decoded chunk can then use the already-decoded samples instead of assigning a new Opus blob to the HTML audio element.

## Suggested architecture

### 1. Add a decoded-audio cache

Add a cache alongside `state.audioCache`:

```js
audioBufferCache: new Map()
```

Use the same content-hash cache key as the encoded blob cache. The decoded cache must be cleared whenever the encoded cache is cleared or the book/settings that affect audio identity change.

### 2. Create one shared `AudioContext`

Create the context lazily in the audio module:

```js
let audioContext;

function getAudioContext() {
    if (!audioContext) {
        audioContext = new AudioContext();
    }
    return audioContext;
}
```

The context must be resumed from a user gesture, such as the first play or chunk click. Browsers may keep an `AudioContext` suspended until that happens.

### 3. Decode during prefetch

After a prefetch receives a blob:

```js
const arrayBuffer = await blob.arrayBuffer();
const decoded = await getAudioContext().decodeAudioData(arrayBuffer);
state.audioBufferCache.set(cacheKey, decoded);
```

The prefetch should only be considered complete after decoding succeeds. Playback should also be able to join an in-flight fetch/decode promise rather than starting duplicate work.

The request state should therefore represent the whole pipeline:

```text
fetch -> blob -> arrayBuffer -> decodeAudioData -> decoded cache
```

### 4. Play decoded chunks with Web Audio

For a chunk present in `audioBufferCache`:

1. Stop the active `AudioBufferSourceNode`.
2. Create a new source node.
3. Assign the cached `AudioBuffer`.
4. Connect it to `audioContext.destination`.
5. Start it immediately.

An `AudioBufferSourceNode` is single-use, so a new source node must be created for every playback, even when reusing the same decoded buffer.

Track the source's start time and duration using `AudioContext.currentTime`. Use the source `ended` event to transition to the next chunk.

## Compatibility strategy

The existing HTML `<audio>` implementation should remain as a fallback initially:

- Use Web Audio when a decoded buffer is available.
- Fall back to the current blob URL path when decoding is unavailable or fails.
- Keep the encoded blob cache so playback can recover without another request.
- Preserve the current error handling and user-visible playback controls.

This allows unsupported browsers, suspended contexts, and decode failures to retain existing behavior.

## Important implementation concerns

### Playback controls

Web Audio does not provide an HTML media element's `currentTime` or `duration`. The frontend will need to calculate:

```text
elapsed = audioContext.currentTime - sourceStartedAt
```

The existing progress and chunk-transition handlers should be adapted to use this value for Web Audio playback.

### Pause and resume

An `AudioBufferSourceNode` cannot be resumed after stopping. Pause behavior must store the elapsed offset and create a new source node on resume, starting at that offset.

### Playback speed

Set `source.playbackRate.value` from the existing speed control. Progress and remaining-duration calculations must account for the playback rate.

### Memory usage

Decoded audio consumes more memory than the encoded blob. Keep the existing cache bounds and evict both representations together. A practical first implementation is to decode only the forward prefetch window and retain a smaller number of decoded buffers than blobs.

### Cache identity

The decoded key must include every setting that changes the audio bytes, or use the existing content hash only when that hash is guaranteed to represent the selected voice/model/audio version. Otherwise, stale decoded audio may be reused after a voice or model change.

### Context lifecycle

Handle these cases explicitly:

- `AudioContext.state === 'suspended'`: call `resume()` after a user gesture.
- `decodeAudioData` failure: log the error and use HTML audio fallback.
- Context closure: recreate it and decode as needed.
- Page visibility changes: preserve or safely rebuild active playback state.

## Diagnostics and validation

Retain the existing `[AUDIO TRACE]` diagnostics and add events for:

```text
audio-decode-start
audio-decode-finished
decoded-cache-hit
web-audio-source-started
web-audio-source-ended
web-audio-fallback
```

Measure these intervals:

1. User chunk click to old audio stop.
2. Old audio stop to new source start.
3. Cache lookup to decoded buffer availability.
4. Decoded source start to audible playback.

Acceptance criteria:

- A prefetched and decoded adjacent chunk starts materially faster than the current blob URL path.
- No additional `/stream/audio` request occurs for a decoded cache hit.
- Chunk progress, pause/resume, speed changes, automatic transitions, and manual jumps remain correct.
- Decode failures still play through the existing HTML audio fallback.
- Cache eviction removes both blobs and decoded buffers.

## Recommended rollout

1. Add decoded-cache state and shared fetch/decode promises.
2. Decode prefetched chunks while preserving the current player.
3. Add a Web Audio playback path for manual skips only.
4. Validate timing and controls using the trace events.
5. Move automatic chunk transitions to Web Audio after manual skips are stable.
6. Tune decoded-cache size after measuring memory usage.

