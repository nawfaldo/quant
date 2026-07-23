#!/usr/bin/env python3
"""Cross-platform script to run Yahoo Finance daily fetch & import."""

import sys
import subprocess
import os

def wait_for_keypress(message="Press any key to exit..."):
    print(message)
    try:
        # Windows
        import msvcrt
        msvcrt.getch()
    except ImportError:
        # macOS / Linux
        import tty
        import termios
        fd = sys.stdin.fileno()
        # Only try if we are in a TTY (interactive terminal)
        if os.isatty(fd):
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        else:
            # Fallback to standard input if not in a TTY
            sys.stdin.read(1)

def close_terminal_window():
    if sys.platform == "darwin" and os.getenv("TERM_PROGRAM") == "Apple_Terminal":
        # AppleScript to quit Terminal if it's the only window, otherwise close just the front window
        applescript = """
        tell application "Terminal"
            if (count of windows) is 1 then
                quit
            else
                close front window
            end if
        end tell
        """
        # Run the AppleScript in the background
        import subprocess
        subprocess.Popen(["osascript", "-e", applescript])

def main():
    # Change directory to the folder containing this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # Determine Python interpreter to use
    python_exe = sys.executable if sys.executable else "python"

    print("==================================================")
    print("Starting Yahoo Finance daily fetch & import...")
    print("==================================================")
    print()

    # Step 1: NQ (Intraday)
    print("--- 1/3 Fetching & Importing NQ (Intraday) ---")
    cmd_nq = [python_exe, 'yfinance_fetch&import.py', '--symbol', 'NQ=F', '--table-prefix', 'nq', '--timezone', 'America/New_York']
    res = subprocess.run(cmd_nq)
    if res.returncode != 0:
        print("Error: Fetching NQ failed.")
        print()
        wait_for_keypress()
        close_terminal_window()
        sys.exit(1)
    print()

    # Step 2: ES (Intraday)
    print("--- 2/3 Fetching & Importing ES (Intraday) ---")
    cmd_es = [python_exe, 'yfinance_fetch&import.py', '--symbol', 'ES=F', '--table-prefix', 'es', '--timezone', 'America/New_York']
    res = subprocess.run(cmd_es)
    if res.returncode != 0:
        print("Error: Fetching ES failed.")
        print()
        wait_for_keypress()
        close_terminal_window()
        sys.exit(1)
    print()

    # Step 3: VIX (Daily)
    print("--- 3/3 Fetching & Importing VIX (Daily) ---")
    cmd_vix = [python_exe, 'yfinance_fetch&import.py', '--symbol', '^VIX', '--table-prefix', 'vix', '--timezone', 'America/New_York']
    res = subprocess.run(cmd_vix)
    if res.returncode != 0:
        print("Error: Fetching VIX failed.")
        print()
        wait_for_keypress()
        close_terminal_window()
        sys.exit(1)
    print()

    print("==================================================")
    print("Daily data fetch & import completed successfully!")
    print("==================================================")
    print()
    wait_for_keypress()
    close_terminal_window()

if __name__ == '__main__':
    main()
