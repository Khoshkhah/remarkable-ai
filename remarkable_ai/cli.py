#!/usr/bin/env python3
"""
rm-ai: Wireless AI Note Assistant for reMarkable 2
"""
import os
import sys
import json
import argparse
import subprocess
import tempfile
from pathlib import Path

import os
import sys
import json
import argparse
import subprocess
import tempfile
from pathlib import Path

CONFIG_FILE = os.path.expanduser("~/.config/remarkable-ai/config.json")
REMOTE_PATH = "/home/root/.local/share/remarkable/xochitl"

def load_config():
    default_cfg = {
        "active_device": "rm2",
        "devices": {
            "rm2": {"host": "rm2", "name": "reMarkable 2 (Primary)", "ip": "192.168.18.18"},
            "rm-alt": {"host": "rm-alt", "name": "reMarkable 2 (Secondary)", "ip": ""}
        }
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return {**default_cfg, **json.load(f)}
        except Exception:
            pass
    return default_cfg

def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def get_active_host(override=None):
    if override:
        return override
    cfg = load_config()
    active = cfg.get("active_device", "rm2")
    dev = cfg.get("devices", {}).get(active, {})
    return dev.get("host", active)

def run_ssh(cmd, host=None):
    target = host or get_active_host()
    full_cmd = ["ssh", target, cmd]
    res = subprocess.run(full_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"SSH command failed to '{target}': {res.stderr}")
    return res.stdout


def list_notebooks():
    """Fetch all notebooks from reMarkable metadata."""
    print("📡 Connecting to reMarkable wirelessly...")
    raw = run_ssh(f"for f in {REMOTE_PATH}/*.metadata; do [ -f \"$f\" ] || continue; uuid=$(basename \"$f\" .metadata); cat \"$f\"; echo \"---$uuid---\"; done")
    notebooks = []
    
    chunks = raw.split("---")
    for i in range(0, len(chunks) - 1, 2):
        meta_str = chunks[i].strip()
        uuid = chunks[i+1].strip()
        if not meta_str or not uuid:
            continue
        try:
            data = json.loads(meta_str)
            if data.get("type") == "DocumentType" and not data.get("deleted", False):
                notebooks.append({
                    "uuid": uuid,
                    "title": data.get("visibleName", "Untitled"),
                    "lastModified": int(data.get("lastModified", 0)),
                    "lastOpenedPage": data.get("lastOpenedPage", 0)
                })
        except Exception:
            continue
            
    notebooks.sort(key=lambda x: x["lastModified"], reverse=True)
    return notebooks

def render_rm_to_png(rm_path, output_png):
    """Parse .rm v6 vector strokes and render to PNG."""
    from rmscene import read_blocks, SceneLineItemBlock
    import svgwrite
    import cairosvg

    with open(rm_path, 'rb') as f:
        blocks = list(read_blocks(f))

    line_blocks = [b for b in blocks if isinstance(b, SceneLineItemBlock)]
    if not line_blocks:
        return False

    dwg = svgwrite.Drawing(size=('1404px', '1872px'), profile='tiny')
    dwg.add(dwg.rect(insert=(0, 0), size=('100%', '100%'), fill='white'))

    all_x, all_y = [], []
    for lb in line_blocks:
        for p in lb.item.value.points:
            all_x.append(p.x)
            all_y.append(p.y)

    min_x = min(all_x) if all_x else 0
    offset_x = 702 if min_x < 0 else 0
    offset_y = 0

    for lb in line_blocks:
        pts = [(p.x + offset_x, p.y + offset_y) for p in lb.item.value.points]
        if len(pts) > 1:
            dwg.add(dwg.polyline(pts, stroke='black', stroke_width=2.5, fill='none', stroke_linecap='round', stroke_linejoin='round'))

    svg_str = dwg.tostring()
    cairosvg.svg2png(bytestring=svg_str.encode('utf-8'), write_to=output_png)
    return True

def cmd_list(args):
    notebooks = list_notebooks()
    print(f"\n📚 Found {len(notebooks)} notebooks on reMarkable:\n")
    print(f"{'TITLE':<30} {'UUID':<38}")
    print("-" * 70)
    for nb in notebooks:
        print(f"{nb['title']:<30} {nb['uuid']:<38}")
    print()

def cmd_read(args):
    notebooks = list_notebooks()
    target = None
    if args.name:
        for nb in notebooks:
            if args.name.lower() in nb["title"].lower():
                target = nb
                break
        if not target:
            print(f"❌ Notebook matching '{args.name}' not found.")
            return
    else:
        target = notebooks[0]

    print(f"📖 Reading notebook: '{target['title']}' (UUID: {target['uuid']})")
    
    # Get pages from .content
    content_raw = run_ssh(f"cat {REMOTE_PATH}/{target['uuid']}.content")
    content = json.loads(content_raw)
    pages = [p["id"] for p in content.get("cPages", {}).get("pages", [])]
    if not pages:
        pages = [p for p in content.get("pages", [])]

    page_idx = args.page - 1 if args.page else target["lastOpenedPage"]
    if page_idx >= len(pages):
        page_idx = 0
    page_uuid = pages[page_idx]
    print(f"📄 Fetching page {page_idx + 1} of {len(pages)} (Page ID: {page_uuid})...")

    with tempfile.TemporaryDirectory() as tmpdir:
        local_rm = os.path.join(tmpdir, f"{page_uuid}.rm")
        local_png = os.path.join(tmpdir, f"{page_uuid}.png")
        
        # Pull page wirelessly
        subprocess.run(["scp", "-q", f"{SSH_HOST}:{REMOTE_PATH}/{target['uuid']}/{page_uuid}.rm", local_rm], check=True)
        
        # Render
        rendered = render_rm_to_png(local_rm, local_png)
        if not rendered:
            print("⚠️ Page contains no pen strokes (blank page).")
            return
            
        print(f"🎨 Rendered page to image! Analyzing with AI ({args.action or 'summarize'})...")
        
        # Save copy locally if requested
        if args.save:
            out_img = os.path.expanduser(args.save)
            import shutil
            shutil.copy(local_png, out_img)
            print(f"💾 Saved image to {out_img}")

        # AI Analysis
        analyze_with_ai(local_png, action=args.action or "summarize", prompt=args.prompt)

def analyze_with_ai(image_path, action="summarize", prompt=None):
    from google import genai
    from PIL import Image

    api_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key) if api_key else None
    
    if not client:
        print("\n💡 NOTE: To enable cloud AI analysis, set your GEMINI_API_KEY environment variable:")
        print("   export GEMINI_API_KEY='your_api_key'\n")
        print(f"✅ The handwriting image has been rendered and is ready at: {image_path}")
        return

    img = Image.open(image_path)
    
    if prompt:
        p = prompt
    elif action == "transcribe":
        p = "Please transcribe all handwritten notes on this page accurately. Format in clean markdown, preserving headings and lists. If there are Persian/Farsi words or English technical terms, transcribe both."
    elif action == "tasks":
        p = "Extract all action items, tasks, todo items, or assignments from these handwritten notes. Format as a markdown task checklist: - [ ] Task name"
    else:
        p = "Analyze these handwritten notes and diagrams: 1. Provide a concise executive summary. 2. Transcribe any key text or terms (both English and Persian). 3. Explain any diagrams, architectural blocks, or formulas. 4. List any action items."

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[img, p]
    )
    print("\n" + "=" * 50)
    print("🤖 AI ANALYSIS & TRANSCRIPTION:")
    print("=" * 50)
    print(response.text)
    print("=" * 50 + "\n")

