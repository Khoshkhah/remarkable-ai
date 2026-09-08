import os
import sys
import json
import argparse
import subprocess
import tempfile
import struct
import time
from datetime import datetime
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
            sample_values = [type(lb.item.value).__name__ if lb.item and lb.item.value else 'None' for lb in line_blocks[:10]]
            print(f"Debug: {len(line_blocks)} line blocks, sample values: {sample_values}")
            valid_blocks = [lb for lb in line_blocks if lb.item and lb.item.value and hasattr(lb.item.value, 'points') and lb.item.value.points]
            print(f"Debug: {len(valid_blocks)} valid blocks with points")
            if not valid_blocks:
                return False

            dwg = svgwrite.Drawing(size=('1404px', '1872px'), profile='tiny')
            dwg.add(dwg.rect(insert=(0, 0), size=('100%', '100%'), fill='white'))

            all_x, all_y = [], []
            for lb in valid_blocks:
                for p in lb.item.value.points:
                    all_x.append(p.x)
                    all_y.append(p.y)

            min_x = min(all_x) if all_x else 0
            offset_x = 702 if min_x < 0 else 0
            offset_y = 0

            for lb in valid_blocks:
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

    # Check if document already exists in target folder on tablet
    existing_uuid = None
    notebooks = list_notebooks(target_host)
    for nb in notebooks:
        nb_folder = nb.get("folder", "/")
        is_same_folder = False
        if folder_name:
            is_same_folder = (nb_folder.lower().strip("/") == folder_name.lower().strip("/"))
        else:
            is_same_folder = (nb_folder in ("", "/"))
        
        if nb["title"].lower() == title.lower() and is_same_folder:
            existing_uuid = nb["uuid"]
            break

    ts = str(int(time.time() * 1000))
    if existing_uuid and not getattr(args, "force_new", False):
        doc_uuid = existing_uuid
        print(f"🔄 Found existing document '{title}' in '{folder_name or '/'}' (UUID: {doc_uuid}). Updating in-place...")
        is_update = True
        try:
            old_meta_raw = run_ssh(f"cat {REMOTE_PATH}/{doc_uuid}.metadata", host=target_host)
            metadata = json.loads(old_meta_raw)
            metadata["lastModified"] = ts
            metadata["modified"] = True
            metadata["synced"] = False
        except Exception:
            metadata = {
                "deleted": False, "lastModified": ts, "metadatamodified": False,
                "modified": True, "parent": parent_uuid, "pinned": False,
                "synced": False, "type": "DocumentType", "version": 1, "visibleName": title
            }
        try:
            old_content_raw = run_ssh(f"cat {REMOTE_PATH}/{doc_uuid}.content", host=target_host)
            content = json.loads(old_content_raw)
            content["pageCount"] = page_count
        except Exception:
            content = {
                "extraMetadata": {}, "fileType": "pdf", "formatVersion": 2,
                "lineHeight": -1, "margins": 125, "orientation": "portrait",
                "pageCount": page_count, "textScale": 1, "zoomMode": "bestFit"
            }
    else:
        doc_uuid = str(uuid.uuid4())
        is_update = False
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


# ==============================================================================
# Virtual Stylus & Real-Time Hardware Digitizer Injection (/dev/input/event1)
# ==============================================================================

EV_SYN = 0
SYN_REPORT = 0

EV_KEY = 1
BTN_TOOL_PEN = 320     # 0x140
BTN_TOOL_RUBBER = 321  # 0x141
BTN_TOUCH = 330        # 0x14a

EV_ABS = 3
ABS_X = 0
ABS_Y = 1
ABS_PRESSURE = 24
ABS_DISTANCE = 25
ABS_TILT_X = 26
ABS_TILT_Y = 27

