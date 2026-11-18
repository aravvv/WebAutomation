from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from config.settings import DOWNLOADS_DIR, RENDERS_DIR
from core.analyzer.moment_detector import CrazyMoment, CrazyMomentDetector
from core.ingest.youtube_downloader import YouTubeDownloader
from core.publishers.quota_tracker import QuotaAndClipTracker
from core.publishers.youtube_publisher import YouTubeShortsPublisher
from core.video.short_compositor import ShortCompositor

app = FastAPI(title="GTA 6 Viral Shorts Clipper & Auto-Publisher Studio")
tracker = QuotaAndClipTracker()
publisher = YouTubeShortsPublisher(tracker=tracker)
downloader = YouTubeDownloader()
detector = CrazyMomentDetector()
compositor = ShortCompositor()


class ClipRequest(BaseModel):
    url: str = "https://youtu.be/rQLEb0W7REo"
    top_n: int = 2
    clip_duration: float = 28.0
    audio_mode: str = "original"


class ReburnHookRequest(BaseModel):
    clip_id: str
    custom_hook: str
    selected_title: Optional[str] = None


class PublishRequest(BaseModel):
    clip_id: str
    selected_hook: Optional[str] = None
    selected_title: Optional[str] = None
    privacy_status: str = "public"
    dry_run: bool = False


@app.get("/api/status")
def get_status():
    return {
        "quota": tracker.get_remaining_quota(),
        "clips": tracker.list_clips(),
    }


@app.post("/api/clip")
def trigger_clip_job(req: ClipRequest):
    import traceback
    from fastapi.responses import JSONResponse

    try:
        top_n = max(1, min(10, int(req.top_n)))
        clip_dur = max(10.0, min(58.0, float(req.clip_duration)))
        ingested = downloader.ingest(req.url.strip(), use_demo_fallback=False)
        moments = detector.detect_top_moments(ingested, top_n=top_n, clip_duration=clip_dur)
        rendered_ids = []
        for m in moments:
            short = compositor.render_short(ingested, m, audio_mode=req.audio_mode)
            tracker.save_rendered_short(short, source_url=ingested.source_url)
            rendered_ids.append(short.clip_id)
        return {
            "status": "ok",
            "video_title": ingested.title,
            "rendered_clip_ids": rendered_ids,
            "clips": tracker.list_clips(),
        }
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "details": traceback.format_exc()},
        )


@app.post("/api/reburn")
def reburn_clip_hook(req: ReburnHookRequest):
    import traceback
    from fastapi.responses import JSONResponse

    try:
        clip = tracker.get_clip(req.clip_id)
        if not clip:
            raise HTTPException(status_code=404, detail=f"Clip {req.clip_id} not found")

        ingested = downloader.ingest(clip["source_url"], use_demo_fallback=True)
        moment = CrazyMoment(
            rank=1,
            start_time=float(clip["start_time"]),
            end_time=float(clip["end_time"]),
            viral_score=float(clip["viral_score"]),
            category="action_peak",
            reason=str(clip["reason"]),
            transcript_excerpt="",
        )
        short = compositor.render_short(
            video=ingested,
            moment=moment,
            audio_mode=clip["audio_mode"],
            custom_hook_text=req.custom_hook,
        )
        target_path = Path(clip["output_path"])
        if short.output_path != target_path and short.output_path.exists():
            target_path.write_bytes(short.output_path.read_bytes())

        new_title = req.selected_title or f"{req.custom_hook} #gta6 #shorts"
        tracker.update_clip_selection(req.clip_id, req.custom_hook, new_title)
        return {"status": "ok", "clip_id": req.clip_id, "hook": req.custom_hook}
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "details": traceback.format_exc()},
        )


@app.post("/api/publish")
def publish_clip(req: PublishRequest):
    clip = tracker.get_clip(req.clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail=f"Clip {req.clip_id} not found")

    hook = req.selected_hook or clip["primary_hook"]
    title = req.selected_title or clip["primary_title"]
    tracker.update_clip_selection(req.clip_id, hook, title)

    res = publisher.publish_short(
        clip_id=req.clip_id,
        video_path=Path(clip["output_path"]),
        title=title,
        description=clip["description"],
        tags=clip["youtube_tags"],
        privacy_status=req.privacy_status,
        dry_run=req.dry_run,
    )
    return res


@app.get("/media/{filename}")
def serve_rendered_video(filename: str):
    target = (RENDERS_DIR / filename).resolve()
    if not target.exists() or target.parent != RENDERS_DIR.resolve():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(str(target), media_type="video/mp4")


