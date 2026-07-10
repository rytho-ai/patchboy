"""Render an audition board — one self-contained HTML page for comparing
preview clips side by side.

Everything is inlined (audio as base64 data URIs, CSS in a <style>), so the
page works anywhere: a local browser, a claude.ai artifact, a remote box —
no external requests, no dependency on the client rendering raw audio files.
Theme-aware via `light-dark()`.
"""

from __future__ import annotations

import base64
import html

from .models import Sample


def _row(index: int, sample: Sample, mp3: bytes) -> str:
    b64 = base64.b64encode(mp3).decode("ascii")
    name = html.escape(sample.name.split("/")[-1])
    pack = html.escape(sample.pack_name or "")
    meta_bits = [
        b for b in (
            html.escape(sample.asset_type),
            f"{sample.bpm} BPM" if sample.bpm else "",
            html.escape(sample.key.upper()) if sample.key else "",
        ) if b
    ]
    meta = " · ".join(meta_bits)
    # catalog_uuid is what download/buy actually need — never the search
    # hash `uuid`, which those commands can't resolve.
    uuid = html.escape(sample.catalog_uuid or "?")
    return f"""
    <div class="row">
      <div class="idx">{index}</div>
      <div class="info">
        <div class="name">{name}</div>
        <div class="meta">{meta}{(' · ' + pack) if pack else ''}</div>
        <div class="player">
          <audio controls preload="none" loop src="data:audio/mpeg;base64,{b64}"></audio>
          <button class="loop on" title="Loop on/off" aria-pressed="true">🔁</button>
        </div>
        <button class="uuid" data-uuid="{uuid}" title="Click to copy — pass to `splice download`/`splice buy`">{uuid}</button>
      </div>
    </div>"""


def audition_html(query: str, items: list[tuple[Sample, bytes]]) -> str:
    """Build the full audition page for a query and its (sample, mp3) pairs."""
    rows = "\n".join(_row(i + 1, s, mp3) for i, (s, mp3) in enumerate(items))
    q = html.escape(query)
    return f"""<title>Audition: {q}</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 0; padding: 1.5rem;
    background: light-dark(#faf8f5, #14120f);
    color: light-dark(#1c1a17, #ece8e1);
  }}
  header {{ max-width: 760px; margin: 0 auto 1.25rem; }}
  h1 {{ font-size: 1.15rem; margin: 0; }}
  .sub {{ color: light-dark(#8a8478, #8f8879); font-size: 0.85rem; margin-top: 0.2rem; }}
  .board {{ max-width: 760px; margin: 0 auto; display: flex; flex-direction: column; gap: 0.75rem; }}
  .row {{
    display: flex; gap: 0.9rem; align-items: flex-start;
    background: light-dark(#ffffff, #1e1b17);
    border: 1px solid light-dark(#eae4d9, #322d26);
    border-radius: 12px; padding: 0.9rem 1rem;
  }}
  .idx {{
    flex: none; width: 1.75rem; height: 1.75rem; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.85rem; font-weight: 600;
    background: light-dark(#f0ebe2, #2a2620); color: light-dark(#6b6558, #b0a894);
  }}
  .info {{ flex: 1; min-width: 0; }}
  .name {{ font-weight: 600; font-size: 0.95rem; word-break: break-word; }}
  .meta {{ color: light-dark(#8a8478, #8f8879); font-size: 0.8rem; margin: 0.15rem 0 0.6rem; }}
  .player {{ display: flex; align-items: center; gap: 0.5rem; }}
  audio {{ flex: 1; min-width: 0; height: 34px; }}
  .loop {{
    flex: none; border: 1px solid light-dark(#eae4d9, #322d26); border-radius: 8px;
    background: none; cursor: pointer; font-size: 0.95rem; line-height: 1;
    padding: 0.35rem 0.5rem; opacity: 0.35; filter: grayscale(1);
  }}
  .loop.on {{ opacity: 1; filter: none; background: light-dark(#f0ebe2, #2a2620); }}
  .uuid {{
    margin-top: 0.5rem; font-family: ui-monospace, monospace; font-size: 0.7rem;
    color: light-dark(#a29a8c, #6f685c); background: none; border: none;
    padding: 0; cursor: pointer; text-align: left; word-break: break-all;
  }}
  .uuid:hover {{ color: light-dark(#6b6558, #b0a894); }}
  .uuid.copied::after {{ content: " ✓ copied"; color: #4a9d5b; }}
</style>
<header>
  <h1>Audition: "{q}"</h1>
  <div class="sub">{len(items)} preview clips · click a uuid to copy it, then tell me which to buy</div>
</header>
<div class="board">
{rows}
</div>
<script>
  document.querySelectorAll('.uuid').forEach(function (b) {{
    b.addEventListener('click', function () {{
      navigator.clipboard.writeText(b.dataset.uuid);
      b.classList.add('copied');
      setTimeout(function () {{ b.classList.remove('copied'); }}, 1500);
    }});
  }});
  document.querySelectorAll('.loop').forEach(function (btn) {{
    var audio = btn.parentElement.querySelector('audio');
    btn.addEventListener('click', function () {{
      audio.loop = !audio.loop;
      btn.classList.toggle('on', audio.loop);
      btn.setAttribute('aria-pressed', audio.loop);
    }});
  }});
</script>"""
