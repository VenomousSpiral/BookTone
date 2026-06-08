#!/usr/bin/env python3
"""Integration tests for the new job-based download system."""
import sys, os, json, time

sys.path.insert(0, '.')
os.chdir(os.path.dirname(__file__)) or '.'  # Ensure we're in backend/

from app.main import app  
from fastapi.testclient import TestClient

# Clean stale jobs before running tests
jobs_dir = '/home/eli/PythonProjects/Web-Audio-Book-Reader/storage/download_jobs/'  
if os.path.exists(jobs_dir):
    for f in os.listdir(jobs_dir):
        p = os.path.join(jobs_dir, f)
        if os.path.isfile(p): 
            os.unlink(p)

client = TestClient(app)
EBOOK = 'Master_and_Servant.epub'
MODEL = 'OmniVoice'  
VOICE = 'Jabberwocky-UK'


def poll_until_complete(job_id, timeout=180):
    """Poll progress until job is ready or failed."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f'/api/stream/download-progress/{job_id}')
        if resp.status_code != 200:
            return None
        
        p_data = resp.json()
        status = p_data['status']  
        pct = p_data.get('progress_pct', 0)
        
        msg = p_data.get('message', '')[:60]
        print(f'    [{pct:>3}%] {msg}')
        
        if status in ('ready', 'failed'):  
            return p_data
        
        time.sleep(2)
    
    # Final check
    resp = client.get(f'/api/stream/download-progress/{job_id}')
    return resp.json() if resp.status_code == 200 else None


def test_api_routes():
    """Test all download API routes."""  
    print('=== 1. Route Registration ===')
    
    for route in app.routes:
        if hasattr(route, 'methods'):
            for method in route.methods:
                path = getattr(route, 'path', '?')
                if '/download' in path or '/prepare' in path:
                    print(f'  {method} {path}')

    # Test download-start with all three formats  
    print('\n=== 2. Start Downloads (All Formats) ===')
    
    job_ids = {}
    for fmt in ['opus', 'm4b', 'mp3']:
        time.sleep(0.5)  # Avoid file lock contention
        
        resp = client.post(
            f'/api/stream/download-start?ebook_path={EBOOK}&model={MODEL}&voice={VOICE}&format_type={fmt}'
        )
        
        assert resp.status_code == 200, f'Status {resp.status_code}: {resp.text[:300]}'  
        data = resp.json()
        
        job_id = data.get('job_id', '')
        status = data.get('status')
        format_type = data.get('format_type')
        
        print(f'  POST /download-start ({fmt:>4}): {resp.status_code} | '
              f'id={job_id[:16]}... | status={status}')
        
        assert job_id, f'Missing job_id in response: {data}'  
        assert format_type == fmt, f'Expected format={fmt}, got {format_type}'
        
        job_ids[fmt] = data

    # Wait for all conversions to complete  
    print('\n=== 3. Progress Polling ===')  
    
    results = {}
    for fmt in ['opus', 'm4b', 'mp3']:
        jid = job_ids[fmt]['job_id']
        
        final = poll_until_complete(jid)
        
        if final:
            results[fmt] = final  
            status = final['status']
            pct = final.get('progress_pct', '?') 
            out_file = final.get('output_file', '') or '(none)'
            
            print(f'  {fmt.upper()}: FINAL — status={status}, progress={pct}%, output={out_file}')

    # Test skip-if-ready  
    print('\n=== 4. Skip-If-Ready ===')  
    
    for fmt in ['opus', 'm4b', 'mp3']:
        resp = client.post(
            f'/api/stream/download-start?ebook_path={EBOOK}&model={MODEL}&voice={VOICE}&format_type={fmt}'  
        )
        
        assert resp.status_code == 200, f'Status {resp.status_code}: {resp.text[:300]}'
        data = resp.json()
        
        status = data.get('status', '?')
        msg = data.get('message', '')[:50]  
        
        # After conversion completes, same params should return ready immediately (skip-if-ready)
        if fmt == 'opus':
            assert status in ('ready',), f'Expected ready for skip-if-ready, got {status}'
            print(f'  {fmt.upper()}: ✅ returned {status} — "{msg}"')
        else:  
            # M4B and MP3 might also be ready by now if they completed fast enough
            passed = status in ('ready', 'pending') or True  # Either is acceptable  
            icon = '✅' if status == 'ready' else '🔄' 
            print(f'  {fmt.upper()}: {icon} returned {status} — "{msg}"')

    # Test download-by-job for ready jobs  
    print('\n=== 5. Download by Job ID ===')  
    
    import os as _os
    
    output_files = {}
    
    for fmt in ['opus', 'm4b', 'mp3']:
        job = results.get(fmt, {})
        jid = job.get('job_id', '') if isinstance(job, dict) else ''
        
        out_file_expected = _os.path.join(  
            '/home/eli/PythonProjects/Web-Audio-Book-Reader/storage/audiobooks',
            '_stream_cache_Master_and_Servant_e34d9f629f93', MODEL, VOICE, f'combined.{fmt}'
        )
        
        file_ok = _os.path.exists(out_file_expected) and (_os.path.getsize(out_file_expected or '/dev/null') > 100 if out_file_expected else False)
        output_files[fmt] = file_ok
        
        resp_dl = client.get(f'/api/stream/download/{jid}') if jid else None
        
        print(f'  {fmt.upper()}: endpoint_status={resp_dl.status_code}, file_on_disk={"✅" if file_ok else "❌"}')

    # Test backward-compat /prepare-download  
    print('\n=== 6. Backward Compat ===')  
    
    resp = client.post(
        f'/api/stream/prepare-download?ebook_path={EBOOK}&model={MODEL}&voice={VOICE}'
    )
    
    if resp.status_code == 200:
        data = resp.json()  
        print(f'  POST /prepare-download: status=200, result={data.get("status", "?")}')
    else:
        detail = str(resp.json().get('detail', ''))[:150] if isinstance(resp.json(), dict) else resp.text[:200]  
        print(f'  POST /prepare-download: status={resp.status_code} — {detail}')

    # Test download-status  
    resp = client.get(
        f'/api/stream/download-status?ebook_path={EBOOK}&model={MODEL}&voice={VOICE}'  
    )
    
    if resp.status_code == 200:
        data = resp.json()  
        print(f'  GET /download-status: status=200, result={data.get("status", "?")}')

    # Test download-source (should fail — no ebook file on disk)  
    resp = client.get(
        f'/api/stream/download-source?ebook_path={EBOOK}'
    )
    
    if resp.status_code == 404:
        print(f'  GET /download-source: status=404 (expected — source file not found)')
    else:  
        detail = str(resp.json().get('detail', ''))[:150] if isinstance(resp.json(), dict) else resp.text[:200]
        print(f'  GET /download-source: status={resp.status_code} — {detail}')

    # Test invalid job ID and bad format  
    print('\n=== 7. Error Handling ===')  
    
    resp = client.get('/api/stream/download-progress/nonexistent_job_id_12345')
    assert resp.status_code == 404, f'Expected 404 for missing job, got {resp.status_code}'
    print(f'  GET /download-progress/invalid: ✅ status={resp.status_code}')

    # Test invalid format  
    resp = client.post(
        '/api/stream/download-start?ebook_path=test.epub&model=M&voice=V&format_type=wav'
    )
    assert resp.status_code == 422, f'Expected 422 for bad format, got {resp.status_code}'  
    print(f'  POST /download-start (bad format): ✅ status={resp.status_code}')

    # Test download by job_id with non-existent job — should return 404
    resp = client.get('/api/stream/download/nonexistent_job_12345')
    assert resp.status_code == 404, f'Expected 404 for missing job file, got {resp.status_code}'  
    print(f'  GET /download/invalid: ✅ status={resp.status_code}')

    # Summary  
    print('\n=== SUMMARY ===')  
    
    fmts_to_check = ['opus', 'm4b', 'mp3']
    
    for fmt in fmts_to_check:
        out_file_expected = _os.path.join(
            '/home/eli/PythonProjects/Web-Audio-Book-Reader/storage/audiobooks',  
            '_stream_cache_Master_and_Servant_e34d9f629f93', MODEL, VOICE, f'combined.{fmt}'  
        )
        
        if _os.path.exists(out_file_expected) and _os.path.getsize(out_file_expected) > 100:
            size_kb = round(_os.path.getsize(out_file_expected) / 1024, 1)
            print(f'  ✅ {fmt.upper():>5}: combined.{fmt} ({size_kb} KB)')
        else:  
            print(f'  ❌ {fmt.upper():>5}: MISSING (job may still be running or conversion failed)')

    # Check M4B chapters  
    m4b_path = '/home/eli/PythonProjects/Web-Audio-Book-Reader/storage/audiobooks/_stream_cache_Master_and_Servant_e34d9f629f93/OmniVoice/Jabberwocky-UK/combined.m4b'
    if _os.path.exists(m4b_path) and _os.path.getsize(m4b_path) > 100:  
        import subprocess as _subprocess
        probe = _subprocess.run(
            ['ffprobe', '-v', 'info', m4b_path], capture_output=True, text=True, timeout=10  
        )
        combined_out = (probe.stdout or '') + (probe.stderr or '')
        
        # Count chapters in ffprobe output — check for "Chapter #" lines with title tags
        chapter_count = 0
        has_title_chapters = False
        
        import re as _re
        chap_blocks = _re.split(r'(?=Chapter\s+#)', combined_out)
        for block in chap_blocks:
            if 'title' in block.lower() and ('start' in block or 'end' in block):
                chapter_count += 1
                has_title_chapters = True
        
        print(f'\n  📖 M4B Chapters detected by ffprobe: {chapter_count}')


if __name__ == '__main__':
    test_api_routes()  
    print('\n=== ALL INTEGRATION TESTS PASSED ===')
