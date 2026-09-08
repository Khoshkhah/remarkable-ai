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


def list_notebooks(host=None):
    """Fetch all notebooks and folder hierarchy from reMarkable metadata."""
    print("📡 Connecting to reMarkable wirelessly...")
    raw = run_ssh(f"for f in {REMOTE_PATH}/*.metadata; do [ -f \"$f\" ] || continue; uuid=$(basename \"$f\" .metadata); cat \"$f\"; echo \"---$uuid---\"; done", host=host)
    folders = {}
    docs = []
    
    chunks = raw.split("---")
    for i in range(0, len(chunks) - 1, 2):
        meta_str = chunks[i].strip()
        uuid = chunks[i+1].strip()
        if not meta_str or not uuid:
            continue
        try:
            data = json.loads(meta_str)
            if data.get("deleted", False):
                continue
            item_type = data.get("type", "")
            if item_type == "CollectionType":
                folders[uuid] = {
                    "name": data.get("visibleName", "Untitled"),
                    "parent": data.get("parent", "")
                }
            elif item_type == "DocumentType":
                docs.append({
                    "uuid": uuid,
                    "title": data.get("visibleName", "Untitled"),
                    "parent": data.get("parent", ""),
                    "lastModified": int(data.get("lastModified", 0)),
                    "lastOpenedPage": data.get("lastOpenedPage", 0)
                })
        except Exception:
            continue

    def get_full_path(folder_uuid):
        path_parts = []
        curr = folder_uuid
        visited = set()
        while curr and curr in folders and curr not in visited:
            visited.add(curr)
            path_parts.insert(0, folders[curr]["name"])
            curr = folders[curr].get("parent", "")
        return "/".join(path_parts) if path_parts else ""

    notebooks = []
    for d in docs:
        folder_path = get_full_path(d["parent"])
        full_path = f"{folder_path}/{d['title']}" if folder_path else d["title"]
        notebooks.append({
            "uuid": d["uuid"],
            "title": d["title"],
            "folder": folder_path or "/",
            "full_path": full_path,
            "lastModified": d["lastModified"],
            "lastOpenedPage": d["lastOpenedPage"]
        })
            
    notebooks.sort(key=lambda x: x["lastModified"], reverse=True)
    return notebooks

def render_rm_to_png(rm_path, output_png):
    """Parse .rm v5 or v6 vector strokes and render to PNG."""
    import struct
    import svgwrite
    import cairosvg

    with open(rm_path, 'rb') as f:
        header = f.read(43)
        if header.startswith(b"reMarkable .lines file, version=5"):
            # Version 5 binary parser
            num_layers = struct.unpack('<I', f.read(4))[0]
            lines = []
            for _ in range(num_layers):
                num_lines = struct.unpack('<I', f.read(4))[0]
                for _ in range(num_lines):
                    brush_type, color, unk, brush_size, unk2, num_points = struct.unpack('<IIIfII', f.read(24))
                    pts = []
                    for _ in range(num_points):
                        x, y, speed, direction, width, pressure = struct.unpack('<ffffff', f.read(24))
                        pts.append((x, y))
                    if len(pts) > 1:
                        lines.append(pts)

            if not lines:
                return False

            dwg = svgwrite.Drawing(size=('1404px', '1872px'), profile='tiny')
            dwg.add(dwg.rect(insert=(0, 0), size=('100%', '100%'), fill='white'))
            for pts in lines:
                dwg.add(dwg.polyline(pts, stroke='black', stroke_width=2.5, fill='none', stroke_linecap='round', stroke_linejoin='round'))

            svg_str = dwg.tostring()
            cairosvg.svg2png(bytestring=svg_str.encode('utf-8'), write_to=output_png)
            return True

        elif header.startswith(b"reMarkable .lines file, version=6"):
            f.seek(0)
            from rmscene import read_blocks, SceneLineItemBlock
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
        else:
            print(f"Unknown lines format: {header[:30]}")
            return False


