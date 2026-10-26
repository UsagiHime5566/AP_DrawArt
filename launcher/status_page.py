"""無法正常開展時，在展示視窗顯示的本機狀態頁。"""

from __future__ import annotations

import html
from pathlib import Path


def render_status_html(title: str, message: str, details: list[str], admin_urls: list[str]) -> str:
    detail_items = "".join(f"<li>{html.escape(item)}</li>" for item in details)
    url_items = "".join(f"<li>{html.escape(url)}</li>" for url in admin_urls)
    admin_block = f"<h2>後臺網址</h2><ul>{url_items}</ul>" if url_items else ""
    detail_block = f"<ul class=\"details\">{detail_items}</ul>" if detail_items else ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="10">
<title>人工對畫(話) · 準備中</title>
<style>
  html, body {{ margin: 0; height: 100%; background: #000; color: #fff; cursor: none;
    font-family: "Noto Sans TC", "Microsoft JhengHei", system-ui, sans-serif; }}
  main {{ box-sizing: border-box; height: 100%; padding: 8vh 8vw; display: flex;
    flex-direction: column; justify-content: center; gap: 2vh; }}
  h1 {{ margin: 0; font-size: 5vh; }}
  p {{ margin: 0; font-size: 3vh; color: #ddd; }}
  h2 {{ margin: 2vh 0 0; font-size: 2.4vh; color: #aaa; }}
  ul {{ margin: 0; padding-left: 1.2em; font-size: 2.4vh; line-height: 1.6; }}
  .details {{ color: #ffd400; }}
</style>
</head>
<body>
<main>
  <h1>{html.escape(title)}</h1>
  <p>{html.escape(message)}</p>
  {detail_block}
  {admin_block}
</main>
</body>
</html>
"""


def write_status_page(path: Path, title: str, message: str, details: list[str], admin_urls: list[str]) -> str:
    """寫入狀態頁並回傳 file:// 網址。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_status_html(title, message, details, admin_urls), encoding="utf-8")
    return path.resolve().as_uri()