def cmd_devices(args):
    cfg = load_config()
    if args.switch:
        if args.switch in cfg["devices"]:
            cfg["active_device"] = args.switch
            save_config(cfg)
            print(f"✅ Switched active reMarkable device to: '{args.switch}' ({cfg['devices'][args.switch]['name']})")
        else:
            print(f"❌ Unknown device '{args.switch}'. Available devices: {list(cfg['devices'].keys())}")
        return

    print("\n📱 Configured reMarkable Tablets:")
    active = cfg.get("active_device", "rm2")
    for key, d in cfg["devices"].items():
        marker = "🟢 ACTIVE" if key == active else "⚪"
        print(f"  {marker} {key:<10} - {d.get('name', key)} (Host: {d.get('host', key)})")
    print(f"\nTip: Switch active tablet with: rm-ai device <name>\n")

def main():
    parser = argparse.ArgumentParser(description="rm-ai: Wireless AI Note Assistant for reMarkable")
    parser.add_argument("--device", "-d", type=str, default=None, help="Target specific tablet (e.g. rm2, rm-alt)")
    subparsers = parser.add_subparsers(dest="command")

    # devices
    p_dev = subparsers.add_parser("devices", aliases=["device"], help="List or switch active reMarkable tablet")
    p_dev.add_argument("switch", nargs="?", default=None, help="Device name to activate (e.g. rm2, rm-alt)")
    p_dev.set_defaults(func=cmd_devices)

    # list
    p_list = subparsers.add_parser("list", help="List all notebooks on reMarkable")
    p_list.set_defaults(func=cmd_list)

    # read
    p_read = subparsers.add_parser("read", help="Read and analyze notebook handwriting")
    p_read.add_argument("name", nargs="?", default=None, help="Notebook name (defaults to latest)")
    p_read.add_argument("--page", type=int, default=None, help="Page number (1-indexed)")
    p_read.add_argument("--action", choices=["summarize", "transcribe", "tasks"], default="summarize", help="Action to perform")
    p_read.add_argument("--prompt", type=str, default=None, help="Custom prompt for the AI")
    p_read.add_argument("--save", type=str, default=None, help="Save rendered PNG to path")
    p_read.set_defaults(func=cmd_read)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