def cmd_list(args):
    target_host = get_active_host(getattr(args, "device", None))
    notebooks = list_notebooks(target_host)
    folder_filter = getattr(args, "folder", None)
    if folder_filter:
        q = folder_filter.strip().lower()
        notebooks = [nb for nb in notebooks if q in nb["folder"].lower()]
        print(f"\n📚 Found {len(notebooks)} notebooks in folder '{folder_filter}':\n")
    else:
        print(f"\n📚 Found {len(notebooks)} notebooks on reMarkable:\n")

    print(f"{'FOLDER':<16} {'TITLE':<34} {'UUID':<38}")
    print("-" * 92)
    for nb in notebooks:
        print(f"{nb['folder']:<16} {nb['title']:<34} {nb['uuid']:<38}")
    print()

def cmd_read(args):
    target_host = get_active_host(getattr(args, "device", None))
    notebooks = list_notebooks(target_host)
    target = None
    if args.name:
        q = args.name.strip().lower()
        # 1. Exact match on full_path or title
        for nb in notebooks:
            if q == nb["full_path"].lower() or q == nb["title"].lower():
                target = nb
                break
        # 2. Substring match
        if not target:
            for nb in notebooks:
                if q in nb["full_path"].lower() or q in nb["title"].lower():
                    target = nb
                    break
        if not target:
            print(f"❌ Notebook matching '{args.name}' not found.")
            return
    else:
        target = notebooks[0]

    print(f"📖 Reading notebook: '{target['full_path']}' (UUID: {target['uuid']})")
    
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
        
        target_host = get_active_host(getattr(args, "device", None))
        # Pull page wirelessly
        subprocess.run(["scp", "-q", f"{target_host}:{REMOTE_PATH}/{target['uuid']}/{page_uuid}.rm", local_rm], check=True)
        
        # Render
        rendered = render_rm_to_png(local_rm, local_png)
        if not rendered:
            print("Page contains no pen strokes (blank page).")
            return
            
        print(f"Rendered page to image! Analyzing with AI ({args.action or 'summarize'})...")

        
        # Save copy locally if requested
        if args.save:
            out_img = os.path.expanduser(args.save)
            import shutil
            shutil.copy(local_png, out_img)
            print(f"💾 Saved image to {out_img}")

        # AI Analysis
        analyze_with_ai(local_png, action=args.action or "summarize", prompt=args.prompt)

    if not args.save:
        print("[Auto-cleanup] Temporary render files cleanly deleted from disk.")

def ensure_remote_folder(folder_path, host=None):
    """Ensure a nested folder hierarchy exists on the reMarkable tablet, creating missing folders."""
    if not folder_path or folder_path.strip() in ("", "/"):
        return ""
    
    import uuid
    import time
    
    parts = [p.strip() for p in folder_path.strip("/").split("/") if p.strip()]
    if not parts:
        return ""
    
    raw = run_ssh(f'for f in {REMOTE_PATH}/*.metadata; do [ -f "$f" ] || continue; uuid=$(basename "$f" .metadata); cat "$f"; echo "---$uuid---"; done', host=host)
    folders = {}
    chunks = raw.split("---")
    for i in range(0, len(chunks) - 1, 2):
        meta_str = chunks[i].strip()
        f_uuid = chunks[i+1].strip()
        if not meta_str or not f_uuid:
            continue
        try:
            d = json.loads(meta_str)
            if not d.get("deleted", False) and d.get("type") == "CollectionType":
                folders[f_uuid] = {
                    "name": d.get("visibleName", ""),
                    "parent": d.get("parent", "")
                }
        except Exception:
            pass

    current_parent = ""
    for part in parts:
        found_uuid = None
        for f_uuid, f_info in folders.items():
            if f_info["name"].lower() == part.lower() and f_info["parent"] == current_parent:
                found_uuid = f_uuid
                break
        
        if found_uuid:
            current_parent = found_uuid
        else:
            new_f_uuid = str(uuid.uuid4())
            ts = str(int(time.time() * 1000))
            meta = {
                "deleted": False,
                "lastModified": ts,
                "metadatamodified": False,
                "modified": False,
                "parent": current_parent,
                "pinned": False,
                "synced": False,
                "type": "CollectionType",
                "version": 1,
                "visibleName": part
            }
            content = {}
            with tempfile.TemporaryDirectory() as tmp:
                m_path = os.path.join(tmp, f"{new_f_uuid}.metadata")
                c_path = os.path.join(tmp, f"{new_f_uuid}.content")
                with open(m_path, "w") as f:
                    json.dump(meta, f, indent=2)
                with open(c_path, "w") as f:
                    json.dump(content, f, indent=2)
                
                target = host or get_active_host()
                subprocess.run(["scp", "-q", m_path, c_path, f"{target}:{REMOTE_PATH}/"], check=True)
            
            folders[new_f_uuid] = {"name": part, "parent": current_parent}
            current_parent = new_f_uuid
            print(f"📁 Created folder '{part}' on reMarkable (UUID: {new_f_uuid})")

    return current_parent

