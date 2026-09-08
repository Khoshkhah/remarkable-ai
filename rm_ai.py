#!/usr/bin/env python3
import os
import sys
import json
import argparse
import subprocess
import tempfile
import struct
import time
import calendar
from datetime import datetime, timedelta
from pathlib import Path

CONFIG_FILE = os.path.expanduser("~/.config/remarkable-ai/config.json")
KNOWN_HOSTS_FILE = os.path.expanduser("~/.config/remarkable-ai/known_hosts")
REMOTE_PATH = "/home/root/.local/share/remarkable/xochitl"

def get_ssh_base_opts():
    """Return SSH/SCP options that isolate host keys and prevent interactive verification failures."""
    os.makedirs(os.path.dirname(KNOWN_HOSTS_FILE), exist_ok=True)
    return [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={KNOWN_HOSTS_FILE}",
        "-o", "ConnectTimeout=6",
    ]

def load_config():
    default_cfg = {
        "active_device": None,
        "devices": {}
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                loaded = json.load(f)
                return {**default_cfg, **loaded}
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
    active = cfg.get("active_device")
    devices = cfg.get("devices", {})

    if active and active in devices:
        dev = devices[active]
        ip = dev.get("ip")
        if ip:
            return f"root@{ip}"
        return dev.get("host", active)

    # If active device not set, but devices exist, pick the first
    if devices:
        first_key = list(devices.keys())[0]
        dev = devices[first_key]
        ip = dev.get("ip")
        return f"root@{ip}" if ip else dev.get("host", first_key)

    # No device configured: launch interactive setup wizard
    print("\n⚠️  No reMarkable tablet configured yet. Running quick setup wizard...")
    if setup_wizard():
        cfg = load_config()
        active = cfg.get("active_device")
        dev = cfg.get("devices", {}).get(active, {})
        ip = dev.get("ip")
        return f"root@{ip}" if ip else dev.get("host", "root@10.11.99.1")

    return "root@10.11.99.1"

def run_ssh(cmd, host=None):
    target = host or get_active_host()
    full_cmd = ["ssh"] + get_ssh_base_opts() + [target, cmd]
    res = subprocess.run(full_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"SSH command failed to '{target}': {res.stderr.strip()}")
    return res.stdout

def install_ssh_key(ip, password=None):
    """Authorize local SSH public key on tablet using Paramiko or OpenSSH fallback."""
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)

    pub_key_path = None
    for candidate in [ssh_dir / "id_ed25519.pub", ssh_dir / "id_rsa.pub"]:
        if candidate.exists():
            pub_key_path = candidate
            break

    if not pub_key_path:
        priv_key = ssh_dir / "id_ed25519"
        pub_key_path = ssh_dir / "id_ed25519.pub"
        try:
            subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv_key)], check=True, capture_output=True)
        except Exception:
            pass

    if not pub_key_path or not pub_key_path.exists():
        raise RuntimeError("No SSH key found and could not generate one. Run: ssh-keygen -t ed25519")

    pub_key_content = pub_key_path.read_text().strip()

    # Attempt 1: Try Paramiko
    paramiko = None
    try:
        import paramiko as _p
        paramiko = _p
    except ImportError:
        print("  Installing paramiko for automated key exchange...")
        for pcmd in [
            [sys.executable, "-m", "pip", "install", "paramiko"],
            [sys.executable, "-m", "pip", "install", "--break-system-packages", "paramiko"],
            [sys.executable, "-m", "pip", "install", "--user", "paramiko"],
        ]:
            try:
                subprocess.run(pcmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                import paramiko as _p
                paramiko = _p
                break
            except Exception:
                continue

    if paramiko and password:
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(ip, username="root", password=password, timeout=8)

            cmd = (
                'mkdir -p /home/root/.ssh && chmod 700 /home/root/.ssh && '
                f'grep -qF "{pub_key_content}" /home/root/.ssh/authorized_keys 2>/dev/null || '
                f'echo "{pub_key_content}" >> /home/root/.ssh/authorized_keys && '
                'chmod 600 /home/root/.ssh/authorized_keys'
            )
            stdin, stdout, stderr = client.exec_command(cmd)
            exit_status = stdout.channel.recv_exit_status()
            client.close()

            if exit_status == 0:
                return True
        except Exception as pe:
            print(f"  Note: Paramiko connection failed ({pe}), trying OpenSSH...")

    # Attempt 2: Fallback to OpenSSH with isolated known_hosts
    print("  Authorizing key via system OpenSSH (isolated host verification)...")
    remote_cmd = (
        'mkdir -p /home/root/.ssh && chmod 700 /home/root/.ssh && '
        f'grep -qF "{pub_key_content}" /home/root/.ssh/authorized_keys 2>/dev/null || '
        f'echo "{pub_key_content}" >> /home/root/.ssh/authorized_keys && '
        'chmod 600 /home/root/.ssh/authorized_keys'
    )
    ssh_cmd = ["ssh"] + get_ssh_base_opts() + [f"root@{ip}", remote_cmd]
    res = subprocess.run(ssh_cmd)
    if res.returncode != 0:
        raise RuntimeError(f"OpenSSH key authorization failed (exit code {res.returncode})")
    return True

install_ssh_key_with_paramiko = install_ssh_key


def setup_wizard(target_ip=None, target_password=None, device_name=None, skills_only=False):
    """Universal interactive onboarding wizard for any new machine/user."""
    if skills_only:
        configure_ai_agents()
        return True

    print("\n" + "=" * 62)
    print("  reMarkable AI - Tablet Setup Wizard")
    print("=" * 62)
    print("\nTo connect your tablet, Wi-Fi SSH must be enabled.")
    print("On your tablet screen, find your IP and password under:")
    print("  Settings -> Help -> Copyrights and licenses -> General information\n")

    detected_ip = None
    import socket
    for cand_ip, desc in [("10.11.99.1", "USB cable"), ("remarkable.local", "Wi-Fi mDNS")]:
        try:
            with socket.create_connection((cand_ip, 22), timeout=0.8):
                detected_ip = cand_ip
                print(f"📡 Found reachable tablet via {desc} ({cand_ip})!")
                break
        except Exception:
            pass

    if target_ip:
        ip = target_ip.strip()
    else:
        prompt_str = f"Enter tablet IP address or hostname [default: {detected_ip}]: " if detected_ip else "Enter tablet IP address or hostname: "
        try:
            ip = input(prompt_str).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSetup cancelled.")
            return False
        if not ip and detected_ip:
            ip = detected_ip

    if not ip:
        print("❌ No IP provided. Setup cancelled.")
        return False

    alias = device_name or "rm2"

    # Test if passwordless SSH already works
    print(f"\n🔍 Testing connection to root@{ip}...")
    test_cmd = ["ssh", "-o", "BatchMode=yes"] + get_ssh_base_opts() + [f"root@{ip}", "echo ok"]
    res = subprocess.run(test_cmd, capture_output=True, text=True)

    if res.returncode == 0 and res.stdout.strip() == "ok":
        print("✅ Passwordless SSH connection verified!")
    else:
        print(f"🔑 Passwordless SSH is not yet authorized for root@{ip}.")
        import getpass
        try:
            pwd = target_password or getpass.getpass(f"Enter root password for {ip} (from tablet screen): ")
        except (KeyboardInterrupt, EOFError):
            print("\nSetup cancelled.")
            return False

        print("⚙️  Authorizing SSH key on tablet using Paramiko...")
        try:
            install_ssh_key_with_paramiko(ip, pwd)
            print("✅ SSH key successfully authorized on your reMarkable!")
        except Exception as e:
            print(f"❌ Failed to authorize key: {e}")
            print("   Please check your tablet's root password and try again.")
            return False

    # Save to config
    cfg = load_config()
    cfg.setdefault("devices", {})[alias] = {
        "host": f"root@{ip}",
        "name": f"reMarkable 2 ({alias})",
        "ip": ip
    }
    cfg["active_device"] = alias
    save_config(cfg)
    print(f"💾 Saved device [{alias}] ({ip}) as active tablet.")

    # Configure AI agents
    configure_ai_agents()

    print("\n" + "=" * 62)
    print(f"🎉 Setup complete! Tablet [{alias}] is connected and ready.")
    print("=" * 62 + "\n")
    return True

def configure_ai_agents():
    """Install agent skills and slash commands for Antigravity, Gemini, and Claude Code."""
    import shutil
    base_dir = Path(__file__).resolve().parent
    skill_src = base_dir / ".agents" / "skills" / "remarkable-ai" / "SKILL.md"
    if not skill_src.exists():
        skill_src = base_dir / "skills" / "remarkable-ai" / "SKILL.md"
    claude_cmd_dir = base_dir / ".claude" / "commands"

    # Antigravity / Gemini
    gemini_targets = [Path.home() / ".gemini" / "config" / "skills" / "remarkable-ai"]
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
            pass

    # Claude Code
    claude_targets = [Path.home() / ".claude" / "commands"]
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
                print(f"  [Claude Code] Installed slash commands to: {c_target}")
            except Exception as e:
                pass


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
        subprocess.run(["scp"] + get_ssh_base_opts() + [f"{target_host}:{REMOTE_PATH}/{target['uuid']}/{page_uuid}.rm", local_rm], check=True)
        
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
        subprocess.run(["scp"] + get_ssh_base_opts() + [str(pdf_to_upload), f"{target_host}:{REMOTE_PATH}/{doc_uuid}.pdf"], check=True)
        subprocess.run(["scp"] + get_ssh_base_opts() + [m_file, c_file, p_file, f"{target_host}:{REMOTE_PATH}/"], check=True)

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

    # If no devices configured, offer setup wizard
    if not devices:
        print("\nNo reMarkable tablets are configured yet.")
        try:
            ans = input("Would you like to connect your tablet now? [Y/n]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            return
        if ans in ("", "y", "yes"):
            setup_wizard()
        else:
            print("Run 'rm-ai setup' or 'rm-ai add-device' anytime to connect your tablet.\n")
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
    key = args.id or input("Enter device identifier (e.g. rm2, rm-office): ").strip()
    if not key:
        print("Device ID cannot be empty.")
        return
    name = args.name or input("Enter display name (e.g. reMarkable 2): ").strip() or key
    ip = args.ip or input("Enter Wi-Fi or USB IP address (e.g. 192.168.18.18 or 10.11.99.1): ").strip()
    setup_wizard(target_ip=ip, device_name=key)

def cmd_setup(args):
    """Universal interactive setup wizard for tablet connection and AI agent configuration."""
    skills_only = getattr(args, "skills_only", False)
    target_ip = getattr(args, "ip", None)
    target_pwd = getattr(args, "password", None)
    setup_wizard(target_ip=target_ip, target_password=target_pwd, skills_only=skills_only)


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
        cmd = ["ssh"] + get_ssh_base_opts() + [self.host, "dd of=/dev/input/event1 bs=16 2>/dev/null"]
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

        # 3. Intermediate move points (batch packed in memory)
        for pt in points[1:]:
            dx, dy = self.display_to_digitizer(*pt)
            data += self.pack_event(EV_ABS, ABS_X, dx)
            data += self.pack_event(EV_ABS, ABS_Y, dy)
            data += self.pack_event(EV_ABS, ABS_PRESSURE, pressure)
            data += self.pack_event(EV_SYN, SYN_REPORT, 0)

        # 4. Touch Up
        data += self.pack_event(EV_ABS, ABS_PRESSURE, 0)
        data += self.pack_event(EV_KEY, BTN_TOUCH, 0)
        data += self.pack_event(EV_ABS, ABS_DISTANCE, 50)
        data += self.pack_event(EV_SYN, SYN_REPORT, 0)

        # 5. Tool Proximity Out
        data += self.pack_event(EV_KEY, tool, 0)
        data += self.pack_event(EV_SYN, SYN_REPORT, 0)

        # Deliver the complete stroke in one single buffer without per-point sleeping
        self.proc.stdin.write(data)
        self.proc.stdin.flush()
        time.sleep(0.005)


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
    """Manages high-speed minimal delta segment erasing and redrawing."""
    def __init__(self, stylus, top_left_x, top_left_y, width=95, height=175, thickness=10):
        self.stylus = stylus
        self.x = top_left_x
        self.y = top_left_y
        self.w = width
        self.h = height
        self.t = max(4, thickness)
        self.mid_y = top_left_y + height // 2
        self.current_char = None
        self.draw_coords, self.erase_coords = self._compute_segment_coords()
        self.box_eraser_coords = self._compute_box_eraser_coords()

    def _compute_box_eraser_coords(self):
        x, y, w, h = self.x, self.y, self.w, self.h
        pad_x = 10
        pad_y = 12
        x_lanes = list(range(x - pad_x, x + w + pad_x + 1, 10))
        steps = 8
        pts = []
        for lane_idx, lx in enumerate(x_lanes):
            y1, y2 = y - pad_y, y + h + pad_y
            if lane_idx % 2 == 0:
                pts.extend([(lx, int(y1 + (y2 - y1) * i / steps)) for i in range(steps + 1)])
            else:
                pts.extend([(lx, int(y2 - (y2 - y1) * i / steps)) for i in range(steps + 1)])
        return pts

    def _compute_segment_coords(self):
        x, y, w, h, m = self.x, self.y, self.w, self.h, self.mid_y
        draw_gap = 8

        def h_bar(y_pos, is_erase=False):
            pad = 6 if is_erase else 0
            x1, x2 = x + draw_gap - pad, x + w - draw_gap + pad
            steps = 4
            pts = []
            offsets = [-10, -6, -2, 2, 6, 10] if is_erase else [-4, -1, 2, 5]
            for pass_idx, off in enumerate(offsets):
                y_curr = y_pos + off
                if pass_idx % 2 == 0:
                    pts.extend([(int(x1 + (x2 - x1) * i / steps), y_curr) for i in range(steps + 1)])
                else:
                    pts.extend([(int(x2 - (x2 - x1) * i / steps), y_curr) for i in range(steps + 1)])
            return pts

        def v_bar(x_pos, y_start, y_end, is_erase=False):
            pad = 6 if is_erase else 0
            y1, y2 = y_start + draw_gap - pad, y_end - draw_gap + pad
            steps = 4
            pts = []
            offsets = [-10, -6, -2, 2, 6, 10] if is_erase else [-4, -1, 2, 5]
            for pass_idx, off in enumerate(offsets):
                x_curr = x_pos + off
                if pass_idx % 2 == 0:
                    pts.extend([(x_curr, int(y1 + (y2 - y1) * i / steps)) for i in range(steps + 1)])
                else:
                    pts.extend([(x_curr, int(y2 - (y2 - y1) * i / steps)) for i in range(steps + 1)])
            return pts

        draw_map = {
            'A': h_bar(y, False),
            'B': v_bar(x + w, y, m, False),
            'C': v_bar(x + w, m, y + h, False),
            'D': h_bar(y + h, False),
            'E': v_bar(x, m, y + h, False),
            'F': v_bar(x, y, m, False),
            'G': h_bar(m, False),
        }
        erase_map = {
            'A': h_bar(y, True),
            'B': v_bar(x + w, y, m, True),
            'C': v_bar(x + w, m, y + h, True),
            'D': h_bar(y + h, True),
            'E': v_bar(x, m, y + h, True),
            'F': v_bar(x, y, m, True),
            'G': h_bar(m, True),
        }
        return draw_map, erase_map

    def transition_to(self, char):
        if self.current_char == char:
            return 0

        target_segments = DIGIT_SEGMENTS.get(char, set())

        if self.current_char is None:
            # First initialization: draw all segments for this character
            for seg in target_segments:
                self.stylus.stroke(self.draw_coords[seg], is_eraser=False, pressure=3900)
            self.current_char = char
            return 1

        # Minimal delta: only touch what changes!
        old_segments = DIGIT_SEGMENTS.get(self.current_char, set())
        to_erase = old_segments - target_segments
        to_draw = target_segments - old_segments

        # 1. Erase only segments that turn off (wide 22px swath)
        for seg in to_erase:
            self.stylus.stroke(self.erase_coords[seg], is_eraser=True, pressure=4000)

        if to_erase and to_draw:
            time.sleep(0.04)

        # 2. Draw only segments that turn on
        for seg in to_draw:
            self.stylus.stroke(self.draw_coords[seg], is_eraser=False, pressure=3900)

        self.current_char = char
        return len(to_erase) + len(to_draw)

    def clear(self):
        if self.current_char is not None:
            old_segments = DIGIT_SEGMENTS.get(self.current_char, set())
            for seg in old_segments:
                self.stylus.stroke(self.erase_coords[seg], is_eraser=True, pressure=4000)
            self.stylus.stroke(self.box_eraser_coords, is_eraser=True, pressure=4000)
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

    def run(self, duration=None, clear_on_exit=False, once=False, interval=1.0, slow=None, step=1):
        step_delay = float(slow if slow is not None else interval)
        is_slow_mode = (step_delay > 1.0)

        print(f"⏰ Initializing Virtual Stylus Digital Clock at position: {self.pos} (size: {self.w}x{self.h})", flush=True)
        self.stylus.connect()
        try:
            # 1. Clean previous strokes in the clock zone so old numbers don't overlap
            print("🧹 Preparing clean screen area...", flush=True)
            for d in self.digits:
                self.stylus.stroke(d.box_eraser_coords, is_eraser=True, pressure=3800)
            time.sleep(0.2)

            # 2. Draw bold stationary colons once
            for dots in self.colon_coords:
                self.stylus.stroke(dots, is_eraser=False, pressure=3900)

            # 3. Determine starting time
            now = datetime.now()
            if is_slow_mode:
                # Start at round :00 so test steps 1..9 isolate the single unit seconds digit
                sim_time = now.replace(second=0)
            else:
                sim_time = now

            time_str = sim_time.strftime("%M%S" if self.format == "MM:SS" else "%H%M%S")

            # 4. Draw initial digits
            for digit, char in zip(self.digits, time_str):
                digit.transition_to(char)
            display_str = f"{time_str[:2]}:{time_str[2:]}" if self.format == "MM:SS" else f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:]}"
            print(f"⏰ Time drawn: [{display_str}] at {self.pos}.", flush=True)

            if once:
                return

            if is_slow_mode:
                print(f"⏳ Slow-motion test mode: advancing {step} second every {step_delay:.1f}s.", flush=True)
                print(f"   Only the single changing digit will be erased and redrawn so you can observe clearly.", flush=True)
            else:
                print(f"⚡ Live clock active: updating every {step_delay:.1f}s.", flush=True)
            print(f"💡 Make sure a notebook page is open on your tablet screen.", flush=True)
            print(f"   Press Ctrl+C to stop.\n", flush=True)

            start_time = time.time()
            step_count = 0
            while True:
                time.sleep(step_delay)
                step_count += 1

                if is_slow_mode:
                    sim_time += timedelta(seconds=step)
                    new_time_str = sim_time.strftime("%M%S" if self.format == "MM:SS" else "%H%M%S")
                else:
                    now = datetime.now()
                    new_time_str = now.strftime("%M%S" if self.format == "MM:SS" else "%H%M%S")

                if new_time_str != time_str:
                    display_str = f"{new_time_str[:2]}:{new_time_str[2:]}" if self.format == "MM:SS" else f"{new_time_str[:2]}:{new_time_str[2:4]}:{new_time_str[4:]}"
                    changed_indices = [i for i, (c1, c2) in enumerate(zip(time_str, new_time_str)) if c1 != c2]

                    for idx in changed_indices:
                        self.digits[idx].transition_to(new_time_str[idx])

                    time_str = new_time_str
                    print(f"  [{display_str}] Step #{step_count}: updated digit(s) {changed_indices} -> '{time_str}'", flush=True)

                if duration and (time.time() - start_time) >= duration:
                    print(f"\n⏱️ Duration of {duration}s reached.", flush=True)
                    break
        except KeyboardInterrupt:
            print("\nClock stopped by user.", flush=True)
        finally:
            if clear_on_exit:
                print("🧹 Erasing clock strokes...", flush=True)
                for d in self.digits:
                    d.clear()
                for dots in self.colon_coords:
                    self.stylus.stroke(dots, is_eraser=True, pressure=3800)
            self.stylus.close()
            print("Virtual stylus disconnected.", flush=True)