class VirtualStylus:
    """Emulates real-time stylus input on reMarkable 2 Wacom I2C Digitizer (/dev/input/event1)."""
    def __init__(self, host=None):
        self.host = host or get_active_host()
        self.proc = None

    def connect(self):
        if self.proc:
            return
        cmd = ["ssh", self.host, "dd of=/dev/input/event1 bs=16 2>/dev/null"]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=2)
            except Exception:
                pass
            self.proc = None

    @staticmethod
    def pack_event(ev_type, ev_code, ev_value):
        t = time.time()
        sec = int(t)
        usec = int((t - sec) * 1_000_000)
        return struct.pack("<IIHHi", sec, usec, ev_type, ev_code, ev_value)

    @staticmethod
    def display_to_digitizer(px, py):
        # Portrait Display: 1404 x 1872
        # Hardware Wacom Calibration:
        # ABS_Y (0..15725) is horizontal (0=Left, 15725=Right)
        # ABS_X (0..20966) is vertical (0=Bottom, 20966=Top)
        abs_y = int(px * 15725 / 1404)
        abs_x = int((1872 - py) * 20966 / 1872)
        abs_y = max(0, min(15725, abs_y))
        abs_x = max(0, min(20966, abs_x))
        return abs_x, abs_y

    def stroke(self, points, is_eraser=False, pressure=2500):
        if not points:
            return
        if not self.proc:
            self.connect()

        tool = BTN_TOOL_RUBBER if is_eraser else BTN_TOOL_PEN
        data = bytearray()

        # 1. Tool Proximity In
        data += self.pack_event(EV_KEY, tool, 1)
        data += self.pack_event(EV_ABS, ABS_DISTANCE, 0)
        data += self.pack_event(EV_SYN, SYN_REPORT, 0)

        # 2. Touch Down at first point
        start_x, start_y = self.display_to_digitizer(*points[0])
        data += self.pack_event(EV_ABS, ABS_X, start_x)
        data += self.pack_event(EV_ABS, ABS_Y, start_y)
        data += self.pack_event(EV_ABS, ABS_PRESSURE, pressure)
        data += self.pack_event(EV_KEY, BTN_TOUCH, 1)
        data += self.pack_event(EV_SYN, SYN_REPORT, 0)

        self.proc.stdin.write(data)
        self.proc.stdin.flush()
        time.sleep(0.006)

        # 3. Intermediate move points
        for pt in points[1:]:
            dx, dy = self.display_to_digitizer(*pt)
            d = bytearray()
            d += self.pack_event(EV_ABS, ABS_X, dx)
            d += self.pack_event(EV_ABS, ABS_Y, dy)
            d += self.pack_event(EV_ABS, ABS_PRESSURE, pressure)
            d += self.pack_event(EV_SYN, SYN_REPORT, 0)
            self.proc.stdin.write(d)
            self.proc.stdin.flush()
            time.sleep(0.003)

        # 4. Touch Up
        data_up = bytearray()
        data_up += self.pack_event(EV_ABS, ABS_PRESSURE, 0)
        data_up += self.pack_event(EV_KEY, BTN_TOUCH, 0)
        data_up += self.pack_event(EV_ABS, ABS_DISTANCE, 50)
        data_up += self.pack_event(EV_SYN, SYN_REPORT, 0)

        # 5. Tool Proximity Out
        data_up += self.pack_event(EV_KEY, tool, 0)
        data_up += self.pack_event(EV_SYN, SYN_REPORT, 0)

        self.proc.stdin.write(data_up)
        self.proc.stdin.flush()
        time.sleep(0.006)


DIGIT_SEGMENTS = {
    '0': {'A', 'B', 'C', 'D', 'E', 'F'},
    '1': {'B', 'C'},
    '2': {'A', 'B', 'G', 'E', 'D'},
    '3': {'A', 'B', 'G', 'C', 'D'},
    '4': {'F', 'G', 'B', 'C'},
    '5': {'A', 'F', 'G', 'C', 'D'},
    '6': {'A', 'F', 'E', 'D', 'C', 'G'},
    '7': {'A', 'B', 'C'},
    '8': {'A', 'B', 'C', 'D', 'E', 'F', 'G'},
    '9': {'A', 'B', 'C', 'D', 'F', 'G'},
    ' ': set(),
}