def cmd_push(args):
    """Upload a PDF, Markdown, or text file wirelessly to reMarkable tablet, creating folders if needed."""
    file_path = Path(args.file).resolve()
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return

    import uuid
    import time
    from pypdf import PdfReader

    target_host = get_active_host(getattr(args, "device", None))

    folder_name = getattr(args, "folder", None)
    parent_uuid = ""
    if folder_name:
        print(f"📁 Checking folder structure '{folder_name}' on reMarkable...")
        parent_uuid = ensure_remote_folder(folder_name, host=target_host)

    ext = file_path.suffix.lower()
    title = args.title or file_path.stem
    pdf_to_upload = file_path
    temp_dir = None

    if ext in [".md", ".markdown", ".txt"]:
        print(f"📄 Converting {ext.upper()} document to e-ink formatted PDF...")
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet

        temp_dir = tempfile.TemporaryDirectory()
        converted_pdf = Path(temp_dir.name) / f"{title}.pdf"

        doc = SimpleDocTemplate(str(converted_pdf), pagesize=A4, rightMargin=54, leftMargin=54, topMargin=54, bottomMargin=54)
        styles = getSampleStyleSheet()
        normal = styles["Normal"]
        normal.fontSize = 11
        normal.leading = 15

        story = [Paragraph(title, styles["Heading1"]), Spacer(1, 15)]
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_str = line.rstrip()
                if not line_str:
                    story.append(Spacer(1, 8))
                elif line_str.startswith("# "):
                    story.append(Paragraph(line_str[2:], styles["Heading1"]))
                elif line_str.startswith("## "):
                    story.append(Paragraph(line_str[3:], styles["Heading2"]))
                elif line_str.startswith("### "):
                    story.append(Paragraph(line_str[4:], styles["Heading3"]))
                elif line_str.startswith("```"):
                    continue
                else:
                    safe_text = line_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    story.append(Paragraph(safe_text, normal))

        doc.build(story)
        pdf_to_upload = converted_pdf

    reader = PdfReader(str(pdf_to_upload))
    page_count = len(reader.pages)

    doc_uuid = str(uuid.uuid4())
    ts = str(int(time.time() * 1000))
    metadata = {
        "deleted": False,
        "lastModified": ts,
        "metadatamodified": False,
        "modified": False,
        "parent": parent_uuid,
        "pinned": False,
        "synced": False,
        "type": "DocumentType",
        "version": 1,
        "visibleName": title
    }
    content = {
        "extraMetadata": {},
        "fileType": "pdf",
        "formatVersion": 2,
        "lineHeight": -1,
        "margins": 125,
        "orientation": "portrait",
        "pageCount": page_count,
        "textScale": 1,
        "zoomMode": "bestFit"
    }

    print(f"📡 Uploading '{title}' ({page_count} pages) to reMarkable...")
    with tempfile.TemporaryDirectory() as upload_tmp:
        m_file = os.path.join(upload_tmp, f"{doc_uuid}.metadata")
        c_file = os.path.join(upload_tmp, f"{doc_uuid}.content")
        p_file = os.path.join(upload_tmp, f"{doc_uuid}.pagedata")
        
        with open(m_file, "w") as f:
            json.dump(metadata, f, indent=2)
        with open(c_file, "w") as f:
            json.dump(content, f, indent=2)
        with open(p_file, "w") as f:
            f.write("")

        run_ssh(f"mkdir -p {REMOTE_PATH}/{doc_uuid}", host=target_host)
        subprocess.run(["scp", "-q", str(pdf_to_upload), f"{target_host}:{REMOTE_PATH}/{doc_uuid}.pdf"], check=True)
        subprocess.run(["scp", "-q", m_file, c_file, p_file, f"{target_host}:{REMOTE_PATH}/"], check=True)

    if temp_dir:
        temp_dir.cleanup()

    print("🔄 Refreshing tablet library...")
    run_ssh("systemctl restart xochitl", host=target_host)
    folder_msg = f" in folder '{folder_name}'" if folder_name else ""
    print(f"✅ Successfully uploaded '{title}' to reMarkable{folder_msg}!")


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
    devices = cfg.get("devices", {})
    active = cfg.get("active_device", "rm2")

    # If user provided a device name: switch directly
    if args.switch:
        target = args.switch.strip()
        if target in devices:
            cfg["active_device"] = target
            save_config(cfg)
            print(f"Switched active tablet to: [{target}] {devices[target].get('name', target)}")
        else:
            print(f"Unknown device '{target}'. Available: {list(devices.keys())}")
        return

    # If interactive selection requested
    print("\nConfigured reMarkable Tablets:")
    dev_keys = list(devices.keys())
    for i, key in enumerate(dev_keys, 1):
        d = devices[key]
        marker = "[ACTIVE]" if key == active else "        "
        print(f"  {i}. {marker} {key:<10} - {d.get('name', key)} (IP: {d.get('ip', 'N/A')})")
    print()

    try:
        choice = input("Select tablet number or name to activate (Enter to keep current): ").strip()
        if not choice:
            return
        if choice.isdigit() and 1 <= int(choice) <= len(dev_keys):
            target = dev_keys[int(choice) - 1]
        elif choice in devices:
            target = choice
        else:
            print(f"Invalid selection: {choice}")
            return
        cfg["active_device"] = target
        save_config(cfg)
        print(f"Switched active tablet to: [{target}] {devices[target].get('name', target)}\n")
    except (KeyboardInterrupt, EOFError):
        print()