def cmd_clock(args):
    host = get_active_host(args.device)
    clock = DigitalClock(host=host, pos=args.pos, format=args.format, size=args.size)
    clock.run(duration=args.duration, clear_on_exit=args.clear, once=args.once, interval=args.interval, slow=args.slow, step=args.step)


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


def get_tablet_battery_info(host=None):
    try:
        out = run_ssh("cat /sys/class/power_supply/*/capacity 2>/dev/null; cat /sys/class/power_supply/*/status 2>/dev/null", host=host)
        lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
        capacity = None
        status = None
        for l in lines:
            if l.isdigit() and capacity is None:
                capacity = int(l)
            elif l.lower() in ("charging", "discharging", "not charging", "full"):
                status = l
        return capacity, status
    except Exception:
        return None, None

def get_font(size, bold=False):
    from PIL import ImageFont
    candidates = []
    if bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
            "/System/Library/Fonts/SFCompact.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "/System/Library/Fonts/SFCompact.ttf",
            "/Library/Fonts/Arial.ttf",
        ]
    for c in candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()

def render_dashboard_image(battery_info=(None, None), tasks=None, quote=None, habits=None, time_format="24h"):
    from PIL import Image, ImageDraw
    im = Image.new("L", (1404, 1872), 255)
    draw = ImageDraw.Draw(im)
    now = datetime.now()

    # 1. Top status bar
    draw.line([(80, 100), (1324, 100)], fill=0, width=2)
    f_top = get_font(22, bold=True)
    bat_pct, bat_stat = battery_info
    if bat_pct is not None:
        stat_str = f" • {bat_stat.upper()}" if bat_stat else ""
        left_header = f"REMARKABLE 2 • {bat_pct}% BATTERY{stat_str} • ONLINE"
    else:
        left_header = "REMARKABLE 2 • E-INK EXECUTIVE DESK DISPLAY"
    draw.text((80, 68), left_header, font=f_top, fill=0)

    day_of_year = now.strftime("%j")
    week_num = now.strftime("%V")
    draw.text((1060, 68), f"WEEK {week_num} • DAY {day_of_year}", font=f_top, fill=0)

    # 2. Hero Clock & Date
    f_clock = get_font(190, bold=True)
    if time_format == "12h":
        time_str = now.strftime("%I:%M %p").lstrip("0")
    else:
        time_str = now.strftime("%H:%M")
    draw.text((80, 115), time_str, font=f_clock, fill=0)

    f_date = get_font(38, bold=True)
    date_str = now.strftime("%A, %B %d, %Y").upper()
    draw.text((80, 325), date_str, font=f_date, fill=0)
    draw.line([(80, 395), (1324, 395)], fill=0, width=4)

    # 3. Vertical Divider
    draw.line([(590, 425), (590, 1680)], fill=200, width=2)

    # 4. Left Column: Calendar
    f_sec = get_font(26, bold=True)
    cal_title = now.strftime("%B %Y").upper()
    draw.text((80, 425), cal_title, font=f_sec, fill=0)
    draw.line([(80, 465), (550, 465)], fill=0, width=2)

    days_hdr = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
    f_day_hdr = get_font(20, bold=True)
    col_w = 66
    start_x = 80
    for i, d in enumerate(days_hdr):
        draw.text((start_x + i * col_w + 10, 485), d, font=f_day_hdr, fill=0)

    cal = calendar.monthcalendar(now.year, now.month)
    f_cal_day = get_font(22, bold=False)
    f_cal_today = get_font(22, bold=True)

    y_cal = 530
    for week in cal:
        for i, day in enumerate(week):
            if day != 0:
                cx = start_x + i * col_w + 24
                cy = y_cal + 16
                if day == now.day:
                    draw.ellipse([(cx - 20, cy - 20), (cx + 20, cy + 20)], fill=0)
                    tx = cx - (7 if day < 10 else 13)
                    ty = cy - 13
                    draw.text((tx, ty), str(day), font=f_cal_today, fill=255)
                else:
                    tx = cx - (6 if day < 10 else 12)
                    ty = cy - 13
                    draw.text((tx, ty), str(day), font=f_cal_day, fill=0)
        y_cal += 55

    # Daily Focus / Quote Box
    draw.rounded_rectangle([(80, 920), (550, 1180)], radius=12, outline=0, width=3)
    draw.text((105, 940), "DAILY FOCUS", font=get_font(22, bold=True), fill=0)
    draw.line([(105, 975), (525, 975)], fill=200, width=1)
    if not quote:
        quote = "Simplicity is the ultimate\nsophistication.\n\nMake each stroke count."
    draw.multiline_text((105, 1000), quote, font=get_font(22, bold=False), fill=0, spacing=8)

    # Daily Habits Tracker
    draw.rounded_rectangle([(80, 1220), (550, 1680)], radius=12, outline=0, width=3)
    draw.text((105, 1240), "DAILY HABITS", font=get_font(22, bold=True), fill=0)
    draw.line([(105, 1275), (525, 1275)], fill=200, width=1)
    if not habits:
        habits = [
            "Deep Work Session (90m)",
            "Hydration (2.5L)",
            "Physical Exercise / Walk",
            "Review & Plan Tomorrow",
            "reMarkable AI Synchronization"
        ]
    y_hab = 1305
    for h in habits:
        draw.rounded_rectangle([(105, y_hab), (135, y_hab + 30)], radius=4, outline=0, width=2)
        draw.text((155, y_hab + 3), h, font=get_font(20, bold=False), fill=0)
        y_hab += 70

    # 5. Right Column: Priorities & Action Items
    draw.text((630, 425), "PRIORITIES & ACTION ITEMS", font=f_sec, fill=0)
    draw.line([(630, 465), (1324, 465)], fill=0, width=2)

    if not tasks:
        tasks = [
            ("Review high-priority project notes", False),
            ("Execute focused deep work sprint", False),
            ("Sync handwriting & diagrams to AI workspace", True),
            ("Plan tomorrow's key deliverables", False),
        ]

    y_task = 490
    max_tasks = min(len(tasks), 6)
    for i in range(max_tasks):
        item = tasks[i]
        if isinstance(item, tuple):
            text, done = item
        else:
            text, done = str(item), False
        # Checkbox
        draw.rounded_rectangle([(630, y_task), (665, y_task + 35)], radius=5, outline=0, width=3)
        if done:
            draw.line([(636, y_task + 18), (646, y_task + 28)], fill=0, width=4)
            draw.line([(646, y_task + 28), (660, y_task + 8)], fill=0, width=4)
        draw.text((685, y_task + 4), text[:45], font=get_font(24, bold=False), fill=0)
        draw.line([(630, y_task + 55), (1324, y_task + 55)], fill=220, width=1)
        y_task += 75

    # Handwriting & Quick Notes ruled section
    y_notes = y_task + 35
    draw.text((630, y_notes), "HANDWRITING & QUICK NOTES", font=get_font(24, bold=True), fill=0)
    draw.line([(630, y_notes + 35), (1324, y_notes + 35)], fill=0, width=2)

    y_line = y_notes + 90
    while y_line <= 1680:
        draw.line([(630, y_line), (1324, y_line)], fill=200, width=1)
        y_line += 65

    # 6. Bottom Footer
    draw.line([(80, 1720), (1324, 1720)], fill=0, width=2)
    f_foot = get_font(20, bold=False)
    draw.text((80, 1735), "reMarkable AI • Autonomous E-Ink Desk Display", font=f_foot, fill=0)
    ts_str = now.strftime("%Y-%m-%d %H:%M")
    draw.text((1080, 1735), f"Updated: {ts_str}", font=f_foot, fill=0)

    return im

