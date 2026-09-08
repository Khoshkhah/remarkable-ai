#!/usr/bin/env python3
"""
One-Step Installer for remarkable-ai
Installs Python dependencies, CLI binary, and configures AI agents (Antigravity, Gemini, Claude).
"""
import sys
import os
import subprocess
import shutil
from pathlib import Path

def main():
    print("=" * 60)
    print("reMarkable AI - One-Step Automated Installer")
    print("=" * 60 + "\n")

    # 1. Check Python version
    if sys.version_info < (3, 10):
        print("Error: Python 3.10 or higher is required.")
        sys.exit(1)

    repo_dir = Path(__file__).resolve().parent

    # 2. Install package
    print("📦 Step 1: Installing Python package and dependencies...")
    install_cmds = [
        [sys.executable, "-m", "pip", "install", "-e", str(repo_dir)],
        [sys.executable, "-m", "pip", "install", str(repo_dir)],
        [sys.executable, "-m", "pip", "install", "--break-system-packages", "-e", str(repo_dir)],
        [sys.executable, "-m", "pip", "install", "--break-system-packages", str(repo_dir)],
    ]
    installed = False
    for cmd in install_cmds:
        try:
            res = subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            installed = True
            print("   Package and dependencies installed successfully!\n")
            break
        except subprocess.CalledProcessError:
            continue

    if not installed:
        # If all silent attempts failed, run once with full output so the user sees the pip error
        print("   Attempting verbose pip install...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", str(repo_dir)], check=True)
            print("   Package installed successfully!\n")
        except subprocess.CalledProcessError as e:
            print(f"   Installation error: {e}")
            print("   Please check your pip/python environment.")
            sys.exit(1)

    # 3. Configure AI Agents (Antigravity, Gemini, Claude Code)
    print("🤖 Step 2: Configuring AI Agents...")
    skill_src = repo_dir / ".agents" / "skills" / "remarkable-ai" / "SKILL.md"
    if not skill_src.exists():
        skill_src = repo_dir / "skills" / "remarkable-ai" / "SKILL.md"
    claude_cmd_dir = repo_dir / ".claude" / "commands"

    # Antigravity targets
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
                print(f"   [Antigravity/Gemini] Installed skill to: {target_dir / 'SKILL.md'}")
        except Exception as e:
            print(f"   [Antigravity/Gemini] Notice: {e}")

    # Claude Code targets
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
                print(f"   [Claude Code] Installed slash commands (/rm-list, /rm-read, /rm-tasks, /rm-devices, /rm-export, /rm-push) to: {c_target}")
            except Exception as e:
                print(f"   [Claude Code] Notice: {e}")

    # 4. Check tablet connection
    print("\n📡 Step 3: Checking reMarkable tablet connectivity...")
    try:
        from rm_ai import load_config, get_ssh_base_opts, setup_wizard
        cfg = load_config()
        active = cfg.get("active_device")
        dev = cfg.get("devices", {}).get(active, {}) if active else {}
        ip = dev.get("ip")
        host = f"root@{ip}" if ip else dev.get("host")

        connected = False
        if host:
            test_cmd = ["ssh", "-o", "BatchMode=yes"] + get_ssh_base_opts() + [host, "echo ok"]
            res = subprocess.run(test_cmd, capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip() == "ok":
                connected = True
                print(f"   Connected to reMarkable tablet [{active}] ({host}) wirelessly!")

        if not connected:
            print("   No active reMarkable tablet connection verified.")
            try:
                ans = input("   Would you like to connect your tablet now? [Y/n]: ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                ans = "n"
            if ans in ("", "y", "yes"):
                setup_wizard()
            else:
                print("   You can connect your tablet at any time by running: rm-ai setup")
    except Exception as e:
        print(f"   Notice: {e}")

    print("\n" + "=" * 60)
    print("Setup completed successfully!")
    print("=" * 60)
    print("\nHow to use:")
    print("  • Terminal CLI:       rm-ai list, rm-ai read, rm-ai devices")
    print("  • Antigravity/Gemini: Ask naturally: 'Read page 5 of D-Wave' or 'List notebooks'")
    print("  • Claude Code:        Use slash commands: /rm-list, /rm-read, /rm-tasks, /rm-devices, /rm-export, /rm-push\n")

if __name__ == "__main__":
    main()