class SevenSegmentDigit:
    """Manages full-digit box wiping and clean redrawing for 7-segment characters."""
    def __init__(self, stylus, top_left_x, top_left_y, width=95, height=175, thickness=10):
        self.stylus = stylus
        self.x = top_left_x
        self.y = top_left_y
        self.w = width
        self.h = height
        self.t = max(4, thickness)
        self.mid_y = top_left_y + height // 2
        self.current_char = None
        self.draw_coords = self._compute_segment_coords()
        self.box_eraser_coords = self._compute_box_eraser_coords()

    def _compute_box_eraser_coords(self):
        # 4 vertical zigzag passes spanning the digit's bounding box
        x, y, w, h = self.x, self.y, self.w, self.h
        pad = 6
        x_lanes = [x + int(w * f) for f in [0.15, 0.38, 0.62, 0.85]]
        pts = [
            (x_lanes[0], y - pad), (x_lanes[0], y + h + pad),
            (x_lanes[1], y + h + pad), (x_lanes[1], y - pad),
            (x_lanes[2], y - pad), (x_lanes[2], y + h + pad),
            (x_lanes[3], y + h + pad), (x_lanes[3], y - pad),
        ]
        return pts

    def _compute_segment_coords(self):
        x, y, w, h, m = self.x, self.y, self.w, self.h, self.mid_y
        draw_gap = 8

        def h_bar(y_pos):
            x1, x2 = x + draw_gap, x + w - draw_gap
            steps = 4
            pts = []
            offsets = [-4, -1, 2, 5]
            for pass_idx, off in enumerate(offsets):
                y_curr = y_pos + off
                if pass_idx % 2 == 0:
                    pts.extend([(int(x1 + (x2 - x1) * i / steps), y_curr) for i in range(steps + 1)])
                else:
                    pts.extend([(int(x2 - (x2 - x1) * i / steps), y_curr) for i in range(steps + 1)])
            return pts

        def v_bar(x_pos, y_start, y_end):
            y1, y2 = y_start + draw_gap, y_end - draw_gap
            steps = 4
            pts = []
            offsets = [-4, -1, 2, 5]
            for pass_idx, off in enumerate(offsets):
                x_curr = x_pos + off
                if pass_idx % 2 == 0:
                    pts.extend([(x_curr, int(y1 + (y2 - y1) * i / steps)) for i in range(steps + 1)])
                else:
                    pts.extend([(x_curr, int(y2 - (y2 - y1) * i / steps)) for i in range(steps + 1)])
            return pts

        return {
            'A': h_bar(y),
            'B': v_bar(x + w, y, m),
            'C': v_bar(x + w, m, y + h),
            'D': h_bar(y + h),
            'E': v_bar(x, m, y + h),
            'F': v_bar(x, y, m),
            'G': h_bar(m),
        }

    def transition_to(self, char):
        if self.current_char == char:
            return 0

        # 1. If updating an existing character, wipe its bounding box completely
        if self.current_char is not None:
            self.stylus.stroke(self.box_eraser_coords, is_eraser=True, pressure=3400)

        # 2. Draw all active segments for the new character fresh and bold
        target_segments = DIGIT_SEGMENTS.get(char, set())
        for seg in target_segments:
            coords = self.draw_coords[seg]
            self.stylus.stroke(coords, is_eraser=False, pressure=3900)

        self.current_char = char
        return 1

    def clear(self):
        if self.current_char is not None:
            self.stylus.stroke(self.box_eraser_coords, is_eraser=True, pressure=3400)
            self.current_char = None