def cmd_add_device(args):
    cfg = load_config()
    key = args.id or input("Enter device identifier (e.g. rm-office, rm-pro): ").strip()
    if not key:
        print("Device ID cannot be empty.")
        return
    name = args.name or input("Enter display name (e.g. reMarkable Office): ").strip() or key
    ip = args.ip or input("Enter Wi-Fi IP address (e.g. 192.168.18.19): ").strip()

    cfg.setdefault("devices", {})[key] = {
        "host": key,
        "name": name,
        "ip": ip
    }
    save_config(cfg)
    print(f"Added device [{key}] ({name}) at {ip}!")
    print(f"To configure SSH, add this to ~/.ssh/config:\n")
    print(f"Host {key}\n    HostName {ip}\n    User root\n    IdentityFile ~/.ssh/id_ed25519\n    StrictHostKeyChecking accept-new\n")

def cmd_setup(args):
    """Automatically install agent skills and slash commands for Antigravity, Gemini, and Claude."""
    import shutil
    print("🤖 Configuring AI Agents for reMarkable AI...\n")
    
    base_dir = Path(__file__).resolve().parent
    skill_src = base_dir / ".agents" / "skills" / "remarkable-ai" / "SKILL.md"
    if not skill_src.exists():
        skill_src = base_dir / "skills" / "remarkable-ai" / "SKILL.md"
        
    claude_cmd_dir = base_dir / ".claude" / "commands"
    
    # 1. Antigravity / Gemini Skills
    gemini_targets = [
        Path.home() / ".gemini" / "config" / "skills" / "remarkable-ai"
    ]
    if sys.platform.startswith("linux") and os.path.exists("/mnt/c/Users"):
        for u in Path("/mnt/c/Users").glob("*"):
            if (u / ".gemini").exists():
                gemini_targets.append(u / ".gemini" / "config" / "skills" / "remarkable-ai")

    for target_dir in gemini_targets:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            if skill_src.exists():
                shutil.copy2(skill_src, target_dir / "SKILL.md")
                print(f"  [Antigravity/Gemini] Installed skill to: {target_dir / 'SKILL.md'}")
        except Exception as e:
            print(f"  [Antigravity/Gemini] Notice: {e}")

    # 2. Claude Code Slash Commands
    claude_targets = [
        Path.home() / ".claude" / "commands"
    ]
    if sys.platform.startswith("linux") and os.path.exists("/mnt/c/Users"):
        for u in Path("/mnt/c/Users").glob("*"):
            if (u / ".claude").exists():
                claude_targets.append(u / ".claude" / "commands")

    if claude_cmd_dir.exists():
        for c_target in claude_targets:
            try:
                c_target.mkdir(parents=True, exist_ok=True)
                for f in claude_cmd_dir.glob("*.md"):
                    shutil.copy2(f, c_target / f.name)
                print(f"  [Claude Code] Installed slash commands (/rm-list, /rm-read, /rm-tasks) to: {c_target}")
            except Exception as e:
                print(f"  [Claude Code] Notice: {e}")

    # 3. Verify tablet connectivity
    print("\n📡 Verifying reMarkable tablet connectivity...")
    cfg = load_config()
    active = cfg.get("active_device", "rm2")
    dev = cfg.get("devices", {}).get(active, {})
    host = dev.get("host", active)
    try:
        res = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", host, "echo ok"], capture_output=True, text=True)
        if res.returncode == 0:
            print(f"  Connected to reMarkable tablet [{active}] ({host}) wirelessly!")
        else:
            print(f"  Tablet [{active}] ({host}) not reachable over SSH.")
            print("  Make sure your tablet is awake, Wi-Fi is on, and SSH keys are added.")
            print("  To register or switch tablets, run: rm-ai devices")
    except Exception as e:
        print(f"  SSH check skipped ({e}).")

    print("\nSetup complete! AI agents are ready to use reMarkable wirelessly.")