@app.get("/download/{filename}")
def download_rendered_video(filename: str):
    target = (RENDERS_DIR / filename).resolve()
    if not target.exists() or target.parent != RENDERS_DIR.resolve():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(str(target), media_type="video/mp4", filename=filename)


@app.get("/", response_class=HTMLResponse)
def studio_index():
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>GTA 6 Viral Shorts Clipper & Auto-Publisher Studio</title>
  <style>
    :root {
      --bg: #0b0914;
      --card: #151226;
      --card-alt: #1d1836;
      --border: #2c244d;
      --pink: #ff2d91;
      --gold: #ffe600;
      --cyan: #00f0ff;
      --green: #00e676;
      --text: #f3f1ff;
      --muted: #9e96c7;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; }
    body { background: var(--bg); color: var(--text); padding: 24px; max-width: 1400px; margin: 0 auto; }
    header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 22px; padding-bottom: 16px; border-bottom: 1px solid var(--border); flex-wrap: wrap; gap: 14px; }
    .brand h1 { font-size: 1.65rem; color: var(--gold); font-weight: 900; letter-spacing: 0.4px; }
    .brand p { color: var(--muted); font-size: 0.9rem; margin-top: 4px; }
    .quota-pill { background: var(--card); border: 1px solid var(--border); padding: 10px 16px; border-radius: 12px; font-size: 0.85rem; }
    .quota-pill strong { color: var(--gold); }

    /* Generator Form Panel */
    .generator-panel { background: linear-gradient(145deg, var(--card), var(--card-alt)); border: 1px solid var(--pink); border-radius: 18px; padding: 20px; margin-bottom: 28px; box-shadow: 0 8px 30px rgba(255, 45, 145, 0.12); }
    .generator-title { font-size: 0.95rem; font-weight: 800; color: var(--gold); margin-bottom: 14px; text-transform: uppercase; letter-spacing: 0.6px; }
    .form-row { display: grid; grid-template-columns: 1fr 150px 150px 230px auto; gap: 12px; align-items: end; }
    @media (max-width: 980px) { .form-row { grid-template-columns: 1fr 1fr; } }
    .field label { display: block; font-size: 0.76rem; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; font-weight: 700; }
    .field input, .field select { width: 100%; background: #0e0b1a; border: 1px solid var(--border); color: var(--text); padding: 11px 13px; border-radius: 10px; font-size: 0.92rem; }
    .btn { background: linear-gradient(90deg, var(--pink), #ff6a00); color: #fff; border: none; padding: 12px 22px; border-radius: 10px; font-weight: 800; cursor: pointer; font-size: 0.92rem; transition: opacity 0.15s; white-space: nowrap; }
    .btn:hover { opacity: 0.92; }
    .btn:disabled { opacity: 0.5; cursor: wait; }
    .btn-secondary { background: #261f47; border: 1px solid var(--border); padding: 9px 14px; font-size: 0.82rem; border-radius: 8px; color: var(--cyan); font-weight: 700; cursor: pointer; text-decoration: none; display: inline-block; }
    .btn-publish { background: linear-gradient(90deg, #00c853, #00bfa5); width: 100%; margin-top: 12px; padding: 12px; }

    /* Progress & Error Banners */
    #jobStatus { display: none; margin-top: 14px; padding: 12px 16px; border-radius: 10px; background: rgba(0, 240, 255, 0.1); border: 1px solid var(--cyan); color: var(--cyan); font-weight: 700; font-size: 0.9rem; }
    #errorBanner { display: none; margin-top: 14px; padding: 12px 16px; border-radius: 10px; background: rgba(255, 45, 145, 0.15); border: 1px solid var(--pink); color: #ff80b5; font-weight: 700; font-size: 0.88rem; white-space: pre-wrap; }

    /* Clips Grid */
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(620px, 1fr)); gap: 22px; }
    .clip-card { background: var(--card); border: 1px solid var(--border); border-radius: 18px; padding: 20px; display: grid; grid-template-columns: 245px 1fr; gap: 20px; }
    @media (max-width: 680px) { .clip-card { grid-template-columns: 1fr; } }
    .video-col { display: flex; flex-direction: column; gap: 10px; align-items: center; }
    .video-wrap { width: 245px; aspect-ratio: 9 / 16; background: #000; border-radius: 14px; overflow: hidden; border: 2px solid var(--pink); position: relative; }
    .video-wrap video { width: 100%; height: 100%; object-fit: cover; }
    .badge-row { display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 10px; }
    .tag-pill { font-size: 0.74rem; font-weight: 700; padding: 4px 10px; border-radius: 999px; background: #231d42; color: var(--cyan); }
    .score-pill { background: rgba(255, 230, 0, 0.15); color: var(--gold); }
    .reason { font-size: 0.83rem; color: var(--muted); margin-bottom: 12px; line-height: 1.4; }
    label.sec-lbl { display: block; font-size: 0.75rem; text-transform: uppercase; color: var(--muted); margin: 10px 0 5px; font-weight: 700; }
    select.full, input.full { width: 100%; background: #0e0b1a; border: 1px solid var(--border); color: var(--text); padding: 9px 11px; border-radius: 8px; font-size: 0.85rem; }
    .hook-edit-row { display: flex; gap: 8px; margin-top: 6px; }
    .hashtags-box { background: #0e0b1a; border: 1px solid var(--border); padding: 9px 11px; border-radius: 8px; font-size: 0.79rem; color: var(--cyan); line-height: 1.45; }
    .status-banner { margin-top: 8px; font-size: 0.82rem; color: var(--green); font-weight: 700; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <h1>GTA VI VIRAL SHORTS CLIPPER & AUTO-PUBLISHER</h1>
      <p>Millisecond WebVTT + VAD Karaoke Subtitles • True Full-Bleed 9:16 Portrait (1080x1920) • Crazy Hooks & Hashtags</p>
    </div>
    <div class="quota-pill" id="quotaBox">Loading free YouTube API quota...</div>
  </header>

  <section class="generator-panel">
    <div class="generator-title">⚡ Paste Any GTA 6 YouTube Video Link & Choose How Many 9:16 Clips You Want</div>
    <div class="form-row">
      <div class="field">
        <label>YouTube Video Link (or local file)</label>
        <input id="urlInput" type="text" placeholder="https://youtu.be/rQLEb0W7REo" value="https://youtu.be/rQLEb0W7REo" />
      </div>
      <div class="field">
        <label>How Many Clips?</label>
        <input id="topNInput" type="number" min="1" max="10" value="2" />
      </div>
      <div class="field">
        <label>Clip Length (sec)</label>
        <select id="durationSelect">
          <option value="20">20 seconds</option>
          <option value="28" selected>28 seconds</option>
          <option value="40">40 seconds</option>
          <option value="55">55 seconds</option>
        </select>
      </div>
      <div class="field">
        <label>Audio & Subtitle Mode</label>
        <select id="modeSelect">
          <option value="original">Original Audio + Synced Subtitles</option>
          <option value="ai-voiceover">AI Hype Voiceover + Ducked Audio</option>
        </select>
      </div>
      <button class="btn" id="clipBtn" onclick="runClipper()">🔥 Make My 9:16 Clips</button>
    </div>
    <div id="jobStatus">⏳ Downloading video, aligning millisecond karaoke subtitles, and cropping crazy moments to 9:16 (1080x1920)...</div>
    <div id="errorBanner"></div>
  </section>

  <div class="grid" id="clipsGrid"></div>

  <script>
    async function loadDashboard() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        const q = data.quota;
        document.getElementById('quotaBox').innerHTML =
          `⚡ Local 9:16 Clipping: <strong>UNLIMITED (100% Free)</strong> &nbsp;|&nbsp; Auto-Upload API: <strong>${q.uploads_remaining_today}</strong>/day`;
        renderClips(data.clips);
      } catch (err) {
        const errBox = document.getElementById('errorBanner');
        errBox.style.display = 'block';
        errBox.textContent = 'Error loading studio status: ' + err;
      }
    }

    function renderClips(clips) {
      const grid = document.getElementById('clipsGrid');
      if (!clips || clips.length === 0) {
        grid.innerHTML = '<p style="color:var(--muted);">No clips rendered yet. Paste a YouTube URL above and click "Make My 9:16 Clips"!</p>';
        return;
      }
      const cacheBust = Date.now();
      grid.innerHTML = clips.map(c => {
        const filename = c.filename || c.output_path.split(/[\\/]/).pop();
        const hookOpts = (c.hook_options || [c.primary_hook]).map(h =>
          `<option value="${h.replace(/"/g, '&quot;')}" ${h === c.primary_hook ? 'selected' : ''}>${h}</option>`
        ).join('');
        const titleOpts = (c.title_options || [c.primary_title]).map(t =>
          `<option value="${t.replace(/"/g, '&quot;')}" ${t === c.primary_title ? 'selected' : ''}>${t}</option>`
        ).join('');
        const statusHtml = c.status === 'published'
          ? `<div class="status-banner">✅ PUBLISHED TO YOUTUBE SHORTS (${c.youtube_video_id})</div>`
          : '';
        return `
          <div class="clip-card">
            <div class="video-col">
              <div class="video-wrap">
                <video id="vid_${c.clip_id}" src="/media/${filename}?t=${cacheBust}" controls loop playsinline></video>
              </div>
              <a class="btn-secondary" href="/download/${filename}" download>⬇️ Download 9:16 MP4</a>
            </div>
            <div>
              <div class="badge-row">
                <span class="tag-pill score-pill">🔥 Score: ${c.viral_score}/100</span>
                <span class="tag-pill">📐 1080x1920 Full 9:16</span>
                <span class="tag-pill">⏱️ ${c.start_time}s – ${c.end_time}s</span>
                <span class="tag-pill">🎙️ ${c.audio_mode}</span>
              </div>
              <div class="reason">${c.reason}</div>

              <label class="sec-lbl">1. Suggested Crazy Hook Captions (or type custom below)</label>
              <select class="full" id="hookSel_${c.clip_id}" onchange="document.getElementById('hookCustom_${c.clip_id}').value=this.value">${hookOpts}</select>
              <div class="hook-edit-row">
                <input class="full" id="hookCustom_${c.clip_id}" value="${c.primary_hook.replace(/"/g, '&quot;')}" />
                <button class="btn-secondary" onclick="reburnHook('${c.clip_id}')">🔄 Re-Burn Hook</button>
              </div>

              <label class="sec-lbl">2. Suggested YouTube Short Title</label>
              <select class="full" id="title_${c.clip_id}">${titleOpts}</select>

              <label class="sec-lbl">3. Viral GTA 6 Hashtags</label>
              <div class="hashtags-box">${(c.hashtags || []).join(' ')}</div>

              <button class="btn btn-publish" onclick="publishClip('${c.clip_id}')">
                🚀 Approve & Post to YouTube Shorts
              </button>
              <div id="msg_${c.clip_id}">${statusHtml}</div>
            </div>
          </div>
        `;
      }).join('');
    }

    async function runClipper() {
      const btn = document.getElementById('clipBtn');
      const statusBox = document.getElementById('jobStatus');
      const errBox = document.getElementById('errorBanner');
      btn.disabled = true;
      btn.textContent = '⏳ Making Clips...';
      statusBox.style.display = 'block';
      errBox.style.display = 'none';
      try {
        const res = await fetch('/api/clip', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            url: document.getElementById('urlInput').value,
            top_n: parseInt(document.getElementById('topNInput').value, 10) || 2,
            clip_duration: parseFloat(document.getElementById('durationSelect').value) || 28.0,
            audio_mode: document.getElementById('modeSelect').value
          })
        });
        if (!res.ok) {
          const errText = await res.text();
          throw new Error(errText);
        }
        await res.json();
        await loadDashboard();
      } catch (err) {
        errBox.style.display = 'block';
        errBox.textContent = 'Clipping failed: ' + err.message;
      } finally {
        btn.disabled = false;
        btn.textContent = '🔥 Make My 9:16 Clips';
        statusBox.style.display = 'none';
      }
    }

    async function reburnHook(clipId) {
      const customHook = document.getElementById(`hookCustom_${clipId}`).value;
      const title = document.getElementById(`title_${clipId}`).value;
      const msgEl = document.getElementById(`msg_${clipId}`);
      msgEl.innerHTML = '<div class="status-banner" style="color:var(--gold);">Re-burning hook banner & millisecond subtitles...</div>';
      await fetch('/api/reburn', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ clip_id: clipId, custom_hook: customHook, selected_title: title })
      });
      await loadDashboard();
    }

    async function publishClip(clipId) {
      const hook = document.getElementById(`hookCustom_${clipId}`).value;
      const title = document.getElementById(`title_${clipId}`).value;
      const msgEl = document.getElementById(`msg_${clipId}`);
      msgEl.innerHTML = '<div class="status-banner" style="color:var(--gold);">Uploading to YouTube Shorts...</div>';
      const res = await fetch('/api/publish', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          clip_id: clipId,
          selected_hook: hook,
          selected_title: title,
          privacy_status: 'public'
        })
      });
      const data = await res.json();
      msgEl.innerHTML = `<div class="status-banner">✅ Ready on YouTube Shorts: ${data.shorts_url} (${data.mode})</div>`;
      await loadDashboard();
    }

    loadDashboard();
  </script>
</body>
</html>
"""