class DigitalClock:
    """Real-time 7-segment digital clock rendered directly via Virtual Stylus."""
    SIZE_PRESETS = {
        "small":  {"w": 50,  "h": 90,  "digit_gap": 24, "colon_gap": 48, "thickness": 6},
        "medium": {"w": 75,  "h": 140, "digit_gap": 36, "colon_gap": 68, "thickness": 8},
        "large":  {"w": 95,  "h": 175, "digit_gap": 46, "colon_gap": 85, "thickness": 10},
        "xlarge": {"w": 120, "h": 220, "digit_gap": 56, "colon_gap": 100, "thickness": 12},
    }

    def __init__(self, host=None, pos="top-right", format="HH:MM:SS", size=None):
        self.stylus = VirtualStylus(host=host)
        self.pos = pos
        self.format = format

        # Auto size: center defaults to large, corner defaults to medium
        if not size:
            size = "large" if pos == "center" else "medium"
        cfg = self.SIZE_PRESETS.get(size, self.SIZE_PRESETS["large"])
        self.w = cfg["w"]
        self.h = cfg["h"]
        self.digit_gap = cfg["digit_gap"]
        self.colon_gap = cfg["colon_gap"]
        self.thickness = cfg["thickness"]

        self.digits = []
        self.colon_coords = []
        self._init_layout()

    def _init_layout(self):
        w, h = self.w, self.h
        digit_gap = self.digit_gap
        colon_gap = self.colon_gap

        is_seconds_only = (self.format == "MM:SS")
        num_digits = 4 if is_seconds_only else 6
        num_colons = 1 if is_seconds_only else 2
        total_w = num_digits * w + (num_digits - num_colons - 1) * digit_gap + num_colons * colon_gap

        if self.pos == "top-right":
            start_x, start_y = 1404 - total_w - 60, 60
        elif self.pos == "top-left":
            start_x, start_y = 60, 60
        elif self.pos == "center":
            start_x, start_y = (1404 - total_w) // 2, (1872 - h) // 2
        elif self.pos == "bottom-right":
            start_x, start_y = 1404 - total_w - 60, 1872 - h - 80
        elif "," in self.pos:
            parts = self.pos.split(",")
            start_x, start_y = int(parts[0].strip()), int(parts[1].strip())
        else:
            start_x, start_y = (1404 - total_w) // 2, (1872 - h) // 2

        curr_x = start_x
        self.digits = []
        self.colon_coords = []

        def add_digit():
            nonlocal curr_x
            self.digits.append(SevenSegmentDigit(self.stylus, curr_x, start_y, w, h, self.thickness))
            curr_x += w + digit_gap

        def add_colon():
            nonlocal curr_x
            curr_x -= digit_gap
            mid_x = curr_x + colon_gap // 2
            y1 = start_y + int(h * 0.33)
            y2 = start_y + int(h * 0.67)
            # Solid square filled dots
            def solid_dot(cx, cy, r=5):
                pts = []
                for off in [-4, -1, 2, 5]:
                    pts.append((cx - r, cy + off))
                    pts.append((cx + r, cy + off))
                return pts
            dot1 = solid_dot(mid_x, y1, r=5)
            dot2 = solid_dot(mid_x, y2, r=5)
            self.colon_coords.extend([dot1, dot2])
            curr_x += colon_gap

        if is_seconds_only:
            add_digit()
            add_digit()
            add_colon()
            add_digit()
            add_digit()
        else:
            add_digit()
            add_digit()
            add_colon()
            add_digit()
            add_digit()
            add_colon()
            add_digit()
            add_digit()

    def run(self, duration=None, clear_on_exit=False, once=False):
        print(f"⏰ Initializing Virtual Stylus Digital Clock at position: {self.pos} (size: {self.w}x{self.h})", flush=True)
        self.stylus.connect()
        try:
            # Draw bold stationary colons once
            for dots in self.colon_coords:
                self.stylus.stroke(dots, is_eraser=False, pressure=3900)

            now = datetime.now()
            time_str = now.strftime("%M%S" if self.format == "MM:SS" else "%H%M%S")
            total_changes = 0
            for digit, char in zip(self.digits, time_str):
                changes = digit.transition_to(char)
                total_changes += changes
            display_str = f"{time_str[:2]}:{time_str[2:]}" if self.format == "MM:SS" else f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:]}"
            print(f"⏰ Time drawn: [{display_str}] at {self.pos}.", flush=True)

            if once:
                return

            print(f"⚡ Minimum delta state machine active (only changed segments toggled per second).", flush=True)
            print(f"💡 Make sure a notebook page is open on your tablet screen.", flush=True)
            print(f"   Press Ctrl+C to stop.\n", flush=True)

            start_time = time.time()
            last_sec = time_str
            while True:
                now = datetime.now()
                time_str = now.strftime("%M%S" if self.format == "MM:SS" else "%H%M%S")
                if time_str != last_sec:
                    last_sec = time_str
                    total_changes = 0
                    for digit, char in zip(self.digits, time_str):
                        changes = digit.transition_to(char)
                        total_changes += changes
                    display_str = f"{time_str[:2]}:{time_str[2:]}" if self.format == "MM:SS" else f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:]}"
                    print(f"  [{display_str}] Segments updated: {total_changes}", flush=True)

                if duration and (time.time() - start_time) >= duration:
                    print(f"\n⏱️ Duration of {duration}s reached.", flush=True)
                    break

                now_t = time.time()
                sleep_t = 1.0 - (now_t % 1.0)
                if sleep_t < 0.05:
                    sleep_t += 1.0
                time.sleep(sleep_t)
        except KeyboardInterrupt:
            print("\nClock stopped by user.", flush=True)
        finally:
            if clear_on_exit:
                print("🧹 Erasing clock strokes...", flush=True)
                for d in self.digits:
                    d.clear()
                for dots in self.colon_coords:
                    self.stylus.stroke(dots, is_eraser=True, pressure=3200)
            self.stylus.close()
            print("Virtual stylus disconnected.", flush=True)