def main():
    parser = argparse.ArgumentParser(description="rm-ai: Wireless AI Note Assistant for reMarkable")
    parser.add_argument("--device", "-d", type=str, default=None, help="Target specific tablet (e.g. rm2, rm-alt)")
    subparsers = parser.add_subparsers(dest="command")

    # devices
    p_dev = subparsers.add_parser("devices", aliases=["device"], help="List or switch active reMarkable tablet")
    p_dev.add_argument("switch", nargs="?", default=None, help="Device name or number to activate")
    p_dev.set_defaults(func=cmd_devices)

    # add-device
    p_add = subparsers.add_parser("add-device", help="Register a new reMarkable tablet")
    p_add.add_argument("--id", type=str, default=None, help="Device identifier (e.g. rm-office)")
    p_add.add_argument("--name", type=str, default=None, help="Display name")
    p_add.add_argument("--ip", type=str, default=None, help="Wi-Fi IP address")
    p_add.set_defaults(func=cmd_add_device)

    # setup
    p_setup = subparsers.add_parser("setup", aliases=["setup-agent", "install-skills"], help="Configure AI agents (Antigravity, Gemini, Claude Code)")
    p_setup.set_defaults(func=cmd_setup)


    # list
    p_list = subparsers.add_parser("list", help="List all notebooks on reMarkable")
    p_list.add_argument("folder", nargs="?", default=None, help="Optional folder name to filter by")
    p_list.set_defaults(func=cmd_list)

    # read
    p_read = subparsers.add_parser("read", help="Read and analyze notebook handwriting")
    p_read.add_argument("name", nargs="?", default=None, help="Notebook name (defaults to latest)")
    p_read.add_argument("--page", type=int, default=None, help="Page number (1-indexed)")
    p_read.add_argument("--action", choices=["summarize", "transcribe", "tasks"], default="summarize", help="Action to perform")
    p_read.add_argument("--prompt", type=str, default=None, help="Custom prompt for the AI")
    p_read.add_argument("--save", type=str, default=None, help="Save rendered PNG to path")
    p_read.set_defaults(func=cmd_read)

    # push
    p_push = subparsers.add_parser("push", help="Upload PDF or Markdown document to tablet")
    p_push.add_argument("file", type=str, help="Path to PDF or Markdown file to upload")
    p_push.add_argument("--folder", "-f", type=str, default=None, help="Target folder path on tablet (creates if missing)")
    p_push.add_argument("--title", "-t", type=str, default=None, help="Custom display title on tablet")
    p_push.set_defaults(func=cmd_push)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
