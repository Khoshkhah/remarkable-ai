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
import math
import re
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

def run_ssh_retry(cmd, host=None, tries=8, delay=5):
    """run_ssh that waits out the moment after `systemctl restart xochitl`, when the tablet drops connections."""
    for i in range(tries):
        try:
            return run_ssh(cmd, host=host)
        except RuntimeError:
            if i == tries - 1:
                raise
            time.sleep(delay)


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
                subprocess.run(["scp", "-q"] + get_ssh_base_opts() + [m_path, c_path, f"{target}:{REMOTE_PATH}/"], check=True)
            
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
            if getattr(args, "rebuild", False):   # the page set changed: a fresh content file, as on a first push
                raise ValueError("rebuild")
            old_content_raw = run_ssh(f"cat {REMOTE_PATH}/{doc_uuid}.content", host=target_host)
            content = json.loads(old_content_raw)
            content["pageCount"] = page_count
            if getattr(args, "margins", None) is not None:
                content["margins"] = args.margins
        except Exception:
            content = {
                "extraMetadata": {}, "fileType": "pdf", "formatVersion": 2,
                "lineHeight": -1, "margins": getattr(args, "margins", None) if getattr(args, "margins", None) is not None else 125,
                "orientation": "portrait", "pageCount": page_count, "textScale": 1, "zoomMode": "bestFit"
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
            "margins": getattr(args, "margins", None) if getattr(args, "margins", None) is not None else 125,
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

    if getattr(args, "fresh", False):   # drop the pen layer: strokes drawn on an earlier version of this page
        run_ssh(f"rm -f {REMOTE_PATH}/{doc_uuid}/*.rm", host=target_host)
    print("🔄 Refreshing tablet library...")
    run_ssh("systemctl restart xochitl", host=target_host)
    folder_msg = f" in folder '{folder_name}'" if folder_name else ""
    print(f"✅ Successfully uploaded '{title}' to reMarkable{folder_msg}!")
    return doc_uuid


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

# Streams the digitizer, and exits by itself when the SSH connection that started it closes: the
# first cat streams, the second blocks on stdin until the client is gone, then the streamer is killed.
# (Killing a plain remote `cat` by name would also kill every other reader on the tablet.)
READER_CMD = "cat /dev/input/event1 & p=$!; cat >/dev/null; kill $p"


class PageChanged(Exception):
    """Raised by the stylus when its guard says the page it draws on is no longer the one on screen."""


class VirtualStylus:
    """Emulates real-time stylus input on reMarkable 2 Wacom I2C Digitizer (/dev/input/event1).

    xochitl smooths and predicts pen motion over *time* and debounces tool changes, so events must
    be paced like a real pen: one frame every FRAME_DT with points STEP_PX apart, a short hover
    before touch-down, and a pause when switching between pen and eraser. Dumping a whole stroke
    in one burst stretches it ~15% and makes the eraser draw a pen line instead.
    """
    FRAME_DT = 0.003     # seconds between frames (a real Wacom reports every few ms)
    STEP_PX = 4          # px between points (~1300 px/s). Faster is possible but not free: the pencil
                         # lays down less ink the faster it moves, and xochitl's motion prediction
                         # overruns turns and stroke ends more (4/3ms: ~4px; 12/3ms: ~11px turns, ~20px ends)
    TOOL_SETTLE = 0.03   # pause before a pen<->eraser switch (20 ms measured as enough)
    PEN_HOVER = 0.0      # extra hover after an eraser->pen switch; measured unnecessary (0 works), knob kept
    ERASE_SETTLE = 0.15  # pause after every eraser stroke: erasing thick ink keeps xochitl busy, and a stroke
                         # sent meanwhile is dropped whole (measured: 0.1 s is enough, 0 loses every other one)
    YIELD_SECONDS = 2.5  # the real pen shares this channel: stay away this long after it was last seen near the screen
    # ponytail: tuned on one rM2 (fw 3.x); raise FRAME_DT/lower STEP_PX if strokes still stretch

    def __init__(self, host=None):
        self.host = host or get_active_host()
        self.proc = None
        self.tool = None
        self.reader = None       # second SSH stream reading the digitizer back, to notice the real pen
        self.sent = {}           # (type, code, value) -> time we injected it, to tell our echo from the real pen
        self.real_until = 0.0    # time until which the real pen is considered near the screen
        self.guard = None        # optional callable: False means the target page is no longer on screen
        self.recheck = False     # set when the real pen was seen (it may have tapped its way elsewhere)

    def connect(self):
        if self.proc:
            return
        cmd = ["ssh"] + get_ssh_base_opts() + [self.host, "dd of=/dev/input/event1 bs=16 2>/dev/null"]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self.reader = subprocess.Popen(["ssh"] + get_ssh_base_opts() + [self.host, READER_CMD], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        import threading
        threading.Thread(target=self._watch_pen, daemon=True).start()
        self._lift_everything()

    def close(self):
        if self.proc:
            try:
                self._lift_everything()
                self.proc.stdin.close()
                self.proc.wait(timeout=2)
            except Exception:
                pass
            self.proc = None
        if self.reader:
            self.reader.kill()      # closing our end of the connection makes the remote reader kill itself
            self.reader = None

    def _watch_pen(self):
        """Every event on the digitizer that is not an echo of one we injected is the real pen: while it
        is near the screen (hovering or writing) the virtual pen must stay off the channel, or the tablet
        sees one stylus jumping between two hands."""
        reader = self.reader
        while reader and reader.poll() is None:
            ev = reader.stdout.read(16)
            if len(ev) < 16:
                break
            _, _, ev_type, code, value = struct.unpack("<IIHHi", ev)
            if ev_type == EV_SYN or time.time() - self.sent.get((ev_type, code, value), 0) < 1.5:
                continue
            if time.time() >= self.real_until:
                print("✋ real pen near the screen, pausing", flush=True)
            self.real_until = time.time() + self.YIELD_SECONDS
            self.recheck = True

    def pen_near(self):
        return time.time() < self.real_until

    def wait_for_pen_gone(self):
        if self.pen_near():
            while self.pen_near():
                time.sleep(0.1)
            print("▶ resuming", flush=True)

    def _lift_everything(self):
        """Pen up, both tools out of proximity. A run killed mid-stroke leaves the digitizer with the
        eraser 'down', and xochitl then erases everything the next run draws; the kernel drops events
        that change nothing, so this is free when the state is already clean."""
        self._frame((EV_ABS, ABS_PRESSURE, 0), (EV_KEY, BTN_TOUCH, 0), (EV_ABS, ABS_DISTANCE, 100),
                    (EV_KEY, BTN_TOOL_RUBBER, 0), (EV_KEY, BTN_TOOL_PEN, 0))
        self.tool = None

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

    def _frame(self, *events):
        """One evdev report: the given (type, code, value) events followed by SYN_REPORT, paced by FRAME_DT."""
        now = time.time()
        for t, c, v in events:
            self.sent[(t, c, v)] = now
        if len(self.sent) > 4000:
            self.sent = {k: t for k, t in self.sent.items() if now - t < 2}
        data = b"".join(self.pack_event(t, c, v) for t, c, v in events)
        self.proc.stdin.write(data + self.pack_event(EV_SYN, SYN_REPORT, 0))
        self.proc.stdin.flush()
        time.sleep(self.FRAME_DT)

    def hover(self, point, seconds):
        """Hold the pen tip in proximity over `point` without touching, e.g. to let xochitl leave its
        temporary eraser mode after a long run of eraser strokes."""
        if not self.proc:
            self.connect()
        x, y = self.display_to_digitizer(*point)
        self._frame((EV_KEY, BTN_TOOL_PEN, 1), (EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y), (EV_ABS, ABS_DISTANCE, 20), (EV_ABS, ABS_PRESSURE, 0))
        for i in range(int(seconds / self.FRAME_DT)):
            self._frame((EV_ABS, ABS_DISTANCE, 20 + (i & 1)))
        self._frame((EV_ABS, ABS_DISTANCE, 60), (EV_KEY, BTN_TOOL_PEN, 0))
        self.tool = BTN_TOOL_PEN

    def stroke(self, points, is_eraser=False, pressure=2500):
        if not points:
            return
        if not self.proc:
            self.connect()

        tool = BTN_TOOL_RUBBER if is_eraser else BTN_TOOL_PEN
        pressure = max(1, min(4095, pressure))   # the digitizer's 12-bit range
        while True:
            self.wait_for_pen_gone()
            if self.recheck and self.guard:
                self.recheck = False
                if not self.guard():
                    raise PageChanged()
            if self._send_stroke(points, tool, pressure, is_eraser):
                return
            # the real pen appeared mid-stroke: we lifted; wait, then redraw this stroke from the start

    def _send_stroke(self, points, tool, pressure, is_eraser):
        """One attempt at a stroke. Returns False if the real pen showed up and the stroke was cut short."""
        back_to_pen = self.tool == BTN_TOOL_RUBBER and tool == BTN_TOOL_PEN
        if self.tool is not None and tool != self.tool:
            time.sleep(self.TOOL_SETTLE)
        self.tool = tool

        # Resample so consecutive points are at most STEP_PX apart
        path = [points[0]]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            n = max(1, math.ceil(math.hypot(x1 - x0, y1 - y0) / self.STEP_PX))
            path += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(1, n + 1)]

        # 1. Proximity in and a short hover at the start point
        x, y = self.display_to_digitizer(*path[0])
        self._frame((EV_KEY, tool, 1), (EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y),
                    (EV_ABS, ABS_DISTANCE, 40), (EV_ABS, ABS_PRESSURE, 0))
        for d in (30, 20, 10):
            self._frame((EV_ABS, ABS_DISTANCE, d))
        for i in range(int(self.PEN_HOVER / self.FRAME_DT) if back_to_pen and self.FRAME_DT else 0):
            self._frame((EV_ABS, ABS_DISTANCE, 10 + (i & 1)))

        # 2. Touch down
        self._frame((EV_ABS, ABS_DISTANCE, 0), (EV_ABS, ABS_PRESSURE, pressure), (EV_KEY, BTN_TOUCH, 1))

        # 3. Move. The kernel drops unchanged ABS values, so a 1-unit pressure wobble keeps every frame alive.
        for i, pt in enumerate(path[1:]):
            if self.pen_near():
                self._frame((EV_ABS, ABS_PRESSURE, 0), (EV_KEY, BTN_TOUCH, 0), (EV_ABS, ABS_DISTANCE, 60), (EV_KEY, tool, 0))
                return False
            x, y = self.display_to_digitizer(*pt)
            self._frame((EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y), (EV_ABS, ABS_PRESSURE, pressure - (i & 1)))

        # 4. Hold at the end point so xochitl's smoothing settles on it, then lift and leave proximity
        for i in range(3):
            self._frame((EV_ABS, ABS_PRESSURE, pressure - 2 - i))
        self._frame((EV_ABS, ABS_PRESSURE, 0), (EV_KEY, BTN_TOUCH, 0), (EV_ABS, ABS_DISTANCE, 30))
        self._frame((EV_ABS, ABS_DISTANCE, 60), (EV_KEY, tool, 0))
        if is_eraser:
            time.sleep(self.ERASE_SETTLE)
        return True


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

def measure_pen_width(stylus, line, pressure):
    """Draw `line` with the pen selected on the tablet and read back the width xochitl gave it.

    Every saved stroke records its drawn width, so one test line is the calibration: wait for
    xochitl's autosave of the open page (irregular, 10-60 s), parse that page file, take its last
    stroke. Returns (width_px, note), or (None, reason) when no new pen stroke appeared, which
    means the tablet is not drawing: no page open, or an eraser/selection tool active."""
    import io
    host = stylus.host
    try:
        from rmscene import read_blocks, SceneLineItemBlock
    except ImportError:
        return None, "rmscene is not installed so the pen cannot be measured; pass --pen-width"
    t0 = int(run_ssh("date +%s", host=host))
    stylus.stroke(line, pressure=pressure)
    saw_save = False
    for _ in range(25):
        time.sleep(3)
        print(".", end="", flush=True)
        saved = run_ssh(f"find {REMOTE_PATH} -name '*.rm' -mmin -3 -exec stat -c '%Y %n' {{}} \\;", host=host).split("\n")
        fresh = [l.split(" ", 1)[1] for l in saved if l.strip() and int(l.split(" ", 1)[0]) >= t0]
        if not fresh:
            continue
        saw_save = True
        raw = subprocess.run(["ssh"] + get_ssh_base_opts() + [host, f"cat {fresh[0]}"], capture_output=True).stdout
        strokes = [b.item.value for b in read_blocks(io.BytesIO(raw))
                   if isinstance(b, SceneLineItemBlock) and b.item.value is not None and getattr(b.item.value, "points", None)]
        if strokes:
            print()
            last = strokes[-1]
            width = sorted(pt.width for pt in last.points)[len(last.points) // 2]
            return width, f"{str(last.tool).split('.')[-1]} draws {width}px at pressure {pressure}"
    print()
    if saw_save:
        return None, "the tablet saved the page but the test line is not in it: an eraser or selection tool is active, select a pen in the toolbar"
    return None, "the tablet did not draw or save the test line within 75 s: open a notebook page and select a pen, or pass --pen-width"


class SevenSegmentDigit:
    """One 7-segment digit; on each change only the segments that differ are erased or redrawn.

    `thickness` is the ink width wanted for each bar and `pen` the width of one line of the pen in
    use: bars thicker than one line are built from overlapping passes. The erase sweep must cover
    the full ink width with margin, and every bar is inset from the corners far enough that erasing
    it can never touch a neighbouring bar's ink. The eraser is assumed to be xochitl's medium one.
    """
    ERASER_R = 8.5      # radius of the medium eraser, measured on an rM2
    ERASE_EXTEND = 3    # erase paths run this far past both ends so no sliver of the pen's round cap survives
    MARGIN = 5.5        # erase coverage beyond the pen edge, absorbs stroke placement error
    # ponytail: a larger eraser size on the tablet needs a bigger ERASER_R here

    def __init__(self, stylus, top_left_x, top_left_y, width=95, height=175, thickness=12, pen=12, ink=None):
        """`pen` is the line width the bars are built from; `ink` the full width a line really covers,
        halo included (the pencil at full pressure covers ~70px), which is what has to be erased."""
        self.stylus = stylus
        self.x, self.y, self.w, self.h = top_left_x, top_left_y, width, height
        self.mid_y = top_left_y + height // 2
        self.current_char = None
        self.pressure = 2500
        thickness = max(thickness, pen)                             # one line is the thinnest bar
        ink_r, pen_r = thickness / 2, pen / 2
        halo_r = max(ink or 0, thickness) / 2                        # total ink half-width to erase
        # draw: overlapping passes (half a line apart) whose ink spans the wanted thickness
        d_spread = (thickness - pen) / 2
        n_d = 1 + math.ceil(d_spread / (pen / 4)) if d_spread else 1
        self.draw_offsets = [-d_spread + 2 * d_spread * i / (n_d - 1) for i in range(n_d)] if n_d > 1 else [0]
        # erase: passes no more than an eraser width apart, covering the full ink width plus margin
        # sideways; the bar ends overrun only enough to take the pen's round caps
        spread = max(0.0, halo_r + self.MARGIN - self.ERASER_R)
        n = 2 + int(2 * spread / (2 * self.ERASER_R - 1))
        offsets = [-spread + 2 * spread * i / (n - 1) for i in range(n)]
        ext = max(self.ERASE_EXTEND, pen_r + self.MARGIN - self.ERASER_R)
        # corner gap: the erase path ends, plus the eraser radius, must stop short of the neighbouring
        # bar's ink. Sideways the sweep may be wide, because it only runs along this bar's own length,
        # where the neighbours' cores are not.
        self.gap = math.ceil(ext + self.ERASER_R + ink_r)
        if min(width, height // 2) - 2 * self.gap < 8:
            raise ValueError(f"{thickness}px bars ({pen}px pen line) need {self.gap}px corner gaps, too much for a "
                             f"{width}x{height} digit; use a bigger --size, a thinner pen, lower --pressure or smaller --thickness")
        self.draw_coords = self._segments(self.draw_offsets, 0)
        self.erase_coords = self._segments(offsets, ext)

    @staticmethod
    def _bar(p0, p1, offsets, ext):
        """Serpentine along p0->p1 (extended by ext at both ends), one pass per perpendicular offset."""
        (x0, y0), (x1, y1) = p0, p1
        length = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        a = (x0 - ux * ext, y0 - uy * ext)
        b = (x1 + ux * ext, y1 + uy * ext)
        pts = []
        for i, off in enumerate(offsets):
            ends = [(a[0] - uy * off, a[1] + ux * off), (b[0] - uy * off, b[1] + ux * off)]
            pts += ends if i % 2 == 0 else ends[::-1]
        return pts

    def _segments(self, offsets, ext):
        x, y, w, h, m, g = self.x, self.y, self.w, self.h, self.mid_y, self.gap
        return {
            'A': self._bar((x + g, y), (x + w - g, y), offsets, ext),
            'B': self._bar((x + w, y + g), (x + w, m - g), offsets, ext),
            'C': self._bar((x + w, m + g), (x + w, y + h - g), offsets, ext),
            'D': self._bar((x + g, y + h), (x + w - g, y + h), offsets, ext),
            'E': self._bar((x, m + g), (x, y + h - g), offsets, ext),
            'F': self._bar((x, y + g), (x, m - g), offsets, ext),
            'G': self._bar((x + g, m), (x + w - g, m), offsets, ext),
        }

    def erase_for(self, char):
        """Erase the segments that are on now but off in `char`."""
        for seg in DIGIT_SEGMENTS.get(self.current_char, set()) - DIGIT_SEGMENTS.get(char, set()):
            self.stylus.stroke(self.erase_coords[seg], is_eraser=True, pressure=4000)

    def draw_for(self, char):
        """Draw the segments that are off now but on in `char`, and become `char`."""
        for seg in DIGIT_SEGMENTS.get(char, set()) - DIGIT_SEGMENTS.get(self.current_char, set()):
            self.stylus.stroke(self.draw_coords[seg], is_eraser=False, pressure=self.pressure)
        self.current_char = char

    def transition_to(self, char):
        if self.current_char != char:
            self.erase_for(char)
            self.draw_for(char)

    def clear(self):
        """Erase along all seven segment lines and nothing else: removes this digit, or one left at the
        same spot by an earlier run, without touching the rest of the page."""
        for seg in "ABCDEFG":
            self.stylus.stroke(self.erase_coords[seg], is_eraser=True, pressure=4000)
        self.current_char = None



class DigitalClock:
    """Real-time 7-segment digital clock rendered directly via Virtual Stylus."""
    START_SETTLE = 4.0   # seconds of pen hover after the start-up erase before drawing (1 s lost the hour digits on a PDF page)
    EDGE = 130   # corner presets keep this far from the screen edges: xochitl's toolbar column on the
                 # left and the notebook menu at the top-right are ~105px, and a stroke landing on them
                 # opens menus or switches tools instead of drawing
    SIZE_PRESETS = {   # gaps grow further with the bar thickness, see __init__
        "small":  {"w": 50,  "h": 90,  "digit_gap": 30, "colon_gap": 60},
        "medium": {"w": 75,  "h": 140, "digit_gap": 46, "colon_gap": 100},
        "large":  {"w": 95,  "h": 175, "digit_gap": 60, "colon_gap": 128},
        "xlarge": {"w": 120, "h": 220, "digit_gap": 60, "colon_gap": 110},
    }

    def __init__(self, host=None, pos="top-right", format="HH:MM:SS", size=None, thickness=12, pressure=2500, pen_width=None, frame=False, ink_width=None):
        self.stylus = VirtualStylus(host=host)
        self.pos = pos
        self.format = format
        self.frame = frame
        self.ink_width = ink_width
        self.pressure = max(1, min(4095, pressure))   # the digitizer's 12-bit range

        # Auto size: center defaults to large, corner defaults to medium
        if not size:
            size = "large" if pos == "center" else "medium"
        cfg = self.SIZE_PRESETS.get(size, self.SIZE_PRESETS["large"])
        self.w = cfg["w"]
        self.h = cfg["h"]
        self.digit_gap = cfg["digit_gap"]
        self.colon_gap = cfg["colon_gap"]

        if pen_width is None:
            pen_width = self._measure_pen()
            self.ink_width = self.ink_width or pen_width   # the measured width is the full ink width
        self.pen_width = pen_width
        if thickness == "max":   # thickest bars that stay readable: at least MIN_BAR_RATIO times longer than thick
            thickness = next((t for t in range(80, pen_width, -1) if self._bar_length(t, pen_width) >= self.MIN_BAR_RATIO * t), pen_width)
        self.thickness = max(thickness, pen_width)
        self.digit_gap += self.thickness // 2       # thick bars need more air between digits
        self.colon_gap += self.thickness            # and the colon dots grow with the thickness

        self.digits = []
        self.colons = []          # (cx, cy) of each colon dot
        self._init_layout()
        bar = min(self.w, self.h // 2) - 2 * self.digits[0].gap
        if bar < self.thickness:
            print(f"⚠️  {self.thickness}px-thick bars on {self.w}x{self.h} digits are only {bar}px long; "
                  f"use --size xlarge, a thinner pen or lower --pressure for readable digits")

    MIN_BAR_RATIO = 1.5   # bar length / thickness that --thickness max keeps, so segments stay bars not blobs

    def _bar_length(self, thickness, pen_width):
        """Length of the shortest bar for this thickness, or -1 if it does not fit the digit at all."""
        try:
            d = SevenSegmentDigit(None, 0, 0, self.w, self.h, thickness, pen_width)
            return min(self.w, self.h // 2) - 2 * d.gap
        except ValueError:
            return -1

    def _frame_path(self, grow):
        """The frame's rounded rectangle, offset outwards by `grow` px (inwards if negative)."""
        x, y, w, h = self.frame_box
        return self._rounded_rect(x - grow, y - grow, w + 2 * grow, h + 2 * grow, r=max(6, 30 + grow))

    @staticmethod
    def _rounded_rect(x, y, w, h, r=30):
        pts = []
        for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
            pts += [(cx + r * math.cos(math.radians(a0 + 90 * i / 6)), cy + r * math.sin(math.radians(a0 + 90 * i / 6))) for i in range(7)]
        return pts + [pts[0]]

    @staticmethod
    def _dot(cx, cy, diameter, line):
        """Spiral that a pen `line` px wide fills into a solid disc about `diameter` px across."""
        pts = []
        r = max(0.0, (diameter - line) / 2)
        while r > 0:
            pts += [(cx + r * math.cos(2 * math.pi * i / 12), cy + r * math.sin(2 * math.pi * i / 12)) for i in range(12)]
            r -= line / 2
        return pts + [(cx, cy)]

    def _measure_pen(self):
        """Width of one line of the selected pen at our pressure, measured with a test line drawn
        along the first digit's middle segment (erased again at start-up). Not cached: the tablet
        only records the selected pen in a notebook's files when the notebook is closed, so there is
        no reliable key to cache under; pass --pen-width to skip the 10-20 s wait."""
        print("📏 Measuring the selected pen: drawing a test line and waiting for the tablet to save it (10-60 s)...", flush=True)
        x, y, _ = self._origin()
        line = [(x + self.w // 4, y + self.h // 2), (x + 3 * self.w // 4, y + self.h // 2)]
        width, note = measure_pen_width(self.stylus, line, self.pressure)
        if width is None:
            self.stylus.close()
            raise ValueError(note)
        print(f"🖊️  {note}")
        return width

    def _origin(self):
        """Top-left of the first digit and the total width of the clock for the current position."""
        w, h = self.w, self.h
        is_seconds_only = self.format in ("MM:SS", "HH:MM")   # the two 4-digit layouts
        num_digits = 4 if is_seconds_only else 6
        num_colons = 1 if is_seconds_only else 2
        total_w = num_digits * w + (num_digits - num_colons - 1) * self.digit_gap + num_colons * self.colon_gap
        e = self.EDGE
        if self.pos == "top-right":
            start_x, start_y = 1404 - total_w - e, e
        elif self.pos == "top-left":
            start_x, start_y = e, e
        elif self.pos == "center":
            start_x, start_y = (1404 - total_w) // 2, (1872 - h) // 2
        elif self.pos == "bottom-right":
            start_x, start_y = 1404 - total_w - e, 1872 - h - e
        elif "," in self.pos:
            parts = self.pos.split(",")
            start_x, start_y = int(parts[0].strip()), int(parts[1].strip())
        else:
            start_x, start_y = (1404 - total_w) // 2, (1872 - h) // 2
        return start_x, start_y, total_w

    def _init_layout(self):
        w, h = self.w, self.h
        digit_gap = self.digit_gap
        colon_gap = self.colon_gap
        is_seconds_only = self.format in ("MM:SS", "HH:MM")
        start_x, start_y, total_w = self._origin()

        # Frame: rounded rectangle around the whole clock, as far out as it can go without entering the
        # toolbar column or the top-right notebook menu (a stroke there presses buttons)
        keep_out = ((0, 0, 105, 720), (1300, 0, 1404, 105))
        def clear(pad):
            x0, y0, x1, y1 = start_x - pad, start_y - pad, start_x + total_w + pad, start_y + h + pad
            return (x0 >= 40 and y0 >= 40 and x1 <= 1364 and y1 <= 1832 and
                    all(x1 <= kx0 or x0 >= kx1 or y1 <= ky0 or y0 >= ky1 for kx0, ky0, kx1, ky1 in keep_out))
        pad = 70 + self.thickness
        while pad > 12 and not clear(pad):
            pad -= 2
        self.frame_pad = pad
        self.frame_box = (start_x - pad, start_y - pad, total_w + 2 * pad, h + 2 * pad)

        curr_x = start_x
        self.digits = []
        self.colons = []

        def add_digit():
            nonlocal curr_x
            digit = SevenSegmentDigit(self.stylus, curr_x, start_y, w, h, self.thickness, self.pen_width, self.ink_width)
            digit.pressure = self.pressure
            self.digits.append(digit)
            curr_x += w + digit_gap

        def add_colon():
            nonlocal curr_x
            curr_x -= digit_gap
            mid_x = curr_x + colon_gap // 2
            self.colons += [(mid_x, start_y + int(h * 0.33)), (mid_x, start_y + int(h * 0.67))]
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

    def start(self):
        """Connect, erase digits an earlier run left at this spot, then draw the colons and the frame."""
        self.stylus.connect()
        print("🧹 Erasing old digits along the segment lines...", flush=True)
        for d in self.digits:
            d.clear()
        # after that long eraser session xochitl stays busy for a while (longer on a PDF page, where it
        # re-renders the page under every erase) and drops strokes sent meanwhile: hover until it is done
        self.stylus.hover((self.digits[0].x, self.digits[0].y), self.START_SETTLE)
        for cx, cy in self.colons:   # filled discs as wide as the bars are thick
            self.stylus.stroke(self._dot(cx, cy, self.thickness, self.pen_width), pressure=self.pressure)
        if self.frame:
            self.stylus.stroke(self._frame_path(0), pressure=self.pressure)

    def show(self, time_str):
        """Change only the digits that differ: all erasing first, then all drawing (one eraser->pen switch)."""
        changed = [i for i, (d, ch) in enumerate(zip(self.digits, time_str)) if d.current_char != ch]
        for i in changed:
            self.digits[i].erase_for(time_str[i])
        for i in changed:
            self.digits[i].draw_for(time_str[i])
        return changed

    def run(self, duration=None, clear_on_exit=False, once=False, interval=1.0, slow=None, step=1):
        step_delay = float(slow if slow is not None else interval)
        is_slow_mode = slow is not None
        fmt = {"MM:SS": "%M%S", "HH:MM": "%H%M"}.get(self.format, "%H%M%S")
        def shown_now():   # the real time at the last interval boundary, e.g. :00 :05 :10 for --interval 5
            return datetime.fromtimestamp(math.floor(time.time() / step_delay) * step_delay).strftime(fmt)

        print(f"⏰ Initializing Virtual Stylus Digital Clock at position: {self.pos} (size: {self.w}x{self.h})", flush=True)
        try:
            self.start()

            # 3. Determine starting time
            now = datetime.now()
            if is_slow_mode:
                # Start at round :00 so test steps 1..9 isolate the single unit seconds digit
                sim_time = now.replace(second=0)
                time_str = sim_time.strftime(fmt)
            else:
                time_str = shown_now()

            # 4. Draw initial digits
            self.show(time_str)
            display_str = f"{time_str[:2]}:{time_str[2:]}" if len(time_str) == 4 else f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:]}"
            print(f"⏰ Time drawn: [{display_str}] at {self.pos}.", flush=True)

            if once:
                return

            if is_slow_mode:
                print(f"⏳ Slow-motion test mode: advancing {step} second every {step_delay:.1f}s.", flush=True)
                print(f"   Only the single changing digit will be erased and redrawn so you can observe clearly.", flush=True)
            else:
                print(f"⚡ Live clock active: updating every {step_delay:.1f}s.", flush=True)
            print(f"💡 Make sure a notebook page is open and a pen is the active tool: after 'Erase all' the", flush=True)
            print(f"   eraser stays selected and every stroke of the clock erases instead of drawing.", flush=True)
            print(f"   Press Ctrl+C to stop.\n", flush=True)

            start_time = time.time()
            step_count = 0
            while True:
                if is_slow_mode:
                    time.sleep(step_delay)
                    sim_time += timedelta(seconds=step)
                    new_time_str = sim_time.strftime("%M%S" if self.format == "MM:SS" else "%H%M%S")
                else:
                    # If the display is already behind (a slow redraw), catch up at once; otherwise wake
                    # on the next wall-clock multiple of the interval, so drawing time never drifts
                    new_time_str = shown_now()
                    if new_time_str == time_str:
                        time.sleep(step_delay - time.time() % step_delay)
                        new_time_str = shown_now()
                step_count += 1

                if new_time_str != time_str:
                    display_str = f"{new_time_str[:2]}:{new_time_str[2:]}" if len(new_time_str) == 4 else f"{new_time_str[:2]}:{new_time_str[2:4]}:{new_time_str[4:]}"
                    changed_indices = [i for i, (c1, c2) in enumerate(zip(time_str, new_time_str)) if c1 != c2]

                    self.show(new_time_str)

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
                for cx, cy in self.colons:
                    self.stylus.stroke(self._dot(cx, cy, self.thickness + self.pen_width + 6, 2 * SevenSegmentDigit.ERASER_R), is_eraser=True, pressure=4000)
                if self.frame:   # eraser passes across the full width of the pen line
                    ink_r = (self.ink_width or self.pen_width) / 2
                    for grow in [g for g in range(-int(ink_r) - 4, int(ink_r) + 5, 12)]:
                        self.stylus.stroke(self._frame_path(grow), is_eraser=True, pressure=4000)
            self.stylus.close()
            print("Virtual stylus disconnected.", flush=True)


TABLET_APP_DIR = "/home/root/.local/share/rmclock"   # where the tablet-resident clock lives
CLOCK_FOLDER = "app"                                  # the library folder holding the documents tablet apps run in
RMCLOCK_UNIT = """[Unit]
Description=rm-ai clock, drawn by the tablet while its Clock document is open

[Service]
ExecStart=%s/rmclock %s
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
""" % (TABLET_APP_DIR, TABLET_APP_DIR)


class StrokeRecorder(VirtualStylus):
    """A stylus that keeps the bytes it would have sent instead of sending them. The tablet-resident
    clock (app/rmclock.c) replays them, so its strokes are byte-for-byte the ones this tool draws."""
    FRAME_DT = TOOL_SETTLE = ERASE_SETTLE = 0

    def __init__(self):
        import io
        super().__init__(host="-")
        self.proc = argparse.Namespace(stdin=io.BytesIO())

    def connect(self):
        pass

    def close(self):
        pass

    def take(self):
        import io
        data, self.proc.stdin = self.proc.stdin.getvalue(), io.BytesIO()
        return data


def bake_clock_app(clock, out):
    """Write every stroke of `clock` into `out`, one raw input-event file per stroke, named as
    app/rmclock.c expects: d<slot><seg>/e<slot><seg> (pen/eraser per segment), colon<i>, frame."""
    rec = StrokeRecorder()
    clock.stylus = rec
    for i, d in enumerate(clock.digits):
        d.stylus = rec
        for seg in "ABCDEFG":
            rec.stroke(d.draw_coords[seg], pressure=d.pressure)
            (out / f"d{i}{seg}.bin").write_bytes(rec.take())
            rec.stroke(d.erase_coords[seg], is_eraser=True, pressure=4000)
            (out / f"e{i}{seg}.bin").write_bytes(rec.take())
    for i, (cx, cy) in enumerate(clock.colons):
        rec.stroke(clock._dot(cx, cy, clock.thickness, clock.pen_width), pressure=clock.pressure)
        (out / f"colon{i}.bin").write_bytes(rec.take())
    if clock.frame:
        rec.stroke(clock._frame_path(0), pressure=clock.pressure)
        (out / "frame.bin").write_bytes(rec.take())


def install_clock_app(host, clock, doc_title, interval, fmt):
    """Put the clock on the tablet: the baked strokes, the replayer and a systemd unit that starts it at
    boot. It then draws whenever the `doc_title` document is open, with no PC involved."""
    import shutil
    binary = Path(__file__).resolve().with_name("app") / "rmclock"
    if not binary.exists():
        print("❌ app/rmclock is missing: build it with app/build.sh (needs Docker)")
        return
    run_ssh("systemctl stop rmclock 2>/dev/null; true", host=host)   # an earlier install must not draw through the changes below
    same_title = [nb for nb in list_notebooks(host) if nb["title"].lower() == doc_title.lower()]
    doc = next((nb for nb in same_title if nb["folder"].lower() == CLOCK_FOLDER), None) or (same_title[0] if same_title else None)
    if doc is None:
        print(f"📄 Pushing a blank '{doc_title}' document into the '{CLOCK_FOLDER}' folder (the tablet reloads once)...", flush=True)
        doc_uuid = push_chat_document(host, doc_title, pages=1, folder=CLOCK_FOLDER)
    else:
        doc_uuid = doc["uuid"]
        if doc["folder"].lower() != CLOCK_FOLDER:   # an existing document of that name: move it into the folder
            folder_uuid = ensure_remote_folder(CLOCK_FOLDER, host=host)
            run_ssh(f"sed -i 's/\"parent\": \"[^\"]*\"/\"parent\": \"{folder_uuid}\"/' {REMOTE_PATH}/{doc_uuid}.metadata && systemctl restart xochitl", host=host)
            print(f"📁 Moved '{doc_title}' into the '{CLOCK_FOLDER}' folder (the tablet reloads once)")
    content = json.loads(run_ssh_retry(f"cat {REMOTE_PATH}/{doc_uuid}.content", host=host))
    pages = content.get("cPages", {}).get("pages") or content.get("pages") or []
    page = next((p["id"] if isinstance(p, dict) else p for p in pages if not (isinstance(p, dict) and p.get("deleted"))), None)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        bake_clock_app(clock, out)
        (out / "config").write_text(f"doc={doc_uuid}\nrm={REMOTE_PATH}/{doc_uuid}/{page}.rm\nslots={len(clock.digits)}\n"
                                    f"fmt={fmt}\ninterval={max(1, int(interval))}\n")
        if os.path.exists("/etc/localtime"):      # the tablet runs on UTC; the clock shows this PC's local time
            shutil.copy("/etc/localtime", out / "localtime")
        shutil.copy(binary, out / "rmclock")
        (out / "rmclock.service").write_text(RMCLOCK_UNIT)
        print(f"📦 Installing {len(list(out.glob('*.bin')))} baked strokes and the replayer on the tablet...", flush=True)
        run_ssh(f"rm -rf {TABLET_APP_DIR}; mkdir -p {TABLET_APP_DIR}", host=host)
        tar = subprocess.run(["tar", "-C", str(out), "-cf", "-", "."], capture_output=True, check=True).stdout
        subprocess.run(["ssh"] + get_ssh_base_opts() + [host, f"tar -C {TABLET_APP_DIR} -xf -"], input=tar, check=True)
    run_ssh(f"chmod +x {TABLET_APP_DIR}/rmclock && mv {TABLET_APP_DIR}/rmclock.service /etc/systemd/system/ && "
            "systemctl daemon-reload && systemctl enable rmclock >/dev/null 2>&1 && systemctl restart rmclock", host=host)
    print(f"✅ Clock installed: open '{doc_title}' on the tablet and it starts by itself (also after a reboot); "
          f"close the document and it stops. Log: ssh {host} journalctl -u rmclock -f")


def uninstall_clock_app(host):
    run_ssh(f"systemctl disable --now rmclock >/dev/null 2>&1; rm -f /etc/systemd/system/rmclock.service; "
            f"systemctl daemon-reload; rm -rf {TABLET_APP_DIR}", host=host)
    print("🗑️  Clock removed from the tablet (the Clock document is kept)")


LIVE_USAGE_ROWS = (1000, 1190, 1380)  # y of the three usage rows (label + bar; digits 40px below)
LIVE_BAR_X = (278, 521)             # x extent of the bar interior the pen fills
LIVE_CLOCK_ZONE = (66, 100, 800, 320)     # swept clean before each redraw of the big HH:MM (font 190 at 80,115)
TABLET_DASH_DIR = "/home/root/.local/share/rmdash"
GLYPH_BASE = (300, 300)   # every glyph is baked with its text origin here; rmdash shifts it into place
# What the tablet-resident dashboard draws with the pen (display px): the time and the three usage rows,
# as the live page does, in single-stroke glyphs (STROKE_FONT) that a thick pen such as the Marker turns
# into bold digits in one or two strokes. Date, calendar and the weather block are
# printed: the installer leaves JPEG templates for the coming days and the tablet composes each day's
# page itself (app/rmdash.c compose_page, weather in the sleep screen's format). The one source
# app/rmdash.c reads as `layout`; zones are erased whole when a value in them changes; texts are
# (font size, x, y) of a text origin.
TABLET_DASH_LAYOUT = {
    "zones": {"clock": LIVE_CLOCK_ZONE, "row0": (95, 996, 535, 1185), "row1": (95, 1186, 535, 1375), "row2": (95, 1376, 535, 1565)},
    "texts": {"clock": (190, 80, 115), "pct0": (110, 105, 1045), "reset0": (40, 335, 1058), "pct1": (110, 105, 1235), "reset1": (40, 335, 1248),
              "pct2": (110, 105, 1425), "reset2": (40, 335, 1438)},
    "bars": [(LIVE_BAR_X[0], LIVE_BAR_X[1], y + 13) for y in LIVE_USAGE_ROWS],
}
GLYPH_SETS = {190: "0123456789:", 110: "0123456789", 40: "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:"}
DASH_STOCK_DAYS = 60   # printed pages (date, calendar) the tablet gets to compose and swap in by itself


SLEEP_FONTS = ((22, True), (96, True), (32, True), (22, False), (18, True), (15, False), (20, False), (64, True))   # what the tablet stamps with
SLEEP_CHARS = "".join(chr(c) for c in range(32, 127)) + "°•"


def rle_encode(data):
    """Runs of (value, count<=65535) over the pixels, tiny for a mostly white page; app/rmdash.c decodes it."""
    out = bytearray()
    for m in re.finditer(rb"(.)\1*", data, re.S):
        v, n = m.group(1)[0], m.end() - m.start()
        while n > 0:
            k = min(n, 65535)
            out += bytes((v, k & 255, k >> 8))
            n -= k
    return bytes(out)


def render_sleep_backgrounds(out, city_name, tasks, days=DASH_STOCK_DAYS):
    """The sleep screen for today and the coming `days` with the live values blank (sleep/YYYY-MM-DD.rle)."""
    (out / "sleep").mkdir(exist_ok=True)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(days + 1):
        day = today + timedelta(days=i)
        im = render_dashboard_image(tasks=tasks, weather={"name": city_name} if city_name else None, now=day, stamp=True).convert("L")
        (out / "sleep" / f"{day.strftime('%Y-%m-%d')}.rle").write_bytes(rle_encode(im.tobytes()))


def bake_font_atlases(out):
    """Glyph bitmaps of the fonts the sleep screen uses, placed as PIL places them (left/top of the glyph
    box from the text origin, advance width), as f<size><b|r>.atlas: 'ATLS', count, then per glyph
    code, left, top, width, height, advance (int16 each) and width*height coverage bytes."""
    from PIL import Image, ImageDraw
    for size, bold in SLEEP_FONTS:
        font = get_font(size, bold=bold)
        blob = bytearray(b"ATLS") + struct.pack("<h", len(SLEEP_CHARS))
        for ch in SLEEP_CHARS:
            code = {"°": 176, "•": 149}.get(ch, ord(ch))
            l, t, r, b = font.getbbox(ch)
            w, h = max(0, r - l), max(0, b - t)
            if w and h:
                im = Image.new("L", (r + 2, b + 2), 0)
                ImageDraw.Draw(im).text((0, 0), ch, font=font, fill=255)
                px = im.crop((l, t, r, b)).tobytes()
            else:
                px = b""
            blob += struct.pack("<hhhhhh", code, l, t, w, h, round(font.getlength(ch))) + px
        (out / f"f{size}{'b' if bold else 'r'}.atlas").write_bytes(bytes(blob))


def render_dash_pages(out, city_name, tasks, days=DASH_STOCK_DAYS):
    """JPEG templates of the dashboard page for today and the coming `days` (pages/YYYY-MM-DD.jpg):
    everything printed except the weather block, which the tablet adds when it composes the page."""
    (out / "pages").mkdir(exist_ok=True)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(days + 1):
        day = today + timedelta(days=i)
        page = render_dashboard_image(tasks=tasks, live=True, weather={"name": city_name} if city_name else None, now=day, pen_weather=True)
        page.convert("L").save(out / "pages" / f"{day.strftime('%Y-%m-%d')}.jpg", "JPEG", quality=85)


# A single-stroke font for the pen: each glyph is one to three polylines/arcs on a 100-unit em (cap
# height 72, y down), drawn along its centreline; the pen's own width gives it body (the Marker makes
# it bold). Glyphs keep the page font's advance widths so the layout is the one the printed page has.
def _arc(cx, cy, rx, ry, t0, t1, n=None):
    n = n or max(6, int(abs(t1 - t0) / 15))
    return [(cx + rx * math.cos(math.radians(t0 + (t1 - t0) * i / n)), cy + ry * math.sin(math.radians(t0 + (t1 - t0) * i / n))) for i in range(n + 1)]

STROKE_FONT = {   # glyph: (width, strokes)
    "0": (54, [_arc(27, 36, 22, 35, 270, 630)]),
    "1": (54, [[(10, 16), (27, 0), (27, 72)], [(12, 72), (42, 72)]]),
    "2": (54, [_arc(27, 21, 21, 21, 200, 380) + [(4, 72), (50, 72)]]),
    "3": (54, [_arc(27, 19, 19, 19, 200, 450) + _arc(26, 52, 20, 20, 275, 530)]),
    "4": (54, [[(38, 0), (2, 52), (54, 52)], [(38, 0), (38, 72)]]),
    "5": (54, [[(48, 0), (10, 0), (6, 34)] + _arc(28, 50, 22, 22, 250, 520)]),
    "6": (54, [[(46, 0), (32, 5), (17, 20), (9, 38), (6, 50)] + _arc(27, 51, 21, 21, 180, 540)]),
    "7": (54, [[(4, 0), (50, 0), (22, 72)]]),
    "8": (54, [_arc(27, 19, 17, 17, 90, 450) + _arc(27, 52, 20, 20, 270, -90)]),
    "9": (54, [_arc(27, 22, 21, 21, 0, 360) + [(48, 22), (46, 42), (38, 58), (26, 68), (10, 72)]]),
    ":": (24, [_arc(12, 26, 2, 2, 0, 360), _arc(12, 62, 2, 2, 0, 360)]),   # dots: a tiny circle the pen fills
    ".": (24, [_arc(12, 70, 2, 2, 0, 360)]),
    ",": (24, [[(13, 66), (9, 78)]]),
    "-": (40, [[(6, 40), (34, 40)]]),
    "/": (40, [[(36, 0), (4, 72)]]),
    "%": (70, [_arc(13, 12, 11, 11, 0, 360), [(58, 0), (12, 72)], _arc(57, 60, 11, 11, 0, 360)]),
    "\u00b0": (30, [_arc(15, 10, 9, 9, 0, 360)]),
    "A": (54, [[(2, 72), (27, 0), (52, 72)], [(11, 48), (43, 48)]]),
    "B": (50, [[(4, 72), (4, 0), (26, 0)] + _arc(26, 18, 18, 18, 270, 450) + [(4, 36), (26, 36)] + _arc(26, 54, 18, 18, 270, 450) + [(4, 72)]]),
    "C": (54, [_arc(30, 36, 26, 36, 325, 35)]),
    "D": (54, [[(4, 0), (4, 72), (22, 72)] + _arc(22, 36, 26, 36, 90, -90) + [(4, 0)]]),
    "E": (46, [[(44, 0), (4, 0), (4, 72), (44, 72)], [(4, 36), (36, 36)]]),
    "F": (44, [[(44, 0), (4, 0), (4, 72)], [(4, 36), (34, 36)]]),
    "G": (56, [_arc(30, 36, 26, 36, 325, 10) + [(56, 40), (32, 40)]]),
    "H": (52, [[(4, 0), (4, 72)], [(48, 0), (48, 72)], [(4, 36), (48, 36)]]),
    "I": (20, [[(10, 0), (10, 72)]]),
    "J": (36, [[(30, 0), (30, 52)] + _arc(18, 52, 12, 20, 0, 180)]),
    "K": (50, [[(4, 0), (4, 72)], [(46, 0), (4, 44)], [(17, 32), (48, 72)]]),
    "L": (44, [[(4, 0), (4, 72), (44, 72)]]),
    "M": (60, [[(4, 72), (4, 0), (30, 50), (56, 0), (56, 72)]]),
    "N": (52, [[(4, 72), (4, 0), (48, 72), (48, 0)]]),
    "O": (56, [_arc(28, 36, 24, 36, 270, 630)]),
    "P": (48, [[(4, 72), (4, 0), (26, 0)] + _arc(26, 20, 20, 20, 270, 450) + [(4, 40)]]),
    "Q": (56, [_arc(28, 36, 24, 36, 270, 630), [(32, 52), (54, 76)]]),
    "R": (50, [[(4, 72), (4, 0), (26, 0)] + _arc(26, 20, 20, 20, 270, 450) + [(4, 40)], [(26, 40), (48, 72)]]),
    "S": (50, [_arc(26, 18, 18, 18, 340, 90) + _arc(26, 54, 18, 18, 270, 520)]),
    "T": (50, [[(2, 0), (48, 0)], [(25, 0), (25, 72)]]),
    "U": (52, [[(4, 0), (4, 50)] + _arc(26, 50, 22, 22, 180, 0) + [(48, 0)]]),
    "V": (54, [[(2, 0), (27, 72), (52, 0)]]),
    "W": (70, [[(2, 0), (18, 72), (35, 14), (52, 72), (68, 0)]]),
    "X": (52, [[(4, 0), (48, 72)], [(48, 0), (4, 72)]]),
    "Y": (52, [[(2, 0), (26, 38), (50, 0)], [(26, 38), (26, 72)]]),
    "Z": (50, [[(4, 0), (46, 0), (4, 72), (46, 72)]]),
    # lower case: x-height 52 (y 20..72), ascenders to 0, descenders to 92
    "a": (46, [_arc(23, 48, 18, 24, 0, 360), [(41, 24), (41, 72)]]),
    "b": (46, [[(6, 0), (6, 72)], _arc(24, 48, 18, 24, 180, 540)]),
    "c": (42, [_arc(22, 48, 17, 24, 320, 40)]),
    "d": (46, [[(40, 0), (40, 72)], _arc(22, 48, 18, 24, 0, 360)]),
    "e": (44, [[(5, 48), (39, 48)] + _arc(22, 48, 17, 24, 0, -250)]),
    "f": (30, [_arc(28, 12, 12, 12, 270, 180) + [(16, 72)], [(4, 24), (28, 24)]]),
    "g": (46, [_arc(22, 46, 18, 22, 0, 360), [(40, 24), (40, 74)] + _arc(22, 74, 18, 18, 0, 160)]),
    "h": (46, [[(6, 0), (6, 72)], _arc(24, 44, 18, 20, 180, 360) + [(42, 72)]]),
    "i": (18, [[(9, 24), (9, 72)], _arc(9, 8, 2, 2, 0, 360)]),
    "j": (22, [[(13, 24), (13, 76)] + _arc(3, 76, 10, 14, 0, 100), _arc(13, 8, 2, 2, 0, 360)]),
    "k": (42, [[(6, 0), (6, 72)], [(36, 24), (6, 52)], [(16, 43), (38, 72)]]),
    "l": (18, [[(9, 0), (9, 72)]]),
    "m": (66, [[(6, 72), (6, 24)], _arc(20, 40, 14, 16, 180, 360) + [(34, 72)], _arc(48, 40, 14, 16, 180, 360) + [(62, 72)]]),
    "n": (46, [[(6, 72), (6, 24)], _arc(24, 44, 18, 20, 180, 360) + [(42, 72)]]),
    "o": (46, [_arc(23, 48, 18, 24, 270, 630)]),
    "p": (46, [[(6, 24), (6, 92)], _arc(24, 48, 18, 24, 180, 540)]),
    "q": (46, [[(40, 24), (40, 92)], _arc(22, 48, 18, 24, 0, 360)]),
    "r": (30, [[(6, 72), (6, 24)], _arc(22, 40, 16, 16, 180, 300)]),
    "s": (40, [_arc(20, 36, 12, 12, 330, 90) + _arc(20, 60, 12, 12, 270, 510)]),
    "t": (30, [[(14, 4), (14, 60)] + _arc(24, 60, 10, 12, 180, 90), [(4, 24), (26, 24)]]),
    "u": (46, [[(6, 24), (6, 52)] + _arc(24, 52, 18, 20, 180, 0), [(42, 24), (42, 72)]]),
    "v": (44, [[(4, 24), (22, 72), (40, 24)]]),
    "w": (62, [[(4, 24), (17, 72), (31, 34), (45, 72), (58, 24)]]),
    "x": (42, [[(5, 24), (37, 72)], [(37, 24), (5, 72)]]),
    "y": (44, [[(4, 24), (22, 72)], [(40, 24), (16, 92)]]),
    "z": (40, [[(5, 24), (35, 24), (5, 72), (35, 72)]]),
    "'": (16, [[(8, 0), (8, 16)]]),
    "\"": (26, [[(8, 0), (8, 16)], [(18, 0), (18, 16)]]),
    "?": (40, [_arc(20, 18, 14, 16, 200, 360) + [(20, 48), (20, 56)], _arc(20, 70, 2, 2, 0, 360)]),
    "!": (20, [[(10, 0), (10, 52)], _arc(10, 70, 2, 2, 0, 360)]),
    "(": (26, [_arc(36, 40, 26, 46, 130, 230)]),
    ")": (26, [_arc(-10, 40, 26, 46, 50, -50)]),
    ";": (24, [_arc(12, 26, 2, 2, 0, 360), [(13, 66), (9, 78)]]),
}


def stroke_glyph(ch, size, advance):
    """The strokes of `ch` at `size` px, in px relative to the text origin the way text_strokes()
    places glyphs (the cap top sits 0.2 em below the origin, as in the page font), centred in `advance`."""
    if ch not in STROKE_FONT:
        return []
    width, strokes = STROKE_FONT[ch]
    k = size / 100
    left = (advance - width * k) / 2
    return [[(left + x * k, 0.2 * size + y * k) for x, y in stroke] for stroke in strokes]


ERASE_OFFSETS = (-12, -6, 0, 6, 12)   # eraser passes along a stroke, sideways offsets in px: the pen overshoots the
                                      # path by a few px at the outside of curves, the eraser cuts the corner
ERASE_STEP_PX = 4                     # eraser samples 4 px apart, like the pen: the app erases only at each sampled
                                      # position, and 8 px left pieces of the Calligraphy pen's 50 px strokes between them
SWEEP_LANE = 6                        # lanes of a zone sweep: the eraser only takes a point of a wide stroke when it
                                      # passes within a few px of the stroke's centreline (measured with a 27 px ballpoint)


def erase_path(paths, ext=6):
    """One continuous eraser stroke that takes the pen strokes drawn along `paths` out again, whatever
    pen it was: for every stroke, passes along it at ERASE_OFFSETS sideways, chained end to end (each
    pass returns the way the previous one went), run `ext` px past the ends (the pen's round caps).
    One stroke, so the app's pause after every eraser stroke is paid once per glyph, not per pass."""
    out = []
    for path in paths:
        pts = [(float(x), float(y)) for x, y in path]
        if len(pts) < 2:
            continue
        if len(pts) == 2 and pts[0] == pts[1]:
            pts = [pts[0], (pts[0][0] + 1, pts[0][1])]
        (x0, y0), (x1, y1) = pts[0], pts[1]
        d = math.hypot(x1 - x0, y1 - y0) or 1
        pts[0] = (x0 - (x1 - x0) / d * ext, y0 - (y1 - y0) / d * ext)
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        d = math.hypot(x1 - x0, y1 - y0) or 1
        pts[-1] = (x1 + (x1 - x0) / d * ext, y1 + (y1 - y0) / d * ext)
        for k, off in enumerate(ERASE_OFFSETS):
            shifted = []
            for i, (x, y) in enumerate(pts):
                (ax, ay), (bx, by) = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
                tx, ty = bx - ax, by - ay
                n = math.hypot(tx, ty) or 1
                shifted.append((x - ty / n * off, y + tx / n * off))
            out += shifted if k % 2 == 0 else shifted[::-1]
    return out


def bake_dash_app(out):
    """Bake glyphs (one file per glyph and size, STROKE_FONT single strokes) and their erasers, zone
    sweeps and the usage bars for app/rmdash.c, plus the `layout` and `glyphs` tables it reads."""
    rec = StrokeRecorder()
    gx, gy = GLYPH_BASE
    lines = []
    for size, chars in GLYPH_SETS.items():
        font = get_font(size, bold=True)
        for ch in chars + " ":
            lines.append(f"{size} {ord(ch)} {font.getlength(ch):.2f}")
            if ch == " ":
                continue
            paths = [[(gx + x, gy + y) for x, y in path] for path in stroke_glyph(ch, size, font.getlength(ch))]
            for path in paths:
                rec.stroke(path, pressure=4000)
            (out / f"g{size}_{ord(ch)}.bin").write_bytes(rec.take())
            rec.STEP_PX = ERASE_STEP_PX
            rec.stroke(erase_path(paths), is_eraser=True, pressure=4000)
            rec.STEP_PX = VirtualStylus.STEP_PX
            (out / f"e{size}_{ord(ch)}.bin").write_bytes(rec.take())
    (out / "glyphs").write_text("\n".join(lines) + "\n")
    for name, zone in TABLET_DASH_LAYOUT["zones"].items():
        rec.stroke(sweep_path(*zone, lane=SWEEP_LANE), is_eraser=True, pressure=4000)
        (out / f"sweep_{name}.bin").write_bytes(rec.take())
    for i, (x0, x1, y) in enumerate(TABLET_DASH_LAYOUT["bars"]):
        rec.stroke([(x0, y), (x1, y)], pressure=4000)
        (out / f"bar{i}.bin").write_bytes(rec.take())
        rec.STEP_PX = ERASE_STEP_PX
        rec.stroke(erase_path([[(x0, y), (x1, y)]]), is_eraser=True, pressure=4000)
        rec.STEP_PX = VirtualStylus.STEP_PX
        (out / f"ebar{i}.bin").write_bytes(rec.take())
    layout = [f"base {gx} {gy}"]
    layout += [f"zone {n} {x0} {y0} {x1} {y1}" for n, (x0, y0, x1, y1) in TABLET_DASH_LAYOUT["zones"].items()]
    layout += [f"text {n} {size} {x} {y}" for n, (size, x, y) in TABLET_DASH_LAYOUT["texts"].items()]
    layout += [f"bar {i} {x0} {x1} {y}" for i, (x0, x1, y) in enumerate(TABLET_DASH_LAYOUT["bars"])]
    (out / "layout").write_text("\n".join(layout) + "\n")


def usage_file_text(sub):
    """The `usage` file rmdash draws from, from fetch_claude_subscription_usage()'s result."""
    lines = [f"fetched={int(time.time())}"]
    n = 0
    for label, pct, resets in (sub or {}).get("windows", [])[:3]:
        try:
            reset = int(datetime.fromisoformat(resets.replace("Z", "+00:00")).timestamp()) if resets else 0
        except (ValueError, AttributeError):
            reset = 0
        lines += [f"label{n}={label}", f"pct{n}={max(0, min(100, round(float(pct))))}", f"reset{n}={reset}"]
        n += 1
    return "\n".join(lines + [f"n={n}", ""])


def feed_dash_usage(host, sub):
    """Copy fresh usage figures to the tablet dashboard (for a tablet without a Claude login of its own)."""
    text = usage_file_text(sub)
    subprocess.run(["ssh"] + get_ssh_base_opts() + [host, f"test -d {TABLET_DASH_DIR} && cat > {TABLET_DASH_DIR}/usage.tmp && mv {TABLET_DASH_DIR}/usage.tmp {TABLET_DASH_DIR}/usage"],
                   input=text.encode(), capture_output=True)


def install_dash_app(host, city, token_file, tasks=None, doc_title="Dashboard", no_push=False):
    """Put the dashboard on the tablet: push the printed page into the `app` folder, bake the strokes,
    install app/rmdash with a systemd unit. With `token_file` (a Claude Code credential file of a login
    made for the tablet) the tablet fetches Claude usage itself; otherwise the PC's standby cron feeds it."""
    import shutil
    binary = Path(__file__).resolve().with_name("app") / "rmdash"
    if not binary.exists():
        print("❌ app/rmdash is missing: build it with app/build.sh (needs Docker)")
        return
    cfg = load_config()
    weather = fetch_weather(city) if city else None
    if weather and "error" in weather:
        print(f"❌ weather: {weather['error']}")
        return
    loc = load_config().get("weather_location") or {}
    token = None
    if token_file:
        creds = json.load(open(os.path.expanduser(token_file)))["claudeAiOauth"]
        token = f"access={creds['accessToken']}\nrefresh={creds['refreshToken']}\nexpires={int(creds.get('expiresAt', 0)) // 1000}\n"
    run_ssh("systemctl stop rmdash 2>/dev/null; true", host=host)   # never push (the app restarts) under a running program
    existing = next((nb["uuid"] for nb in list_notebooks(host) if nb["title"].lower() == doc_title.lower() and nb["folder"].lower() == CLOCK_FOLDER), None)
    stock = Path(tempfile.mkdtemp())
    render_dash_pages(stock, loc.get("name", ""), tasks)
    if no_push and existing:
        doc_uuid = existing
        print(f"📄 Keeping the '{doc_title}' page already on the tablet (no push, no reload)", flush=True)
    else:
        today_pdf = stock / "today.pdf"
        render_dashboard_image(tasks=tasks, live=True, weather=weather).save(today_pdf, "PDF", resolution=226.0)
        print(f"📄 Pushing the '{doc_title}' page into the '{CLOCK_FOLDER}' folder (the tablet reloads once)...", flush=True)
        doc_uuid = cmd_push(argparse.Namespace(file=str(today_pdf), folder=CLOCK_FOLDER, title=doc_title, force_new=False, margins=0, fresh=True, device=None))
    content = json.loads(run_ssh_retry(f"cat {REMOTE_PATH}/{doc_uuid}.content", host=host))
    pages = content.get("cPages", {}).get("pages") or content.get("pages") or []
    page_id = next((p["id"] if isinstance(p, dict) else p for p in pages if not (isinstance(p, dict) and p.get("deleted"))), None)
    pen = (content.get("extraMetadata") or {}).get("LastPen", "")
    if pen and "marker" not in pen.lower():
        print(f"🖊️  The page's pen is {pen}: the single-line digits look best with the Marker, select it on that page", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        bake_dash_app(out)
        bake_font_atlases(out)
        render_sleep_backgrounds(out, loc.get("name", ""), tasks)
        run_ssh("test -f /usr/share/remarkable/suspended.png.original || cp /usr/share/remarkable/suspended.png /usr/share/remarkable/suspended.png.original", host=host)
        shutil.move(str(stock / "pages"), str(out / "pages"))
        shutil.rmtree(stock, ignore_errors=True)
        if not (no_push and existing):
            (out / "printed").write_text(f"{datetime.now().strftime('%Y-%m-%d')} {int(time.time())}\n")   # else the tablet keeps its own
        (out / "config").write_text(f"doc={doc_uuid}\nrm={REMOTE_PATH}/{doc_uuid}/{page_id}.rm\npdf={REMOTE_PATH}/{doc_uuid}.pdf\n"
                                    f"lat={loc.get('lat', 0)}\nlon={loc.get('lon', 0)}\ncity={loc.get('name', '')}\nminutes=5\n"
                                    f"sleep=/usr/share/remarkable/suspended.png\n")
        if token:
            (out / "token").write_text(token)
            os.chmod(out / "token", 0o600)
        has_token = bool(token) or run_ssh(f"test -f {TABLET_DASH_DIR}/token && echo yes || true", host=host).strip() == "yes"
        if not has_token:
            (out / "usage").write_text(usage_file_text(fetch_claude_subscription_usage()))
        if os.path.exists("/etc/localtime"):
            shutil.copy("/etc/localtime", out / "localtime")
        shutil.copy(binary, out / "rmdash")
        (out / "rmdash.service").write_text(RMCLOCK_UNIT.replace("rmclock", "rmdash").replace(TABLET_APP_DIR, TABLET_DASH_DIR)
                                            .replace("its Clock document", "its Dashboard page"))
        print(f"📦 Installing {len(list(out.glob('*.bin')))} baked strokes, {DASH_STOCK_DAYS + 1} daily pages and sleep screens, "
              f"{len(SLEEP_FONTS)} fonts and the dashboard program on the tablet...", flush=True)
        # a token the tablet has been renewing itself is newer than any copy on the PC: keep it unless one is
        # given; without a push the printed page is still the one on the tablet, so keep its date too
        keep = "token printed"   # never `state`: a reinstall may change how things are drawn, so the page is redone
        run_ssh(f"cd {TABLET_DASH_DIR} 2>/dev/null && for f in {keep}; do cp $f /tmp/rmdash.$f 2>/dev/null; done; "
                f"rm -rf {TABLET_DASH_DIR}; mkdir -p {TABLET_DASH_DIR}; for f in {keep}; do mv /tmp/rmdash.$f {TABLET_DASH_DIR}/$f 2>/dev/null; done; true", host=host)
        tar = subprocess.run(["tar", "-C", str(out), "-cf", "-", "."], capture_output=True, check=True).stdout
        subprocess.run(["ssh"] + get_ssh_base_opts() + [host, f"tar -C {TABLET_DASH_DIR} -xf -"], input=tar, check=True)
    run_ssh(f"chmod +x {TABLET_DASH_DIR}/rmdash && chmod 600 {TABLET_DASH_DIR}/token 2>/dev/null; mv {TABLET_DASH_DIR}/rmdash.service /etc/systemd/system/ && "
            "systemctl daemon-reload && systemctl enable rmdash >/dev/null 2>&1 && systemctl restart rmdash", host=host)
    cfg["dash_app"] = {"feed": not has_token, "city": city, "tasks": [t for t, _ in tasks] if tasks else None, "sleep": True}
    save_config(cfg)
    how = "fetches its Claude usage itself" if has_token else "gets its Claude usage from this PC's dashboard cron (no tablet login given)"
    print(f"✅ Dashboard installed: open '{doc_title}' on the tablet and it draws the time, weather and usage by itself,"
          f" swaps in each day's printed page and paints the sleep screen itself ({DASH_STOCK_DAYS} days on board); it {how}. "
          f"Log: ssh {host} journalctl -u rmdash -f")


def top_up_dash_pages(host, city, tasks):
    """Keep the tablet's stocks of printed pages and sleep screens DASH_STOCK_DAYS long (no reload)."""
    have = set(run_ssh(f"cd {TABLET_DASH_DIR} 2>/dev/null && ls pages sleep 2>/dev/null; true", host=host).split())
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        render_dash_pages(out, city, tasks)
        render_sleep_backgrounds(out, city, tasks)
        new = [f"{d}/{p.name}" for d in ("pages", "sleep") for p in (out / d).iterdir() if p.name not in have]
        if not new:
            return
        tar = subprocess.run(["tar", "-C", str(out), "-cf", "-"] + new, capture_output=True, check=True).stdout
        subprocess.run(["ssh"] + get_ssh_base_opts() + [host, f"tar -C {TABLET_DASH_DIR} -xf -"], input=tar, check=True)
        print(f"📅 {len(new)} new daily pages/sleep screens added to the tablet dashboard's stock")


def uninstall_dash_app(host):
    run_ssh(f"systemctl disable --now rmdash >/dev/null 2>&1; rm -f /etc/systemd/system/rmdash.service; systemctl daemon-reload; rm -rf {TABLET_DASH_DIR}", host=host)
    cfg = load_config()
    cfg.pop("dash_app", None)
    save_config(cfg)
    print("🗑️  Dashboard removed from the tablet (the Dashboard page is kept)")


def cmd_clock(args):
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # so the stylus is closed (pen lifted) when killed
    cfg = load_config()
    saved = cfg.get("clock_defaults", {})
    # flag given on the command line > saved default > built-in default
    opts = {k: getattr(args, k) if getattr(args, k) is not None else saved.get(k)
            for k in ("pos", "size", "thickness", "pressure", "pen_width", "ink_width", "frame", "interval")}
    # built-in defaults: the look tuned on an rM2 with the pencil at full pressure (see README)
    builtin = {"pos": "center", "size": "large", "thickness": 28, "pressure": 4000,
               "pen_width": 12, "ink_width": 70, "frame": True, "interval": 2.0}
    for k, v in builtin.items():
        if opts[k] is None:
            opts[k] = v
    if opts["pen_width"] == 0:          # --pen-width 0: measure even if a default width is set
        opts["pen_width"] = None
    if args.save_defaults:
        cfg["clock_defaults"] = {k: v for k, v in opts.items() if v is not None}
        save_config(cfg)
        print(f"💾 Saved as defaults for 'rm-ai clock': {cfg['clock_defaults']}")
        return
    host = get_active_host(args.device)
    if args.uninstall:
        uninstall_clock_app(host)
        return
    if args.install and opts["pen_width"] is None:
        print("❌ --install needs a known pen width (--pen-width), the tablet cannot measure the pen by itself")
        return
    try:
        clock = DigitalClock(host=host, pos=opts["pos"], format=args.format, size=opts["size"],
                             thickness=opts["thickness"], pressure=opts["pressure"], pen_width=opts["pen_width"],
                             frame=bool(opts["frame"]), ink_width=opts["ink_width"])
    except ValueError as e:
        print(f"❌ {e}")
        return
    if args.install:
        fmt = {"MM:SS": "%M%S", "HH:MM": "%H%M"}.get(args.format, "%H%M%S")
        install_clock_app(host, clock, args.doc, opts["interval"], fmt)
        return
    clock.run(duration=args.duration, clear_on_exit=args.clear, once=args.once, interval=opts["interval"], slow=args.slow, step=args.step)


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

def summarize_claude_usage(cost_report, usage_report, today):
    """Reduce the Admin API cost and usage reports (daily buckets) to the numbers the dashboard shows.
    Amounts are decimal strings in cents; `today` is a UTC date."""
    month_cents = today_cents = 0.0
    for bucket in cost_report.get("data", []):
        day = bucket["starting_at"][:10]
        cents = sum(float(r["amount"]) for r in bucket.get("results", []))
        if day[:7] == today.strftime("%Y-%m"):
            month_cents += cents
        if day == today.isoformat():
            today_cents += cents
    tokens_in = tokens_out = 0
    for bucket in usage_report.get("data", []):
        for r in bucket.get("results", []):
            cc = r.get("cache_creation") or {}
            tokens_in += r.get("uncached_input_tokens", 0) + r.get("cache_read_input_tokens", 0) + sum(cc.values())
            tokens_out += r.get("output_tokens", 0)
    return {"month_usd": month_cents / 100, "today_usd": today_cents / 100, "tokens_in": tokens_in, "tokens_out": tokens_out}


def fetch_claude_usage(days=30):
    """Month-to-date and today's spend plus 30-day token totals from the Anthropic Usage & Cost Admin API.
    Needs ANTHROPIC_ADMIN_KEY (an Admin API key, sk-ant-admin...; the Admin API needs an organization,
    not an individual account). Returns None without a key, or {"error": ...} on failure."""
    import urllib.request
    import urllib.parse
    key = os.getenv("ANTHROPIC_ADMIN_KEY")
    if not key:
        return None
    end = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    start = end - timedelta(days=days)
    params = {"starting_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "ending_at": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "bucket_width": "1d", "limit": 31}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "User-Agent": "remarkable-ai/0.1 (https://github.com/Khoshkhah/remarkable-ai)"}
    try:
        reports = []
        for path in ("cost_report", "usage_report/messages"):
            url = f"https://api.anthropic.com/v1/organizations/{path}?{urllib.parse.urlencode(params)}"
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
                reports.append(json.load(r))
        return summarize_claude_usage(reports[0], reports[1], datetime.utcnow().date())
    except Exception as e:
        return {"error": str(e)[:80]}


def fetch_claude_subscription_usage():
    """The three windows `/usage` shows in Claude Code — session (5 h), week, week for Fable/Opus — as
    utilization percentages of the claude.ai subscription Claude Code is logged in with, read from the
    OAuth usage endpoint with the token Claude Code keeps in ~/.claude/.credentials.json.
    Returns {"windows": [(label, percent, resets_at_iso)]}, {"error": ...}, or None when not logged in."""
    import urllib.request
    path = os.path.expanduser("~/.claude/.credentials.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            token = json.load(f)["claudeAiOauth"]["accessToken"]
        req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
            "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
            "anthropic-version": "2023-06-01", "User-Agent": "remarkable-ai/0.1 (https://github.com/Khoshkhah/remarkable-ai)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.load(r)
    except Exception as e:
        return {"error": str(e)[:80]}
    # `limits` is the list /usage in Claude Code shows: kind session / weekly_all / weekly_scoped (the
    # per-model weekly window, whose scope names the model, e.g. "Fable"); the top-level dicts are
    # the same data under codenames and serve as the fallback
    windows = []
    for lim in body.get("limits") or []:
        kind, pct = lim.get("kind"), lim.get("percent")
        if pct is None:
            continue
        if kind == "session":
            label = "Session"
        elif kind == "weekly_all":
            label = "Week"
        elif kind == "weekly_scoped":
            label = "Week " + (((lim.get("scope") or {}).get("model") or {}).get("display_name") or "model")
        else:
            label = kind.replace("_", " ").title()[:12]
        windows.append((label, pct, lim.get("resets_at")))
    if not windows:
        labels = {"five_hour": "Session", "seven_day": "Week"}
        windows = [(labels.get(k, k.replace("_", " ").title()[:12]), v.get("utilization"), v.get("resets_at"))
                   for k, v in body.items() if isinstance(v, dict) and v.get("utilization") is not None]
    if not windows:
        return {"error": "unexpected response, keys: " + ", ".join(list(body)[:6])}
    return {"windows": windows[:3], "all_keys": [lim.get("kind") for lim in body.get("limits") or []] or list(body)}


CLAUDE_OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
CLAUDE_CODE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"   # Claude Code's public OAuth client id


def refresh_claude_token(refresh_token):
    """Renew a Claude Code login the way Claude Code does; returns the token endpoint's JSON
    ({access_token, refresh_token, expires_in, ...}) or raises with the HTTP status and body."""
    import urllib.request
    import urllib.error
    body = json.dumps({"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLAUDE_CODE_CLIENT_ID}).encode()
    req = urllib.request.Request(CLAUDE_OAUTH_TOKEN_URL, data=body, headers={"Content-Type": "application/json", "Accept": "application/json",
                                 "User-Agent": "remarkable-ai/0.1 (https://github.com/Khoshkhah/remarkable-ai)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"token endpoint HTTP {e.code}: {e.read()[:200].decode(errors='replace')}")


def save_claude_credentials(path, tokens):
    """Write renewed tokens into Claude Code's credential file atomically, keeping every other field."""
    import stat
    with open(path) as f:
        creds = json.load(f)
    o = creds["claudeAiOauth"]
    o["accessToken"] = tokens["access_token"]
    if tokens.get("refresh_token"):
        o["refreshToken"] = tokens["refresh_token"]
    if tokens.get("expires_in"):
        o["expiresAt"] = int(time.time() * 1000) + int(tokens["expires_in"]) * 1000
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(creds, f)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, path)




WEATHER_TEXT = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
                51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
                61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
                71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Showers", 81: "Showers",
                82: "Heavy showers", 85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm",
                96: "Thunderstorm, hail", 99: "Thunderstorm, hail"}   # WMO weather codes as Open-Meteo reports them


def summarize_weather(data, name):
    """Reduce an Open-Meteo forecast response to what the dashboard shows."""
    cur, day = data["current"], data["daily"]
    days = [{"dow": datetime.fromisoformat(date).strftime("%a").upper(),
             "text": WEATHER_TEXT.get(day["weather_code"][i], "?"),
             "max": day["temperature_2m_max"][i], "min": day["temperature_2m_min"][i],
             "pop": (day.get("precipitation_probability_max") or [None] * 9)[i],
             "sunrise": day["sunrise"][i][11:16], "sunset": day["sunset"][i][11:16]}
            for i, date in enumerate(day["time"])]
    return {"name": name, "temp": cur["temperature_2m"], "feels": cur["apparent_temperature"],
            "text": WEATHER_TEXT.get(cur["weather_code"], "?"), "wind": cur["wind_speed_10m"], "days": days}


def fetch_weather(city):
    """Current weather and a 5-day forecast for `city` from Open-Meteo (free, no key). The city is
    geocoded once and the coordinates kept in the config. Returns None without a city, {"error": ..}
    on failure."""
    import urllib.request
    import urllib.parse
    if not city:
        return None
    cfg = load_config()
    loc = cfg.get("weather_location") or {}
    try:
        if loc.get("city", "").lower() != city.lower():
            url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": city, "count": 1, "language": "en"})
            with urllib.request.urlopen(url, timeout=15) as r:
                hits = json.load(r).get("results") or []
            if not hits:
                return {"error": f"city not found: {city}"}
            loc = {"city": city, "name": hits[0]["name"], "lat": hits[0]["latitude"], "lon": hits[0]["longitude"]}
            cfg["weather_location"] = loc
            save_config(cfg)
        params = {"latitude": loc["lat"], "longitude": loc["lon"],
                  "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                  "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
                  "timezone": "auto", "forecast_days": 6, "wind_speed_unit": "ms"}
        with urllib.request.urlopen("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params), timeout=15) as r:
            return summarize_weather(json.load(r), loc["name"])
    except Exception as e:
        return {"error": str(e)[:80]}


def render_dashboard_image(battery_info=(None, None), tasks=None, quote=None, habits=None, time_format="24h", claude_usage=None, subscription=None, live=False, weather=None, notes=False, clock=False, now=None, pen_weather=False, stamp=False):
    """`now` renders the page for another day (the tablet dashboard keeps a stock of coming days);
    `pen_weather` prints only the weather header, the tablet adds the block itself; `stamp` renders a
    sleep-screen background with every live value left blank (header, weather, usage rows, footer
    time): the tablet stamps them on with SLEEP_FONTS glyphs (app/rmdash.c compose_sleep)."""
    from PIL import Image, ImageDraw
    im = Image.new("L", (1404, 1872), 255)
    draw = ImageDraw.Draw(im)
    now = now or datetime.now()

    # 1. Top status bar
    draw.line([(80, 100), (1324, 100)], fill=0, width=2)
    f_top = get_font(22, bold=True)
    bat_pct, bat_stat = battery_info
    if bat_pct is not None:
        stat_str = f" • {bat_stat.upper()}" if bat_stat else ""
        left_header = f"REMARKABLE 2 • {bat_pct}% BATTERY{stat_str} • ONLINE"
    else:
        left_header = "REMARKABLE 2 • E-INK EXECUTIVE DESK DISPLAY"
    if not stamp:
        draw.text((80, 68), left_header, font=f_top, fill=0)

    day_of_year = now.strftime("%j")
    week_num = now.strftime("%V")
    draw.text((1060, 68), f"WEEK {week_num} • DAY {day_of_year}", font=f_top, fill=0)

    # 2. Hero Clock & Date
    if live:
        # the live page leaves the top blank: the pen draws HH:MM there and updates it every minute
        draw.text((80, 325), now.strftime("%A, %B %d, %Y").upper(), font=get_font(38, bold=True), fill=0)
    elif clock:
        # a pushed document: the big time is the time of the push (it cannot tick), date below it
        time_str = now.strftime("%I:%M %p").lstrip("0") if time_format == "12h" else now.strftime("%H:%M")
        draw.text((80, 115), time_str, font=get_font(190, bold=True), fill=0)
        draw.text((80, 325), now.strftime("%A, %B %d, %Y").upper(), font=get_font(38, bold=True), fill=0)
    else:
        # the sleep screen is painted once when the tablet falls asleep, so no clock: the date takes
        # the space instead (the footer's "Updated" stamp says when it was rendered)
        draw.text((80, 120), now.strftime("%A").upper(), font=get_font(110, bold=True), fill=0)
        draw.text((80, 260), now.strftime("%B %d, %Y").upper(), font=get_font(64, bold=True), fill=0)
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

    # Claude API usage, just under the calendar (fits the 6-row months too)
    y_use = y_cal + 8
    if live or stamp:
        pass   # the live pages have the usage box; the Admin API spend line is the PC's sleep screen's
    elif claude_usage is None:
        draw.text((80, y_use), "CLAUDE API", font=get_font(20, bold=True), fill=0)
        draw.text((250, y_use), "spend needs ANTHROPIC_ADMIN_KEY", font=get_font(18), fill=110)
    elif "error" in claude_usage:
        draw.text((80, y_use), "CLAUDE API", font=get_font(20, bold=True), fill=0)
        draw.text((250, y_use), f"unavailable: {claude_usage['error'][:34]}", font=get_font(18), fill=110)
    else:
        draw.text((80, y_use), "CLAUDE API", font=get_font(20, bold=True), fill=0)
        u = claude_usage
        draw.text((250, y_use), f"{now.strftime('%b').upper()} ${u['month_usd']:.2f}  •  TODAY ${u['today_usd']:.2f}", font=get_font(20, bold=True), fill=0)
        draw.text((80, y_use + 28), f"last 30 days: {u['tokens_in'] / 1e6:.1f}M tokens in  •  {u['tokens_out'] / 1e6:.2f}M out", font=get_font(18), fill=0)

    # Claude usage box (session / week / week Fable, like `/usage` in Claude Code); the quote box otherwise
    if live:
        # Live page: printed labels, empty bar outlines and a "%" per row; the pen fills the bars and
        # draws the percentages as 7-segment digits (UsageWidget), so no reload is ever needed
        draw.rounded_rectangle([(80, 920), (550, 1560)], radius=12, outline=0, width=3)
        draw.text((105, 940), "CLAUDE USAGE", font=get_font(22, bold=True), fill=0)
        draw.line([(105, 975), (525, 975)], fill=200, width=1)
        for label, y_row in zip(("SESSION", "WEEK", "WEEK FABLE"), LIVE_USAGE_ROWS):
            draw.text((105, y_row), label, font=get_font(22, bold=True), fill=0)
            draw.rectangle([(LIVE_BAR_X[0] - 4, y_row), (LIVE_BAR_X[1] + 4, y_row + 26)], outline=0, width=2)
            draw.text((268, y_row + 128), "%", font=get_font(34, bold=True), fill=0)   # right after the pen-drawn number
    elif stamp or (subscription and "windows" in subscription):
        draw.rounded_rectangle([(80, 920), (550, 1180)], radius=12, outline=0, width=3)
        draw.text((105, 940), "CLAUDE USAGE", font=get_font(22, bold=True), fill=0)
        draw.line([(105, 975), (525, 975)], fill=200, width=1)
        y_row = 995
        if stamp:
            subscription = {"windows": []}   # rows stamped by the tablet (compose_sleep in app/rmdash.c)
        for label, pct, resets in subscription["windows"]:
            pct = max(0.0, min(100.0, float(pct)))
            draw.text((105, y_row), label.upper(), font=get_font(18, bold=True), fill=0)
            draw.rectangle([(250, y_row + 2), (470, y_row + 20)], outline=0, width=2)
            draw.rectangle([(252, y_row + 4), (252 + int(216 * pct / 100), y_row + 18)], fill=0)
            draw.text((480, y_row), f"{pct:.0f}%", font=get_font(18, bold=True), fill=0)
            if resets:
                try:
                    local = datetime.fromisoformat(resets.replace("Z", "+00:00")).astimezone()
                    draw.text((250, y_row + 26), "resets " + local.strftime("%a %H:%M"), font=get_font(15), fill=110)
                except ValueError:
                    pass
            y_row += 58
    else:
        draw.rounded_rectangle([(80, 920), (550, 1180)], radius=12, outline=0, width=3)
        draw.text((105, 940), "DAILY FOCUS", font=get_font(22, bold=True), fill=0)
        draw.line([(105, 975), (525, 975)], fill=200, width=1)
        if subscription and "error" in subscription:
            quote = "Claude usage unavailable:\n" + subscription["error"][:60]
        elif not quote:
            quote = "Simplicity is the ultimate\nsophistication.\n\nMake each stroke count."
        draw.multiline_text((105, 1000), quote, font=get_font(22, bold=False), fill=0, spacing=8)

    # Daily Habits Tracker (the live page's usage box takes this space)
    if not live:
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

    # 5. Right Column: weather (when a city is set), then priorities, then ruled notes (only without weather)
    y_task = 490
    if pen_weather or stamp:   # the tablet prints/stamps the weather block itself (app/rmdash.c), same rows and sizes as below
        draw.text((630, 425), f"WEATHER  •  {(weather or {}).get('name', '').upper()}".rstrip(" •"), font=f_sec, fill=0)
        draw.line([(630, 465), (1324, 465)], fill=0, width=2)
        draw.text((630, 885), "PRIORITIES & ACTION ITEMS", font=f_sec, fill=0)
        draw.line([(630, 925), (1324, 925)], fill=0, width=2)
        y_task = 950
    elif weather and "days" in weather:
        today, coming = weather["days"][0], weather["days"][1:6]
        draw.text((630, 425), f"WEATHER  •  {weather['name'].upper()}", font=f_sec, fill=0)
        draw.line([(630, 465), (1324, 465)], fill=0, width=2)
        draw.text((630, 478), f"{round(weather['temp'])}°", font=get_font(96, bold=True), fill=0)
        draw.text((840, 495), weather["text"], font=get_font(32, bold=True), fill=0)
        pop = f"  •  rain {today['pop']}%" if today.get("pop") is not None else ""
        draw.text((840, 545), f"feels {round(weather['feels'])}°  •  wind {weather['wind']:.0f} m/s{pop}", font=get_font(22), fill=0)
        draw.text((630, 605), f"TODAY  H {round(today['max'])}°  L {round(today['min'])}°   •   sunrise {today['sunrise']}   sunset {today['sunset']}",
                  font=get_font(22, bold=True), fill=0)
        y_row = 655
        for d in coming:
            draw.text((630, y_row), d["dow"], font=get_font(22, bold=True), fill=0)
            draw.text((720, y_row), d["text"], font=get_font(22), fill=0)
            draw.text((1010, y_row), f"{round(d['max'])}° / {round(d['min'])}°", font=get_font(22), fill=0)
            if d.get("pop") is not None:
                draw.text((1210, y_row), f"{d['pop']}%", font=get_font(22), fill=0)
            draw.line([(630, y_row + 34), (1324, y_row + 34)], fill=220, width=1)
            y_row += 42
        draw.text((630, y_row + 20), "PRIORITIES & ACTION ITEMS", font=f_sec, fill=0)
        draw.line([(630, y_row + 60), (1324, y_row + 60)], fill=0, width=2)
        y_task = y_row + 85
    else:
        if weather and "error" in weather:
            draw.text((630, 400), f"weather unavailable: {weather['error'][:50]}", font=get_font(18), fill=110)
        draw.text((630, 425), "PRIORITIES & ACTION ITEMS", font=f_sec, fill=0)
        draw.line([(630, 465), (1324, 465)], fill=0, width=2)

    if not tasks:
        tasks = [
            ("Review high-priority project notes", False),
            ("Execute focused deep work sprint", False),
            ("Sync handwriting & diagrams to AI workspace", True),
            ("Plan tomorrow's key deliverables", False),
        ]

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

    if notes and not live:
        # Handwriting & Quick Notes ruled section: only on a page that can be written on
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
    if not stamp:
        draw.text((1080, 1735), f"Updated: {ts_str}", font=f_foot, fill=0)

    return im

def sweep_path(x0, y0, x1, y1, lane=14):
    """One serpentine eraser path over a rectangle: vertical lanes `lane` px apart (the eraser is ~17 px wide)."""
    pts = []
    for i, x in enumerate(range(int(x0), int(x1) + 1, lane)):
        pts += [(x, y0), (x, y1)] if i % 2 == 0 else [(x, y1), (x, y0)]
    return pts


def text_strokes(text, size, x, y, pitch, bold=True):
    """Pen strokes that fill `text`, rendered in the dashboard font at (x, y): one horizontal run per
    dark span on every `pitch`-th pixel row, so the pen reproduces the printed glyph shapes."""
    from PIL import Image, ImageDraw
    font = get_font(size, bold=bold)
    left, top, right, bottom = font.getbbox(text)
    img = Image.new("L", (right + 2, bottom + 2), 255)
    ImageDraw.Draw(img).text((0, 0), text, font=font, fill=0)
    px = img.load()
    strokes = []
    for yy in range(top + pitch // 2, bottom, pitch):
        run = None
        for xx in range(right + 2):
            dark = px[xx, yy] < 128
            if dark and run is None:
                run = xx
            elif not dark and run is not None:
                if xx - run >= 3:
                    strokes.append([(x + run, y + yy), (x + xx - 1, y + yy)])
                run = None
    return strokes


def wait_until_open(host, doc_uuid, what="the document"):
    """Block until `doc_uuid` is the document on screen (polls xochitl's LastOpen every 3 s)."""
    if open_document(host) == doc_uuid:
        return
    print(f"⏳ Waiting for {what} to be open on the tablet...", flush=True)
    while open_document(host) != doc_uuid:
        time.sleep(3)
    print(f"▶ {what} is open", flush=True)


class TouchWatcher:
    """Notes the last time a finger touched the screen (pages are turned by finger); the live
    dashboard then requires a fresh confirmation before drawing again."""

    def __init__(self, host):
        self.touched_at = 0.0
        self.proc = subprocess.Popen(["ssh"] + get_ssh_base_opts() + [host, READER_CMD.replace("event1", "event2")],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        import threading
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        while self.proc and self.proc.poll() is None:
            if len(self.proc.stdout.read(16)) < 16:
                break
            self.touched_at = time.time()

    def close(self):
        if self.proc:
            self.proc.kill()
            self.proc = None


def open_document(host):
    """UUID of the document open on the tablet: xochitl writes it to its config as LastOpen."""
    try:
        line = run_ssh("grep -m1 '^LastOpen=' /home/root/.config/remarkable/xochitl.conf", host=host)
        m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})", line)
        return m.group(1) if m else None
    except Exception:
        return None




def run_live_dashboard(host, usage_minutes, doc_uuid, repush=None):
    """Keep the dashboard page current with the pen, in the page's own font: every minute sweep the
    clock zone clean and write HH:MM again; every `usage_minutes` do the same for usage rows whose
    value changed. All erasing first, then one long pause (xochitl drops strokes while still busy
    erasing, longest on PDF pages), then all drawing. It only ever draws while xochitl reports the
    dashboard as the open document: checked before each cycle, after any finger touch, and, through
    the stylus guard, after any real-pen activity mid-cycle."""
    saved = load_config().get("clock_defaults", {})
    pressure = max(1, min(4095, saved.get("pressure", 2500)))
    ink = saved.get("ink_width") or saved.get("pen_width") or 12
    pitch = max(6, int(ink) // 7)             # row spacing of the fill strokes: the pencil (~70px halo) -> 10px, ballpoint -> 6px
    stylus = VirtualStylus(host=host)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    stylus.connect()
    touch = TouchWatcher(host)
    stylus.guard = lambda: open_document(host) == doc_uuid
    st = {"shown": None, "rows": [], "rows_shown": [None, None, None], "last_usage": 0.0,
          "checked_at": time.time(), "date": datetime.now().date(), "doc": doc_uuid}

    def reset_lines(resets_at):
        """('TUE', '08:00') in local time from the endpoint's ISO timestamp, or () when unknown."""
        try:
            local = datetime.fromisoformat(resets_at.replace("Z", "+00:00")).astimezone()
            return (local.strftime("%a").upper(), local.strftime("%H:%M"))
        except (AttributeError, ValueError, TypeError):
            return ()

    def cycle(time_str, changed_rows):
        if time_str == st["shown"] and not changed_rows:
            return
        # 1. all erasing: one sweep per zone
        if time_str != st["shown"]:
            stylus.stroke(sweep_path(*LIVE_CLOCK_ZONE), is_eraser=True, pressure=4000)
        for i in changed_rows:
            y = LIVE_USAGE_ROWS[i]
            stylus.stroke(sweep_path(95, y - 4, 535, y + 185), is_eraser=True, pressure=4000)
        # 2. let xochitl finish erasing before any pen stroke
        stylus.hover((LIVE_CLOCK_ZONE[0], LIVE_CLOCK_ZONE[1]), DigitalClock.START_SETTLE)
        # 3. all drawing, in the page's font
        if time_str != st["shown"]:
            for path in text_strokes(time_str, 190, 80, 115, pitch):
                stylus.stroke(path, pressure=pressure)
            st["shown"] = time_str
            print(f"  ⏰ {time_str}", flush=True)
        for i in changed_rows:
            y, (pct, reset) = LIVE_USAGE_ROWS[i], st["rows"][i]
            if pct > 0:
                x0, x1 = LIVE_BAR_X
                stylus.stroke([(x0, y + 13), (x0 + (x1 - x0) * pct / 100, y + 13)], pressure=pressure)
            for path in text_strokes(str(pct), 110, 105, y + 45, pitch):
                stylus.stroke(path, pressure=pressure)
            for line, text in enumerate(reset):   # e.g. TUE / 08:00, smaller, right of the "%"
                # lighter pressure: the pencil line gets thin enough for 44px letters to stay legible
                for path in text_strokes(text, 44, 335, y + 60 + 52 * line, max(4, pitch // 2)):
                    stylus.stroke(path, pressure=max(800, int(pressure * 0.55)))
            st["rows_shown"][i] = st["rows"][i]
        if changed_rows:
            print("  🤖 " + "  •  ".join(f"{'Session' if i == 0 else 'Week' if i == 1 else 'Week Fable'} {p}%" for i, (p, _) in enumerate(st["rows"])), flush=True)

    try:
        while True:
            if repush and datetime.now().date() != st["date"]:
                # the printed date, calendar and week number are stale: re-render and push the template
                # (one reload a day), then draw nothing until the dashboard is the open document again
                print("  📅 New day: pushing a fresh template (the tablet reloads once)...", flush=True)
                stylus.close()
                st["doc"] = doc_uuid = repush()
                stylus.guard = lambda: open_document(host) == doc_uuid
                st["date"] = datetime.now().date()
                wait_until_open(host, doc_uuid, "the dashboard")
                stylus.connect()
                st["shown"], st["rows_shown"] = None, [None, None, None]   # the fresh page has no strokes
            if touch.touched_at > st["checked_at"] or open_document(host) != doc_uuid:   # never draw elsewhere
                wait_until_open(host, doc_uuid, "the dashboard")
            st["checked_at"] = time.time()
            time_str = datetime.now().strftime("%H:%M")
            changed_rows = []
            if time.time() - st["last_usage"] >= usage_minutes * 60:
                sub = fetch_claude_subscription_usage()
                if sub and "windows" in sub:
                    st["rows"] = [(max(0, min(100, round(float(p)))), reset_lines(r)) for _, p, r in sub["windows"]][:3]
                    changed_rows = [i for i, row in enumerate(st["rows"]) if st["rows_shown"][i] != row]
                else:
                    print(f"  ⚠️  usage not updated: {(sub or {}).get('error', 'no Claude Code login')}", flush=True)
                st["last_usage"] = time.time()
            try:
                cycle(time_str, changed_rows)
            except PageChanged:
                print("⏸ the page changed mid-cycle; everything is redrawn once the dashboard is back", flush=True)
                st["shown"], st["rows_shown"] = None, [None, None, None]
            time.sleep(max(1, 60 - time.time() % 60))
    except KeyboardInterrupt:
        print("\nLive dashboard stopped.", flush=True)
    finally:
        touch.close()
        stylus.close()


# ==============================================================================
# Chat on the page: read the real pen live, a box around handwriting means "sent"
# ==============================================================================

def digitizer_to_display(abs_x, abs_y):
    """Inverse of VirtualStylus.display_to_digitizer."""
    return abs_y * 1404 / 15725, 1872 - abs_x * 1872 / 20966


class PenReader:
    """Streams the tablet's digitizer and hands every finished real-pen stroke, as display-coordinate
    points, to `on_stroke`. Eraser strokes are dropped."""

    def __init__(self, host, on_stroke):
        self.host, self.on_stroke = host, on_stroke
        self.proc = None

    def run(self):
        self.proc = subprocess.Popen(["ssh"] + get_ssh_base_opts() + [self.host, READER_CMD], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        x = y = 0
        touching, eraser, pts = False, False, []
        try:
            while True:
                ev = self.proc.stdout.read(16)
                if len(ev) < 16:
                    break
                _, _, ev_type, code, value = struct.unpack("<IIHHi", ev)
                if ev_type == EV_ABS and code == ABS_X:
                    x = value
                elif ev_type == EV_ABS and code == ABS_Y:
                    y = value
                elif ev_type == EV_KEY and code == BTN_TOOL_RUBBER:
                    eraser = bool(value)
                elif ev_type == EV_KEY and code == BTN_TOUCH:
                    if value and not touching:
                        pts = []
                    elif not value and touching and len(pts) > 1 and not eraser:
                        self.on_stroke(pts)
                    touching = bool(value)
                elif ev_type == EV_SYN and touching:
                    pts.append(digitizer_to_display(x, y))
        finally:
            self.close()

    def close(self):
        if self.proc:
            self.proc.kill()      # the remote reader kills itself when the connection closes
            self.proc = None


def is_box(pts):
    """A closed loop big enough to surround a message: a rectangle, a rounded box or an oval drawn
    around handwriting all count. Open shapes and scribbles do not."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if w < 80 or h < 40:
        return False
    if math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) > max(40, 0.2 * math.hypot(w, h)):
        return False   # not closed
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    return 0.6 <= length / (2 * (w + h)) <= 1.6   # one lap around, not a scribble or a double loop


def inside(pt, loop):
    """Point-in-polygon (ray casting) so an oval selects only what is really inside it."""
    x, y = pt
    hit = False
    for (ax, ay), (bx, by) in zip(loop, loop[1:] + loop[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


def render_strokes(strokes, box, path, scale=2):
    """Draw `strokes` (inside `box` = (x0, y0, x1, y1)) into a PNG at `path`."""
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = box
    pad = 20
    im = Image.new("L", (int((x1 - x0 + 2 * pad) * scale), int((y1 - y0 + 2 * pad) * scale)), 255)
    d = ImageDraw.Draw(im)
    for pts in strokes:
        line = [((px - x0 + pad) * scale, (py - y0 + pad) * scale) for px, py in pts]
        if len(line) > 1:
            d.line(line, fill=0, width=3 * scale, joint="curve")
    im.save(path)


CHAT_PAGE = """<!doctype html><meta charset="utf-8"><title>reMarkable chat</title>
<style>body{font-family:sans-serif;max-width:900px;margin:2em auto;background:#f4f4f4}
.box{background:#fff;border:1px solid #ccc;border-radius:8px;padding:1em;margin:1em 0}
.box img{max-width:100%;border:1px solid #eee} .meta{color:#666;font-size:.9em}</style>
<h1>reMarkable chat <span class="meta" id="n"></span></h1><div id="list"></div>
<script>
let shown = '';
async function poll(){
  try{ const r = await fetch('boxes.json?'+Date.now()); const boxes = await r.json();
    const sig = boxes.length + ':' + (boxes.length ? boxes[boxes.length-1].n + '/' + boxes[boxes.length-1].time + '/' + (boxes[boxes.length-1].reply||'') : '');
    if(sig !== shown){ shown = sig;
      document.getElementById('n').textContent = boxes.length + ' message' + (boxes.length==1?'':'s');
      document.getElementById('list').innerHTML = boxes.slice().reverse().map(b =>
        `<div class="box"><div class="meta">#${b.n} · ${b.time} · ${b.strokes} strokes · box ${b.box.map(Math.round).join(',')}</div>
         <img src="${b.image}?${Date.now()}">${b.text ? '<p><b>Read:</b> '+b.text+'</p>' : ''}${b.reply ? '<p><b>Reply:</b> '+b.reply+'</p>' : ''}</div>`).join(''); }
  }catch(e){}
  setTimeout(poll, 2000); }
poll();
</script>"""


def push_chat_document(host, title, pages=10, folder=None):
    """Push `pages` blank pages (a small header each) as the chat document; returns its uuid."""
    from PIL import Image, ImageDraw
    ims = []
    for i in range(pages):
        im = Image.new("L", (1404, 1872), 255)
        d = ImageDraw.Draw(im)
        d.text((80, 60), f"{title.upper()}  •  page {i + 1}", font=get_font(22, bold=True), fill=120)
        d.line([(80, 100), (1324, 100)], fill=200, width=2)
        ims.append(im)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf = f.name
    ims[0].save(pdf, "PDF", resolution=226.0, save_all=True, append_images=ims[1:])
    uuid_ = cmd_push(argparse.Namespace(file=pdf, folder=folder, title=title, force_new=False, margins=0, fresh=True, device=None))
    os.unlink(pdf)
    return uuid_


# ==============================================================================
# Vocabulary: what you highlight on a PDF or box in a notebook, explained in simple English and Farsi
# ==============================================================================

def read_highlights(raw):
    """(page-order start, text) of the highlighter marks in a .rm v6 page: xochitl stores the highlighted
    text of a PDF/EPUB page itself, so a highlighted word or paragraph needs no recognition."""
    import io
    from rmscene import read_blocks
    from rmscene.scene_stream import SceneGlyphItemBlock
    out = []
    for b in read_blocks(io.BytesIO(raw)):
        if isinstance(b, SceneGlyphItemBlock) and b.item.value is not None and getattr(b.item.value, "text", None):
            out.append((b.item.value.start or 0, " ".join(b.item.value.text.split())))
    return out


def read_marked_ink(raw):
    """Handwriting under highlighter strokes in a .rm v6 page: for each highlighter stroke, the box it
    covers (display px) and the pen strokes mostly inside that box. A highlighter over printed text
    becomes a text highlight instead (read_highlights); over handwriting it is a stroke like any other."""
    import io
    from rmscene import read_blocks, SceneLineItemBlock
    from rmscene.scene_items import Pen
    items = [b.item.value for b in read_blocks(io.BytesIO(raw))
             if isinstance(b, SceneLineItemBlock) and b.item.value is not None and getattr(b.item.value, "points", None)]
    strokes = [([(p.x + 702, p.y) for p in it.points], it.tool) for it in items]
    ink = [pts for pts, tool in strokes if tool not in (Pen.HIGHLIGHTER_1, Pen.HIGHLIGHTER_2, Pen.ERASER, Pen.ERASER_AREA)]
    out = []
    for pts, tool in strokes:
        if tool not in (Pen.HIGHLIGHTER_1, Pen.HIGHLIGHTER_2):
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        box = (min(xs) - 15, min(ys) - 15, max(xs) + 15, max(ys) + 15)
        under = [s for s in ink if sum(1 for x, y in s if box[0] <= x <= box[2] and box[1] <= y <= box[3]) >= 0.5 * len(s)]
        if under:
            out.append(((round(pts[0][0]), round(pts[0][1])), box, under))
    return out


def sentence_around(page_text, phrase):
    """The sentence of `page_text` that contains `phrase` (or its first words), for context; '' if not found."""
    text = " ".join(page_text.split())
    words = phrase.split()
    probe = " ".join(words[:4]) if words else phrase
    i = text.find(probe)
    if i < 0:
        return ""
    start = max(text.rfind(". ", 0, i), text.rfind("? ", 0, i), text.rfind("! ", 0, i), text.rfind("\n", 0, i))
    start = start + 2 if start >= 0 else 0
    ends = [j for j in (text.find(". ", i), text.find("? ", i), text.find("! ", i)) if j >= 0]
    end = min(ends) + 1 if ends else len(text)
    return text[start:end].strip()


DEFAULT_TEACHER = ("You are an experienced English teacher for adult Farsi speakers at an intermediate level. Explain the word "
                   "or phrase as a patient tutor would, in simple English, with usage and two examples, and give the Farsi.")


def explain_with_gemini(text=None, context=None, image_path=None, teacher=None, previous=None):
    """Simple-English meaning, a usage note, two examples and the Farsi of `text` (or of the handwriting
    in `image_path`, transcribed first), from Gemini with GEMINI_API_KEY. `teacher` is the persona and
    method (vocab/teacher.md by default: paste your own teaching instructions there). Returns a dict or {"error"}."""
    import os
    key = os.getenv("GEMINI_API_KEY") or load_config().get("gemini_api_key")
    if not key:
        return {"error": "no GEMINI_API_KEY (export it, or rm-ai setup --gemini-key)"}
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        models = ("gemini-flash-latest", "gemini-3.5-flash", "gemini-flash-lite-latest")   # the alias first; the free tier answers 503 under load

        def ask(contents, **cfg):
            last = None
            for model in models:
                for attempt in range(2):
                    try:
                        return client.models.generate_content(model=model, contents=contents, config=types.GenerateContentConfig(**cfg))
                    except Exception as e:
                        last = e
                        if "503" not in str(e) and "429" not in str(e):
                            raise
                        time.sleep(2)
            raise last

        if image_path:
            from PIL import Image
            read = ask([Image.open(image_path), "Transcribe the handwritten English in this image exactly, nothing else."]).text.strip()
            text = read
        prompt = ((teacher or DEFAULT_TEACHER).strip() + "\n\nReply as JSON with these keys: "
                  "\"phrase\" (the word or phrase, corrected if misspelt), "
                  "\"full\" (the complete lesson as plain text with line breaks, in the shape the instructions above describe), "
                  "and, for a short card written by hand on a small e-ink page: "
                  "\"meaning\" (the meaning in simple English, one or two short sentences, with the part of speech in parentheses), "
                  "\"note\" (one short sentence: the most useful collocation or usage point), "
                  "\"examples\" (two short natural example sentences), "
                  "\"farsi\" (the Farsi translation of the phrase), "
                  "\"farsi_meaning\" (one short sentence in Farsi explaining it). "
                  f"\nWord or phrase: {text!r}." + (f" It was read in this sentence: {context!r}." if context else "")
                  + (f"\nWords learned earlier, for the connection section: {', '.join(previous)}." if previous else ""))
        out = json.loads(ask(prompt, response_mime_type="application/json").text)
        if image_path:
            out["read"] = text
        return out
    except Exception as e:
        return {"error": str(e)[:160]}


PERSIAN = re.compile("[\u0600-\u06FF]")
LESSON_FONT = {"regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"}


def lesson_markdown(entry):
    """One markdown file per lookup: the full lesson with a small front matter."""
    head = [f"---", f"phrase: \"{(entry.get('phrase') or entry.get('text') or '').replace(chr(34), chr(39))}\"", f"source: \"{entry.get('doc', '')}\"",
            f"page: {entry.get('page') or ''}", f"date: {datetime.now().strftime('%Y-%m-%d %H:%M')}", "---", ""]
    body = [f"# {entry.get('phrase') or entry.get('text')}", ""]
    if entry.get("context"):
        body += [f"> {entry['context']}", ""]
    body.append((entry.get("full") or entry.get("explanation") or "").strip())
    return "\n".join(head + body) + "\n"


def render_lesson_pages(entry):
    """The lesson as printed page image(s), 1404x1872: the phrase as a title, then the lesson line by line,
    Farsi lines right-to-left and right-aligned, English lines left-to-right, headings in bold."""
    from PIL import Image, ImageDraw, ImageFont
    W, H, L, R, TOP, BOTTOM = 1404, 1872, 80, 1324, 90, 1790
    fonts = {k: ImageFont.truetype(LESSON_FONT["bold" if k in ("title", "head") else "regular"], v) for k, v in (("title", 60), ("head", 34), ("body", 30), ("meta", 22))}
    text = (entry.get("full") or entry.get("explanation") or "").replace("**", "")
    text = re.sub(r"^\s*[\U0001F300-\U0001FAFF\u2600-\u27BF\u2700-\u27BF]\s*", "", text, flags=re.M)   # emoji tofu off the headings
    heads = ("Definition & Meaning", "Structure & Grammar", "Examples & Translations", "Synonyms & Antonyms", "Connection to Previous")

    def rtl(line):
        return bool(PERSIAN.search(line)) and (len(PERSIAN.findall(line)) >= len(re.findall("[A-Za-z]", line)) / 2)

    def wrap(line, font, is_rtl):
        kw = {"direction": "rtl", "language": "fa"} if is_rtl else {}
        out, cur = [], ""
        for word in line.split():
            trial = (cur + " " + word).strip()
            if cur and font.getlength(trial, **kw) > R - L:
                out.append(cur)
                cur = word
            else:
                cur = trial
        return out + ([cur] if cur else [])

    pages, im, d, y = [], None, None, TOP

    def new_page():
        nonlocal im, d, y
        im = Image.new("L", (W, H), 255)
        d = ImageDraw.Draw(im)
        pages.append(im)
        y = TOP

    new_page()
    d.text((L, y), entry.get("phrase") or entry.get("text") or "?", font=fonts["title"], fill=0)
    y += 80
    meta = f"{entry.get('doc', '')}{'  •  p.' + str(entry['page']) if entry.get('page') else ''}  •  {datetime.now().strftime('%Y-%m-%d')}"
    d.text((L, y), meta, font=fonts["meta"], fill=110)
    y += 44
    if entry.get("context"):
        for ln in wrap(entry["context"], fonts["meta"], False):
            d.text((L, y), ln, font=fonts["meta"], fill=80)
            y += 30
    y += 20
    d.line([(L, y), (R, y)], fill=0, width=2)
    y += 26
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            y += 16
            continue
        is_head = any(h in line for h in heads)
        font = fonts["head" if is_head else "body"]
        is_rtl = rtl(line)
        if is_head:
            y += 12
        for ln in wrap(line, font, is_rtl):
            if y > BOTTOM - 40:
                new_page()
            if is_rtl:
                d.text((R, y), ln, font=font, fill=0, direction="rtl", language="fa", anchor="ra")
            else:
                d.text((L, y), ln, font=font, fill=0)
            y += 46 if is_head else 40
        if is_head:
            y += 6
    return pages


def vocab_document_pdf(lessons_dir, out_pdf):
    """All lesson page images so far, in order, as one PDF: the Vocabulary document."""
    from PIL import Image
    images = [Image.open(p) for p in sorted(Path(lessons_dir).glob("*.png"))]
    if not images:
        return 0
    images[0].save(out_pdf, "PDF", resolution=226.0, save_all=True, append_images=images[1:])
    return len(images)


def push_vocab_document(host, lessons_dir, doc_title="Vocabulary"):
    """Rebuild the Vocabulary document from every lesson page and push it into the app folder (one reload)."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf = f.name
    n = vocab_document_pdf(lessons_dir, pdf)
    if not n:
        return None
    uuid_ = cmd_push(argparse.Namespace(file=pdf, folder=CLOCK_FOLDER, title=doc_title, force_new=False, margins=0, fresh=True, rebuild=True, device=None))
    os.unlink(pdf)
    return uuid_


def vault_note(path, entry):
    """Append one lookup to the vocabulary index markdown (an Obsidian note or a plain file)."""
    path = Path(os.path.expanduser(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write("# Vocabulary\n\nWords looked up on the reMarkable, explained by Gemini.\n")
        f.write(f"\n## {entry.get('phrase') or entry.get('text')}\n")
        f.write(f"*{entry['time']}, {entry['doc']}{' p.' + str(entry['page']) if entry.get('page') else ''}*\n\n")
        if entry.get("context"):
            f.write(f"> {entry['context']}\n\n")
        if entry.get("full"):
            f.write(entry["full"].strip() + "\n")
        else:
            f.write(f"{entry.get('meaning', '')} {entry.get('note', '')}\n\n")
            for ex in entry.get("examples") or []:
                f.write(f"- {ex}\n")
            if entry.get("farsi"):
                f.write(f"\n**{entry['farsi']}** — {entry.get('farsi_meaning', '')}\n")


VOCAB_PAGE = """<!doctype html><meta charset="utf-8"><title>reMarkable vocabulary</title>
<style>body{font:16px system-ui;margin:2em auto;max-width:56em;padding:0 1em} .e{border:1px solid #ddd;border-radius:8px;padding:1em;margin:1em 0}
.e img{max-width:100%;border:1px solid #eee} .meta{color:#666;font-size:.9em} .ctx{color:#444}
.lesson div{text-align:start;line-height:1.7;font-size:1.05em;unicode-bidi:plaintext} .fa{font-size:1.1em;text-align:start;unicode-bidi:plaintext}</style>
<h1>reMarkable vocabulary <span id="n" class="meta"></span></h1><div id="list"></div>
<script>let last='';async function poll(){
  try{ const r = await fetch('events.json?'+Date.now()); const ev = await r.json();
    const sig = ev.length + ':' + (ev.length ? ev[ev.length-1].n + '/' + (ev[ev.length-1].explanation||'') : '');
    if (sig !== last) { last = sig;
      document.getElementById('n').textContent = ev.length + ' lookup' + (ev.length==1?'':'s');
      document.getElementById('list').innerHTML = ev.slice().reverse().map(e =>
        `<div class="e"><div class="meta">#${e.n} · ${e.time} · ${e.doc}${e.page ? ' p.'+e.page : ''} · ${e.kind}</div>
         ${e.text ? '<p><b>'+e.text+'</b></p>' : ''}${e.context ? '<p class="ctx">'+e.context+'</p>' : ''}
         ${e.image ? '<img src="'+e.image+'?'+Date.now()+'">' : ''}
         ${e.read ? '<p><b>Read:</b> '+e.read+'</p>' : ''}${e.explanation ? '<div class="lesson">'+e.explanation.split(/\\n/).map(l => '<div dir="auto">'+(l.trim()||'&nbsp;')+'</div>').join('')+'</div>' : ''}${e.farsi_line ? '<p dir="auto" class="fa"><b>'+e.farsi_line+'</b></p>' : ''}</div>`).join(''); }
  } catch(e) {} setTimeout(poll, 2000); } poll();</script>
"""


def cmd_vocab(args):
    """Stage 1: watch the open document; a highlighter mark on a PDF (its text comes from the page file)
    or a loop around handwriting in a notebook becomes an entry on a local web page."""
    import http.server
    import threading
    host = get_active_host(getattr(args, "device", None))
    out = Path(args.dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(VOCAB_PAGE)
    events_file = out / "events.json"
    events = json.loads(events_file.read_text()) if events_file.exists() else []
    lock = threading.Lock()
    if getattr(args, "install", False):
        if push_vocab_document(host, lessons):
            pushed["pages"] = len(list(lessons.glob("*.png")))
            pushed_file.write_text(json.dumps(pushed))
            pending["push"] = False
        else:
            print("📄 No lessons yet; the Vocabulary document is pushed with the first one")
    docs = {}       # uuid -> {"title", "pages": [page ids], "pdf": local path or None, "texts": {index: page text}}
    seen = {(e["page_id"], e["text"]) for e in events if e.get("kind") == "highlight" and "page_id" in e}   # across restarts
    seen_marks = {(e["page_id"], tuple(e["mark"])) for e in events if e.get("kind") == "ink" and "mark" in e}
    strokes = []    # real-pen strokes no loop has claimed yet

    counter = [max((e.get("n", 0) for e in events), default=0)]

    def next_n():
        with lock:
            counter[0] += 1
            return counter[0]

    explain = getattr(args, "explain", True) and bool(os.getenv("GEMINI_API_KEY") or load_config().get("gemini_api_key"))
    tablet = getattr(args, "tablet", True)
    lessons = out / "lessons"
    lessons.mkdir(exist_ok=True)
    pushed_file = out / "pushed.json"
    pushed = json.loads(pushed_file.read_text()) if pushed_file.exists() else {"pages": 0}
    pending = {"push": len(list(lessons.glob("*.png"))) != pushed["pages"]}   # lesson pages the document does not have yet

    def save_events():
        events_file.write_text(json.dumps(events, indent=1, ensure_ascii=False))

    def explain_and_write(entry):
        """In a worker thread: Gemini, the tablet's queue, the vault, the web page."""
        teacher_file = Path(getattr(args, "teacher", None) or out / "teacher.md")
        teacher = teacher_file.read_text() if teacher_file.exists() else None
        with lock:
            previous = [e["phrase"] for e in events if e.get("phrase") and e is not entry][-12:]
        r = explain_with_gemini(text=entry.get("text"), context=entry.get("context"), image_path=str(out / entry["image"]) if entry.get("image") else None,
                                teacher=teacher, previous=previous)
        with lock:
            if "error" in r:
                entry["explanation"] = "⚠️ " + r["error"]
                save_events()
                print(f"⚠️  #{entry['n']}: {r['error']}", flush=True)
                return
            entry.update({k: r[k] for k in ("phrase", "meaning", "note", "examples", "farsi", "farsi_meaning", "read", "full") if k in r})
            entry["explanation"] = r.get("full") or f"{r.get('meaning', '')}\n{r.get('note', '')}\n" + "\n".join("- " + e for e in r.get("examples") or [])
            entry["farsi_line"] = f"{r.get('farsi', '')} — {r.get('farsi_meaning', '')}"
            save_events()
            print(f"💡 #{entry['n']} {r.get('phrase')}: {r.get('meaning', '')[:80]} | {r.get('farsi', '')}", flush=True)
            stem = f"{entry['n']:04d}-" + re.sub(r"[^A-Za-z0-9]+", "-", entry.get("phrase") or "entry").strip("-")[:40]
            try:
                (lessons / f"{stem}.md").write_text(lesson_markdown(entry), encoding="utf-8")
                for i, im in enumerate(render_lesson_pages(entry)):
                    im.save(lessons / f"{stem}{'' if i == 0 else '-' + str(i + 1)}.png")
                vault = getattr(args, "vault", None)
                if vault:
                    vault_note(vault, entry)
                pending["push"] = True
                print(f"📝 #{entry['n']} lesson saved: {lessons / (stem + '.md')}", flush=True)
            except Exception as e:
                print(f"⚠️  lesson files: {str(e)[:100]}", flush=True)

    def emit(entry):
        with lock:
            entry.setdefault("n", counter[0] + 1)
            counter[0] = max(counter[0], entry["n"])
            entry["time"] = datetime.now().strftime("%H:%M:%S")
            events.append(entry)
            save_events()
        what = entry.get("text") or entry.get("image")
        print(f"📖 #{entry['n']} {entry['kind']} in '{entry['doc']}'{' p.' + str(entry['page']) if entry.get('page') else ''}: {what}", flush=True)
        if explain:
            threading.Thread(target=explain_and_write, args=(entry,), daemon=True).start()

    def doc_info(uuid_):
        if uuid_ in docs:
            return docs[uuid_]
        meta = json.loads(run_ssh(f"cat {REMOTE_PATH}/{uuid_}.metadata", host=host))
        content = json.loads(run_ssh(f"cat {REMOTE_PATH}/{uuid_}.content", host=host))
        pages = [p["id"] if isinstance(p, dict) else p for p in (content.get("cPages", {}).get("pages") or content.get("pages") or [])
                 if not (isinstance(p, dict) and p.get("deleted"))]
        info = {"title": meta.get("visibleName", uuid_[:8]), "pages": pages, "pdf": None, "texts": {}}
        if content.get("fileType") == "pdf":
            local = out / f"{uuid_}.pdf"
            if not local.exists():
                subprocess.run(["scp", "-q"] + get_ssh_base_opts() + [f"{host}:{REMOTE_PATH}/{uuid_}.pdf", str(local)], check=False)
            info["pdf"] = local if local.exists() else None
        docs[uuid_] = info
        return info

    def page_text(info, index):
        if info["pdf"] is None or index is None:
            return ""
        if index not in info["texts"]:
            try:
                import logging
                logging.getLogger("pypdf").setLevel(logging.ERROR)   # font-encoding notes are not lookups
                from pypdf import PdfReader
                info["texts"][index] = PdfReader(str(info["pdf"])).pages[index].extract_text() or ""
            except Exception:
                info["texts"][index] = ""
        return info["texts"][index]

    def on_stroke(pts):   # the pen: a loop around handwriting is a lookup (printed text: use the highlighter)
        if not is_box(pts):
            strokes.append(pts)
            del strokes[:-400]
            return
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        box = (min(xs), min(ys), max(xs), max(ys))
        loop = pts[::max(1, len(pts) // 200)]
        content = [s for s in strokes if sum(1 for q in s if inside(q, loop)) >= 0.6 * len(s)]
        uuid_ = open_document(host)
        info = doc_info(uuid_) if uuid_ else {"title": "?", "pages": [], "pdf": None, "texts": {}}
        if not content:
            # a loop with no handwriting inside: on printed text the highlighter is the tool, since the tablet
            # stores the highlighted text itself, while where a printed word sits on screen depends on the
            # tablet's own page fit ("best fit" crops the page to its content) and cannot be read off the PDF
            print(f"▢ loop with no handwriting inside on '{info['title']}' (box {[round(v) for v in box]}): for printed text use the highlighter", flush=True)
            return
        for s in content:
            strokes.remove(s)
        n = next_n()
        image = f"ink-{n}.png"
        render_strokes(content, box, out / image)
        emit({"n": n, "kind": "ink", "doc": info["title"], "image": image, "box": [round(v) for v in box], "strokes": len(content)})

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(out), **k)
        def log_message(self, *a):
            pass
    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    reader = PenReader(host, on_stroke)
    threading.Thread(target=reader.run, daemon=True).start()
    print(f"🌐 http://localhost:{args.port}  —  highlight printed text or handwriting, or draw a loop around handwriting; lessons become pages of the Vocabulary document; Ctrl+C to stop", flush=True)
    last = None   # (page file, mtime) last parsed
    primed = set()
    try:
        while True:
            uuid_ = open_document(host)
            if tablet and pending["push"] and not uuid_:   # new lesson pages, and nothing open on the tablet: rebuild the document now
                try:
                    pending["push"] = False
                    n = len(list(lessons.glob("*.png")))
                    push_vocab_document(host, lessons)
                    pushed["pages"] = n
                    pushed_file.write_text(json.dumps(pushed))
                    print(f"📄 Vocabulary document rebuilt with {n} pages (the tablet reloaded once, on its home screen)", flush=True)
                except Exception as e:
                    pending["push"] = True
                    print(f"⚠️  document push: {str(e)[:100]}", flush=True)
                    time.sleep(30)
            if uuid_:
                try:
                    newest = run_ssh(f"cd {REMOTE_PATH}/{uuid_} 2>/dev/null && ls -t *.rm 2>/dev/null | head -n 1 | xargs -r stat -c '%Y %n'", host=host).split()
                except RuntimeError:
                    newest = []
                if len(newest) == 2 and (newest[1], newest[0]) != last:
                    last = (newest[1], newest[0])
                    page_id = newest[1][:-3]
                    raw = subprocess.run(["ssh"] + get_ssh_base_opts() + [host, f"cat {REMOTE_PATH}/{uuid_}/{newest[1]}"], capture_output=True).stdout
                    try:
                        marks = read_highlights(raw)
                        inked = read_marked_ink(raw)
                    except Exception as e:
                        marks, inked = [], []
                        print(f"⚠️  could not read the page: {str(e)[:80]}", flush=True)
                    info = doc_info(uuid_)
                    index = info["pages"].index(page_id) if page_id in info["pages"] else None
                    fresh = [(page_id, st, tx) for st, tx in marks if (page_id, tx) not in seen]
                    seen.update((page_id, tx) for _, _, tx in fresh)
                    fresh_ink = [(key, box, under) for key, box, under in inked if (page_id, key) not in seen_marks]
                    seen_marks.update((page_id, key) for key, _, _ in fresh_ink)
                    if page_id not in primed and time.time() - int(newest[0]) > 600:   # a page untouched for 10 min: its marks are old
                        primed.add(page_id)
                        if fresh or fresh_ink:
                            print(f"· '{info['title']}' page {index + 1 if index is not None else '?'}: {len(fresh) + len(fresh_ink)} earlier mark(s) skipped", flush=True)
                    else:
                        primed.add(page_id)
                        for _, st, tx in fresh:
                            emit({"kind": "highlight", "doc": info["title"], "page": index + 1 if index is not None else None, "page_id": page_id, "start": st,
                                  "text": tx, "context": sentence_around(page_text(info, index), tx)})
                        for key, box, under in fresh_ink:   # a highlighter over handwriting: the handwriting under it
                            n = next_n()
                            image = f"ink-{n}.png"
                            render_strokes(under, box, out / image)
                            emit({"n": n, "kind": "ink", "doc": info["title"], "page": index + 1 if index is not None else None, "page_id": page_id,
                                  "mark": list(key), "image": image, "box": [round(v) for v in box], "strokes": len(under)})
            time.sleep(4)
    except KeyboardInterrupt:
        print("\nVocabulary watcher stopped.", flush=True)
    finally:
        if reader.proc:
            reader.proc.kill()


def cmd_chat(args):
    """Stage 1: detect looped handwriting on the chat document live and show it on a local web page;
    nothing is written back yet."""
    import http.server
    import threading
    host = get_active_host(getattr(args, "device", None))
    title = args.doc
    doc_uuid = next((nb["uuid"] for nb in list_notebooks(host) if nb["title"].lower() == title.lower() and nb["folder"] == "/"), None)
    if doc_uuid is None or getattr(args, "push", False):
        print(f"📄 Pushing the '{title}' document (the tablet reloads once)...", flush=True)
        doc_uuid = push_chat_document(host, title)
    out = Path(args.dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(CHAT_PAGE)
    boxes_file = out / "boxes.json"
    boxes = json.loads(boxes_file.read_text()) if boxes_file.exists() else []
    strokes = []   # real-pen strokes seen so far that no box has claimed yet

    def on_stroke(pts):
        if not is_box(pts):
            strokes.append(pts)
            note = ""
            if len(pts) > 150:   # a long stroke that was not taken as a loop: say why
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                w, h = max(xs) - min(xs), max(ys) - min(ys)
                gap = math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
                length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
                note = f"  [not a loop: {round(w)}x{round(h)}, start-end gap {round(gap)}px, length/perimeter {length / (2 * (w + h)):.2f}]"
            print(f"· stroke ({len(pts)} pts), {len(strokes)} unsent{note}", flush=True)
            return
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        box = (min(xs), min(ys), max(xs), max(ys))
        loop = pts[::max(1, len(pts) // 200)]   # a few hundred vertices are plenty for the containment test
        content = [s for s in strokes if sum(1 for q in s if inside(q, loop)) >= 0.6 * len(s)]
        if not content:
            print("▢ loop with nothing inside, ignored", flush=True)
            return
        for s in content:
            strokes.remove(s)
        if open_document(host) != doc_uuid:   # xochitl's LastOpen names the document on screen
            print(f"▢ loop drawn while another document is open (not '{title}'), ignored", flush=True)
            return
        n = len(boxes) + 1
        image = f"box-{n}.png"
        render_strokes(content, box, out / image)
        boxes.append({"n": n, "time": datetime.now().strftime("%H:%M:%S"), "box": box, "strokes": len(content), "image": image})
        boxes_file.write_text(json.dumps(boxes, indent=1))
        print(f"✉️  message #{n}: {len(content)} strokes in a {round(box[2]-box[0])}x{round(box[3]-box[1])} loop -> {out / image}", flush=True)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(out), **k)
        def log_message(self, *a):
            pass
    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"🌐 http://localhost:{args.port}  —  on the '{title}' document: write, then draw a loop around it; Ctrl+C to stop", flush=True)
    wait_until_open(host, doc_uuid, f"the '{title}' document")
    reader = PenReader(host, on_stroke)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            reader.run()   # returns when the connection drops (the tablet's app restart closes SSH sessions)
            print("⚠️  pen stream closed by the tablet, reconnecting in 3 s...", flush=True)
            time.sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
        server.shutdown()


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

    if getattr(args, "uninstall", False):
        uninstall_dash_app(target_host)
        return
    if getattr(args, "install", False):
        city = getattr(args, "city", None) or load_config().get("weather_city")
        token_file = getattr(args, "token_file", None)
        if token_file is None and os.path.exists(os.path.expanduser("~/.claude-tablet/.credentials.json")):
            token_file = "~/.claude-tablet/.credentials.json"
        install_dash_app(target_host, city, token_file, tasks=[(t, False) for t in args.task] if getattr(args, "task", None) else None,
                         no_push=getattr(args, "no_push", False))
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

    cfg = load_config()
    if getattr(args, "city", None):
        cfg["weather_city"] = args.city
        save_config(cfg)
    city = cfg.get("weather_city")
    weather = fetch_weather(city)
    if weather is None:
        print("💡 Pass --city once (e.g. --city Stockholm) to add the weather report; it is remembered.")
    elif "error" in weather:
        print(f"⚠️  Weather unavailable: {weather['error']}")
    else:
        print(f"🌤  {weather['name']}: {round(weather['temp'])}° {weather['text']}, today {round(weather['days'][0]['max'])}°/{round(weather['days'][0]['min'])}°")

    claude_usage = fetch_claude_usage()
    if claude_usage is None:
        print("💡 Set ANTHROPIC_ADMIN_KEY (an Admin API key) to show Claude API spend and tokens on the dashboard.")
    elif "error" in claude_usage:
        print(f"⚠️  Claude API usage unavailable: {claude_usage['error']}")
    else:
        print(f"🤖 Claude API: ${claude_usage['month_usd']:.2f} this month, ${claude_usage['today_usd']:.2f} today")

    subscription = fetch_claude_subscription_usage()
    if subscription is None:
        print("💡 No Claude Code login found (~/.claude/.credentials.json); the usage box shows the daily focus instead.")
    elif "error" in subscription:
        print(f"⚠️  Claude usage unavailable: {subscription['error']}")
    else:
        print("🤖 Claude usage: " + "  •  ".join(f"{l} {float(p):.0f}%" for l, p, _ in subscription["windows"]))
        print("   usage windows reported by the endpoint: " + ", ".join(subscription.get("all_keys", [])))

    # Render image
    im = render_dashboard_image(battery_info=battery_info, tasks=tasks, quote=quote, time_format=time_format,
                                claude_usage=claude_usage, subscription=subscription, weather=weather, notes=(mode == "doc"), clock=(mode == "doc"))

    save_path = getattr(args, "save", None)

    if mode == "standby":
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_img:
            temp_png = tmp_img.name
        im.save(temp_png, "PNG")

        if save_path:
            im.save(save_path, "PNG")
            print(f"Saved local preview image to {save_path}")

        dash_app = load_config().get("dash_app")
        if dash_app and dash_app.get("sleep"):
            print("🖼️  The tablet paints its own sleep screen now (rm-ai dashboard --install); only its stock is topped up.")
        else:
            print("Uploading dashboard to tablet standby screen (/usr/share/remarkable/suspended.png)...")
        if dash_app:   # the tablet dashboard runs by itself; this only tops up its stock of printed pages when the PC is around
            try:
                top_up_dash_pages(target_host, (load_config().get("weather_location") or {}).get("name", ""),
                                  [(t, False) for t in dash_app.get("tasks") or []] or None)
            except Exception as e:
                print(f"⚠️  tablet dashboard pages not topped up: {str(e)[:80]}")
            if dash_app.get("feed") and subscription and "windows" in subscription:
                feed_dash_usage(target_host, subscription)   # the tablet dashboard without a login of its own
            if dash_app.get("sleep"):
                os.unlink(temp_png)
                return
        try:
            # Ensure backup of original
            run_ssh("test -f /usr/share/remarkable/suspended.png.original || cp /usr/share/remarkable/suspended.png /usr/share/remarkable/suspended.png.original", host=target_host)
            # Upload
            subprocess.run(["scp"] + get_ssh_base_opts() + [temp_png, f"{target_host}:/usr/share/remarkable/suspended.png"], check=True)
        except (RuntimeError, subprocess.CalledProcessError) as e:
            # a sleeping tablet has its Wi-Fi off; a scheduled run simply tries again next time
            print(f"⚠️  Tablet unreachable (asleep or off Wi-Fi), nothing uploaded: {str(e)[:80]}")
            return
        finally:
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
        live = getattr(args, "live", False)
        if live:   # re-render as the template: blank clock area, printed usage labels and bar outlines
            im = render_dashboard_image(battery_info=battery_info, tasks=tasks, quote=quote, time_format=time_format, live=True, weather=weather)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
            temp_pdf = tmp_pdf.name
        im.save(temp_pdf, "PDF", resolution=226.0)

        if save_path:
            im.save(save_path, "PNG")
            print(f"Saved local preview image to {save_path}")

        folder = getattr(args, "folder", None)
        title = getattr(args, "title", "Daily Dashboard")
        print(f"Pushing dashboard as a notebook document '{title}' to reMarkable...")
        def push_template():
            """Render today's template and push it as the dashboard document; returns its uuid."""
            page = render_dashboard_image(battery_info=get_tablet_battery_info(host=target_host), tasks=tasks,
                                          quote=quote, time_format=time_format, live=True, weather=fetch_weather(city)) if live else im
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                pdf = f.name
            page.save(pdf, "PDF", resolution=226.0)
            uuid_ = cmd_push(argparse.Namespace(file=pdf, folder=folder, title=title, force_new=False, margins=0,
                                                fresh=True, device=getattr(args, "device", None)))
            try:
                os.unlink(pdf)
            except Exception:
                pass
            return uuid_

        if live and getattr(args, "no_push", False):
            print("Using the dashboard page already on the tablet (no push).", flush=True)
            doc_uuid = next((nb["uuid"] for nb in list_notebooks(target_host) if nb["title"].lower() == title.lower() and nb["folder"] == "/"), None)
            if not doc_uuid:
                print(f"❌ No document '{title}' on the tablet; run without --no-push first.")
                return
            wait_until_open(target_host, doc_uuid, "the dashboard")
            run_live_dashboard(target_host, getattr(args, "usage_interval", 5), doc_uuid, repush=push_template)
            return
        try:
            os.unlink(temp_pdf)
        except Exception:
            pass
        doc_uuid = push_template()
        print(f"Daily Dashboard notebook created! Open it on your tablet to write notes with your stylus.")
        if live and doc_uuid:
            print(f"📄 Open '{title}' on the tablet.", flush=True)
            wait_until_open(target_host, doc_uuid, "the dashboard")
            run_live_dashboard(target_host, getattr(args, "usage_interval", 5), doc_uuid, repush=push_template)


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
    p_clock.add_argument("--pos", "-p", type=str, default=None, help="Screen position: center (default), top-right, top-left, bottom-right (presets stay clear of the toolbar and menus), or a raw X,Y")
    p_clock.add_argument("--size", "-s", type=str, default=None, choices=["small", "medium", "large", "xlarge"], help="Clock size preset")
    p_clock.add_argument("--format", choices=["HH:MM:SS", "HH:MM", "MM:SS"], default="HH:MM:SS", help="Clock time format")
    p_clock.add_argument("--thickness", "-t", type=lambda v: v if v == "max" else int(v), default=None, help="Bar thickness in px (default 28) or 'max' for the thickest the size allows; thicker than one pen line is built from overlapping passes (max per size: small 12, medium 25, large 33, xlarge 45)")
    p_clock.add_argument("--pressure", type=int, default=None, help="Simulated pen pressure 0..4095 (default 4000); widens and darkens pressure-sensitive pens such as the pencil and ballpoint")
    p_clock.add_argument("--pen-width", type=int, default=None, help="Width in px of one line of the selected pen at that pressure (default 12); 0 measures it with a test line at start")
    p_clock.add_argument("--ink-width", type=int, default=None, help="Full width in px that one line really covers, halo included, so erasing removes all of it (default 70, the pencil at pressure 4000)")
    p_clock.add_argument("--frame", action=argparse.BooleanOptionalAction, default=None, help="Draw a rounded frame around the clock (default on; --no-frame turns it off)")
    p_clock.add_argument("--save-defaults", action="store_true", help="Store the given --pos/--size/--thickness/--pressure/--pen-width/--ink-width/--frame/--interval as the defaults for future runs, then exit")
    p_clock.add_argument("--duration", type=int, default=None, help="Duration in seconds to run (default: infinite)")
    p_clock.add_argument("--interval", "-i", type=float, default=None, help="Seconds between updates (default 2); with 5 the clock shows the real time at :00, :05, :10 ... so each reading stays visible longer")
    p_clock.add_argument("--slow", type=float, default=None, help="Slow-motion test delay in seconds: wait N seconds per 1-second increment (e.g. --slow 5)")
    p_clock.add_argument("--step", type=int, default=1, help="Number of seconds to advance per update (default: 1)")
    p_clock.add_argument("--once", "-1", action="store_true", help="Draw current time once and exit immediately")
    p_clock.add_argument("--clear", action="store_true", help="Erase the clock from screen on exit")
    p_clock.add_argument("--install", action="store_true", help="Install the clock on the tablet itself: it then runs whenever the --doc document is open, no PC needed (see app/)")
    p_clock.add_argument("--uninstall", action="store_true", help="Remove the tablet-resident clock again")
    p_clock.add_argument("--doc", default="Clock", help="Title of the document the tablet-resident clock runs in (default Clock; pushed blank if missing)")
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

    # chat
    p_chat = subparsers.add_parser("chat", help="Chat on the page: box your handwriting to send it (stage 1: detect and show on a local web page)")
    p_chat.add_argument("--dir", type=str, default="chat", help="Folder for the rendered messages and the web page (default ./chat)")
    p_chat.add_argument("--port", type=int, default=8765, help="Local web page port (default 8765)")
    p_chat.add_argument("--doc", type=str, default="Chat", help="Title of the chat document on the tablet (pushed as blank pages if missing; default Chat)")
    p_chat.add_argument("--push", action="store_true", help="Push the chat document again (fresh blank pages), reloading the tablet")
    p_chat.set_defaults(func=cmd_chat)

    # vocab
    p_vocab = subparsers.add_parser("vocab", help="English learning: what you highlight on a PDF or loop in a notebook, captured (stage 1: local web page)")
    p_vocab.add_argument("--dir", type=str, default="vocab", help="Folder for the captured lookups and the web page (default ./vocab)")
    p_vocab.add_argument("--port", type=int, default=8765, help="Local web page port (default 8765)")
    p_vocab.add_argument("--vault", type=str, default=None, help="Also append every lesson to this markdown file (an Obsidian note); each lesson is always its own file under ./vocab/lessons")
    p_vocab.add_argument("--no-explain", dest="explain", action="store_false", help="Only capture; no Gemini explanation (needs GEMINI_API_KEY)")
    p_vocab.add_argument("--no-tablet", dest="tablet", action="store_false", help="Don't rebuild the Vocabulary document on the tablet (one printed page per lesson)")
    p_vocab.add_argument("--install", action="store_true", help="Push the Vocabulary document (all lessons so far) into the app folder now")
    p_vocab.add_argument("--teacher", type=str, default=None, help="A text file with the teaching persona and method for the explanations (default ./vocab/teacher.md)")
    p_vocab.set_defaults(func=cmd_vocab)

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
    p_dash.add_argument("--city", type=str, default=None, help="City for the weather report (Open-Meteo, no key); remembered in the config")
    p_dash.add_argument("--live", action="store_true", help="With --mode doc: push the page once as a template, then keep drawing HH:MM and the Claude usage bars on it with the pen (no reloads)")
    p_dash.add_argument("--usage-interval", type=float, default=5, help="Minutes between usage refreshes in --live mode (default 5)")
    p_dash.add_argument("--no-push", action="store_true", help="With --live or --install: don't push the page again (no reload), use the dashboard page already on the tablet")
    p_dash.add_argument("--install", action="store_true", help="Install the dashboard on the tablet itself: it draws time, date, calendar, weather and Claude usage whenever its Dashboard page is open, no PC needed (see app/)")
    p_dash.add_argument("--uninstall", action="store_true", help="Remove the tablet-resident dashboard again")
    p_dash.add_argument("--token-file", type=str, default=None, help="With --install: a Claude Code credential file of a login made for the tablet (default ~/.claude-tablet/.credentials.json if it exists), so the tablet fetches its usage itself")
    p_dash.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