def cmd_clock(args):
    host = get_active_host(args.device)
    clock = DigitalClock(host=host, pos=args.pos, format=args.format, size=args.size)
    clock.run(duration=args.duration, clear_on_exit=args.clear, once=args.once)


def cmd_draw(args):
    host = get_active_host(args.device)
    stylus = VirtualStylus(host=host)
    stylus.connect()
    try:
        if args.shape == "line":
            p1 = [int(v.strip()) for v in args.from_coord.split(",")]
            p2 = [int(v.strip()) for v in args.to_coord.split(",")]
            steps = 20
            points = [(int(p1[0] + (p2[0] - p1[0]) * i / steps), int(p1[1] + (p2[1] - p1[1]) * i / steps)) for i in range(steps + 1)]
            print(f"✏️ Drawing line from {p1} to {p2}...")
            stylus.stroke(points, is_eraser=args.eraser, pressure=args.pressure)
            print("Done!")
        elif args.shape == "box":
            x, y = [int(v.strip()) for v in args.at.split(",")]
            w, h = [int(v.strip()) for v in args.size.split(",")]
            print(f"✏️ Drawing box at ({x}, {y}) size {w}x{h}...")
            top = [(x + i, y) for i in range(0, w, 5)]
            right = [(x + w, y + i) for i in range(0, h, 5)]
            bottom = [(x + w - i, y + h) for i in range(0, w, 5)]
            left = [(x, y + h - i) for i in range(0, h, 5)]
            stylus.stroke(top + right + bottom + left + [(x, y)], is_eraser=args.eraser, pressure=args.pressure)
            print("Done!")
    finally:
        stylus.close()



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
    p_push.add_argument("--force-new", "-n", action="store_true", help="Always create a new duplicate file instead of updating existing document")
    p_push.set_defaults(func=cmd_push)

    # clock
    p_clock = subparsers.add_parser("clock", help="Real-time 7-segment digital clock via Virtual Stylus with minimal delta updates")
    p_clock.add_argument("--pos", "-p", type=str, default="top-right", help="Screen position: top-right, top-left, center, bottom-right, or X,Y")
    p_clock.add_argument("--size", "-s", type=str, default=None, choices=["small", "medium", "large", "xlarge"], help="Clock size preset")
    p_clock.add_argument("--format", choices=["HH:MM:SS", "MM:SS"], default="HH:MM:SS", help="Clock time format")
    p_clock.add_argument("--duration", type=int, default=None, help="Duration in seconds to run (default: infinite)")
    p_clock.add_argument("--once", "-1", action="store_true", help="Draw current time once and exit immediately")
    p_clock.add_argument("--clear", action="store_true", help="Erase the clock from screen on exit")
    p_clock.set_defaults(func=cmd_clock)

    # draw
    p_draw = subparsers.add_parser("draw", help="Inject live vector strokes via Virtual Stylus")
    p_draw.add_argument("shape", choices=["line", "box"], help="Shape type to draw")
    p_draw.add_argument("--from", dest="from_coord", type=str, help="Start coordinate X,Y (for line)")
    p_draw.add_argument("--to", dest="to_coord", type=str, help="End coordinate X,Y (for line)")
    p_draw.add_argument("--at", type=str, help="Top-left coordinate X,Y (for box)")
    p_draw.add_argument("--size", type=str, help="Width,Height (for box)")
    p_draw.add_argument("--eraser", action="store_true", help="Use virtual eraser instead of pen")
    p_draw.add_argument("--pressure", type=int, default=2500, help="Simulated pen pressure (0..4095)")
    p_draw.set_defaults(func=cmd_draw)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