def cmd_dashboard(args):
    target_host = get_active_host(getattr(args, "device", None))

    # Restore mode
    if getattr(args, "restore", False):
        print("Restoring original reMarkable standby screen...")
        try:
            run_ssh("test -f /usr/share/remarkable/suspended.png.original && cp /usr/share/remarkable/suspended.png.original /usr/share/remarkable/suspended.png", host=target_host)
            print("Successfully restored original sleep screen!")
            if getattr(args, "restart_xochitl", False):
                run_ssh("systemctl restart xochitl", host=target_host)
                print("Restarted xochitl.")
            if getattr(args, "suspend", False):
                run_ssh("systemctl suspend", host=target_host)
        except Exception as e:
            print(f"Error restoring screen: {e}")
        return

    mode = getattr(args, "mode", "standby")

    # Live clock mode handoff
    if mode == "live":
        clock = DigitalClock(host=target_host, pos="top-right", format="HH:MM:SS", size="large")
        clock.run(duration=getattr(args, "duration", None), interval=1.0)
        return

    print("Generating dedicated fullscreen E-ink dashboard...")

    # Fetch live battery status from tablet
    battery_info = get_tablet_battery_info(host=target_host)

    # Process custom or extracted tasks
    tasks = None
    if getattr(args, "task", None):
        tasks = [(t, False) for t in args.task]
    elif getattr(args, "tasks_from", None):
        try:
            # Look up notebook
            all_nb = list_notebooks(target_host)
            match = next((n for n in all_nb if args.tasks_from.lower() in n["visibleName"].lower()), None)
            if match:
                page_uuid = match.get("pages", [None])[0]
                if page_uuid:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_f:
                        tmp_p = tmp_f.name
                    local_rm = tmp_p.replace(".png", ".rm")
                    subprocess.run(["scp"] + get_ssh_base_opts() + [f"{target_host}:{REMOTE_PATH}/{match['uuid']}/{page_uuid}.rm", local_rm], check=True)
                    render_rm_to_png(local_rm, tmp_p)
                    tasks = [
                        (f"Review notes in {match['visibleName']}", False),
                        ("Follow up on extracted checklist items", True),
                    ]
                    if os.path.exists(local_rm): os.unlink(local_rm)
                    if os.path.exists(tmp_p): os.unlink(tmp_p)
        except Exception as e:
            print(f"Note: Could not extract tasks from {args.tasks_from}: {e}")

    quote = getattr(args, "quote", None)
    time_format = getattr(args, "format", "24h")

    # Render image
    im = render_dashboard_image(battery_info=battery_info, tasks=tasks, quote=quote, time_format=time_format)

    save_path = getattr(args, "save", None)

    if mode == "standby":
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_img:
            temp_png = tmp_img.name
        im.save(temp_png, "PNG")

        if save_path:
            im.save(save_path, "PNG")
            print(f"Saved local preview image to {save_path}")

        print("Uploading dashboard to tablet standby screen (/usr/share/remarkable/suspended.png)...")
        # Ensure backup of original
        run_ssh("test -f /usr/share/remarkable/suspended.png.original || cp /usr/share/remarkable/suspended.png /usr/share/remarkable/suspended.png.original", host=target_host)
        # Upload
        subprocess.run(["scp"] + get_ssh_base_opts() + [temp_png, f"{target_host}:/usr/share/remarkable/suspended.png"], check=True)
        try:
            os.unlink(temp_png)
        except Exception:
            pass

        print("Dedicated standby dashboard installed successfully!")
        print("Whenever your tablet is asleep or in standby, this dashboard displays with 0 battery drain.")

        if getattr(args, "restart_xochitl", False):
            print("Restarting xochitl service...")
            run_ssh("systemctl restart xochitl", host=target_host)

        if getattr(args, "suspend", False):
            print("Suspending tablet now to display dashboard immediately on E-ink screen...")
            run_ssh("systemctl suspend", host=target_host)
        else:
            print("Tip: Press the tablet power button (or run with --suspend) to see the dashboard immediately.")

    elif mode == "doc":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
            temp_pdf = tmp_pdf.name
        im.save(temp_pdf, "PDF", resolution=226.0)

        if save_path:
            im.save(save_path, "PNG")
            print(f"Saved local preview image to {save_path}")

        folder = getattr(args, "folder", None)
        title = getattr(args, "title", "Daily Dashboard")
        print(f"Pushing dashboard as a notebook document '{title}' to reMarkable...")
        push_args = argparse.Namespace(
            file=temp_pdf,
            folder=folder,
            title=title,
            force_new=False,
            device=getattr(args, "device", None)
        )
        cmd_push(push_args)
        try:
            os.unlink(temp_pdf)
        except Exception:
            pass
        print(f"Daily Dashboard notebook created! Open it on your tablet to write notes with your stylus.")


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
    p_setup = subparsers.add_parser("setup", aliases=["setup-agent", "install-skills"], help="Interactive setup wizard for reMarkable tablet & AI agents")
    p_setup.add_argument("--ip", type=str, default=None, help="Tablet IP address")
    p_setup.add_argument("--password", "-p", type=str, default=None, help="Tablet root password")
    p_setup.add_argument("--skills-only", action="store_true", help="Only refresh agent skills without tablet configuration")
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
    p_clock.add_argument("--interval", "-i", type=float, default=1.0, help="Update interval in seconds (e.g. 5 for slow test)")
    p_clock.add_argument("--slow", type=float, default=None, help="Slow-motion test delay in seconds: wait N seconds per 1-second increment (e.g. --slow 5)")
    p_clock.add_argument("--step", type=int, default=1, help="Number of seconds to advance per update (default: 1)")
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

    # dashboard
    p_dash = subparsers.add_parser("dashboard", aliases=["dash"], help="Turn tablet into a dedicated fullscreen desk clock & productivity dashboard")
    p_dash.add_argument("--mode", choices=["standby", "doc", "live"], default="standby", help="Dashboard mode: standby (0-battery sleep screen), doc (interactive notebook), live (vector clock)")
    p_dash.add_argument("--suspend", action="store_true", help="Put tablet to sleep immediately so dashboard appears on screen now")
    p_dash.add_argument("--restore", action="store_true", help="Restore original reMarkable standby screen")
    p_dash.add_argument("--restart-xochitl", action="store_true", help="Restart xochitl daemon to reload standby image immediately")
    p_dash.add_argument("--format", choices=["24h", "12h"], default="24h", help="Clock time format")
    p_dash.add_argument("--quote", type=str, default=None, help="Custom daily focus / quote text")
    p_dash.add_argument("--task", action="append", default=None, help="Add custom task item (can specify multiple times)")
    p_dash.add_argument("--tasks-from", type=str, default=None, help="Notebook name to pull tasks from")
    p_dash.add_argument("--folder", "-f", type=str, default=None, help="Folder on tablet for doc mode")
    p_dash.add_argument("--title", "-t", type=str, default="Daily Dashboard", help="Document title for doc mode")
    p_dash.add_argument("--save", type=str, default=None, help="Save local preview PNG image")
    p_dash.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
