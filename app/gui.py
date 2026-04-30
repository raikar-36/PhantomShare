"""
PhantomShare — CustomTkinter GUI.

Single-window application with Send / Receive modes,
progress bar, status log, and verification popup.

v3: VPS-only relay, simplified architecture, improved UX.
"""

from __future__ import annotations

import datetime
import logging
import random
import secrets
import socket
import ssl
import string
import sys
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional
from urllib.parse import urlparse

import customtkinter as ctk

import webbrowser

# Drag-and-drop support (optional)
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except ImportError:
    _HAS_DND = False

from .config import (
    APP_NAME,
    APP_VERSION,
    GITHUB_URL,
    HOMEPAGE_URL,
    SESSION_CODE_LENGTH,
    VPS_MAX_FILE_SIZE,
    VPS_RELAY_URL,
)
from .ws_relay import VPSRelaySender, VPSRelayReceiver
from .telemetry import report_crash, report_session

log = logging.getLogger(__name__)

# ── Appearance ─────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ── Startup tips (shown randomly on launch) ───────────────────────
def _startup_tips() -> list[str]:
    return [
        "🔒  Your files are protected by AES-256-GCM encryption. The server never sees your data.",
        "🔄  If the connection drops, the transfer will automatically resume from where it stopped.",
        f"⭐  Like PhantomShare? Star us on GitHub: {GITHUB_URL}",
        "🚀  Tip: for multiple files, pack them into a single archive (ZIP/RAR).",
        "🛡️  Always check the verification code — it protects against man-in-the-middle (MITM) attacks.",
    ]


def _generate_code() -> str:
    chars = string.ascii_lowercase + string.digits
    code = "".join(secrets.choice(chars) for _ in range(SESSION_CODE_LENGTH))
    # Format as xxxx-xxxxxx for 10 chars (easier to read/share)
    return f"{code[:5]}-{code[5:]}"


def _human_size(b: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def _human_speed(bps: float) -> str:
    return f"{_human_size(bps)}/s"


def _human_eta(seconds: float) -> str:
    if seconds < 0 or seconds > 360000:
        return "—"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _timestamp() -> str:
    """Current time as [HH:MM:SS] prefix for log lines."""
    return datetime.datetime.now().strftime("[%H:%M:%S]")


# ════════════════════════════════════════════════════════════════════
#  Main application window
# ════════════════════════════════════════════════════════════════════

class App(ctk.CTk):
    WIDTH = 580
    HEIGHT = 700

    # Connection states
    STATE_IDLE = "idle"
    STATE_CONNECTING = "connecting"
    STATE_WAITING = "waiting"
    STATE_KEY_EXCHANGE = "key_exchange"
    STATE_VERIFYING = "verifying"
    STATE_TRANSFERRING = "transferring"
    STATE_DONE = "done"
    STATE_ERROR = "error"

    @staticmethod
    def _get_state_labels():
        return {
            App.STATE_IDLE:         ("⚪  Ready", "gray"),
            App.STATE_CONNECTING:   ("🟡  Connecting...", "#f39c12"),
            App.STATE_WAITING:      ("🟡  Waiting for peer...", "#f39c12"),
            App.STATE_KEY_EXCHANGE: ("🟡  Key exchange...", "#f39c12"),
            App.STATE_VERIFYING:    ("🟠  Verifying...", "#e67e22"),
            App.STATE_TRANSFERRING: ("🟢  Transferring...", "#2ecc71"),
            App.STATE_DONE:         ("🟢  Complete ✓", "#27ae60"),
            App.STATE_ERROR:        ("🔴  Error", "#e74c3c"),
        }

    def __init__(self):
        super().__init__()

        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.minsize(500, 660)
        self.resizable(True, True)

        # ── Window icon ─────────────────────────────────────────────
        self._set_window_icon()

        # State
        self._worker_thread: Optional[threading.Thread] = None
        self._cancel_flag = False
        self._current_transfer = None   # VPSRelaySender / VPSRelayReceiver

        self._build_ui()

        # ── Show a random startup tip ────────────────────────
        self.after(500, self._show_startup_tip)

    # ── Window icon ──────────────────────────────────────────────

    def _set_window_icon(self):
        """Set the window/taskbar icon from bundled assets."""
        try:
            # PyInstaller bundled path
            if getattr(sys, 'frozen', False):
                base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
            else:
                base = Path(__file__).resolve().parent.parent

            ico_path = base / "assets" / "PhantomShare.ico"
            png_path = base / "assets" / "icon_32.png"

            if ico_path.exists():
                self.iconbitmap(str(ico_path))
            if png_path.exists():
                from tkinter import PhotoImage
                self._icon_photo = PhotoImage(file=str(png_path))
                self.iconphoto(True, self._icon_photo)
        except Exception as exc:
            log.debug("Could not set window icon: %s", exc)

    # ── UI construction ────────────────────────────────────────────

    def _build_ui(self):
        # ── Title bar ─────────────────────────────────────────────
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(fill="x", padx=20, pady=(14, 0))

        ctk.CTkLabel(
            title_frame,
            text=f"🔒 {APP_NAME}",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")

        ctk.CTkLabel(
            title_frame,
            text=f"v{APP_VERSION}",
            font=ctk.CTkFont(size=10),
            text_color="#777777",
        ).pack(side="left", padx=(6, 0), pady=(5, 0))

        # ── Toolbar ───────────────────────────────────────────────
        toolbar = ctk.CTkFrame(self, fg_color="#1e1e1e", corner_radius=8, height=36)
        toolbar.pack(fill="x", padx=20, pady=(8, 0))
        toolbar.pack_propagate(False)

        _tb_font = ctk.CTkFont(size=12)
        _tb_kw = dict(
            height=28,
            font=_tb_font,
            fg_color="transparent",
            hover_color="#333333",
            border_width=0,
            corner_radius=6,
        )

        self._tb_diag_btn = ctk.CTkButton(
            toolbar, text="🔍 Diagnostics", width=116,
            command=self._run_diagnostics, **_tb_kw,
        )
        self._tb_diag_btn.pack(side="left", padx=(6, 2), pady=4)

        self._tb_help_btn = ctk.CTkButton(
            toolbar, text="❓ Help", width=100,
            command=self._show_help, **_tb_kw,
        )
        self._tb_help_btn.pack(side="left", padx=2, pady=4)

        # Theme toggle button (right side)
        self._current_theme = "dark"
        self._tb_theme_btn = ctk.CTkButton(
            toolbar, text="🌙", width=36,
            command=self._toggle_theme, **_tb_kw,
        )
        self._tb_theme_btn.pack(side="right", padx=(2, 6), pady=4)

        # Tab view
        self.tabs = ctk.CTkTabview(self, width=self.WIDTH - 40)
        self.tabs.pack(fill="both", expand=True, padx=20, pady=(6, 0))

        self._tab_send_name = "📤  Send"
        self._tab_recv_name = "📥  Receive"
        self._build_send_tab(self.tabs.add(self._tab_send_name))
        self._build_recv_tab(self.tabs.add(self._tab_recv_name))

        # ── Status / progress area (shared) ───────────────────────
        status_frame = ctk.CTkFrame(self)
        status_frame.pack(fill="x", padx=20, pady=(4, 6))

        # Connection status indicator
        self.status_indicator = ctk.CTkLabel(
            status_frame,
            text="⚪  Ready",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="gray",
        )
        self.status_indicator.pack(padx=12, pady=(6, 0))

        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(status_frame, height=16)
        self.progress_bar.pack(fill="x", padx=12, pady=(4, 2))
        self.progress_bar.set(0)

        self.progress_label = ctk.CTkLabel(
            status_frame,
            text="",
            font=ctk.CTkFont(size=12),
        )
        self.progress_label.pack(padx=12, pady=(0, 2))

        # Status log textbox
        self.status_box = ctk.CTkTextbox(
            status_frame,
            height=110,
            font=ctk.CTkFont(family="Consolas", size=11),
            state="disabled",
            wrap="word",
        )
        self.status_box.pack(fill="x", padx=12, pady=(2, 4))

        # Bottom buttons row: log actions + cancel
        btn_row = ctk.CTkFrame(status_frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 8))

        self._copy_log_btn = ctk.CTkButton(
            btn_row,
            text="📋 Copy log",
            width=130,
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color="#3a3a3a",
            hover_color="#4a4a4a",
            border_width=1,
            border_color="#555555",
            command=self._copy_log,
        )
        self._copy_log_btn.pack(side="left", padx=(0, 6))

        self._save_log_btn = ctk.CTkButton(
            btn_row,
            text="💾 Save log",
            width=130,
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color="#3a3a3a",
            hover_color="#4a4a4a",
            border_width=1,
            border_color="#555555",
            command=self._save_log,
        )
        self._save_log_btn.pack(side="left")

        # Cancel button — right-aligned in the same row
        self.cancel_btn = ctk.CTkButton(
            btn_row,
            text="⏹ Cancel",
            width=130,
            height=28,
            fg_color="#c0392b",
            hover_color="#e74c3c",
            command=self._on_cancel,
            state="disabled",
        )
        self.cancel_btn.pack(side="right")

    # ── Send tab ───────────────────────────────────────────────────

    def _build_send_tab(self, tab):
        self._send_choose_lbl = ctk.CTkLabel(
            tab,
            text="Choose a file to send:",
            font=ctk.CTkFont(size=13),
        )
        self._send_choose_lbl.pack(anchor="w", padx=10, pady=(6, 2))

        # Drop zone frame for drag-and-drop
        drop_frame = ctk.CTkFrame(tab, fg_color="#2a2a3a", corner_radius=8)
        drop_frame.pack(fill="x", padx=10, pady=2)
        
        self._drop_label = ctk.CTkLabel(
            drop_frame,
            text="📂 Drop file here or click Browse",
            font=ctk.CTkFont(size=12),
            text_color="gray",
            height=60,
        )
        self._drop_label.pack(fill="x", padx=10, pady=5)
        
        # Register drag-and-drop if available
        if _HAS_DND:
            self._setup_drag_drop(drop_frame)

        file_frame = ctk.CTkFrame(tab, fg_color="transparent")
        file_frame.pack(fill="x", padx=10, pady=2)

        self.file_entry = ctk.CTkEntry(
            file_frame,
            placeholder_text="File path...",
            state="readonly",
        )
        self.file_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self._send_browse_btn = ctk.CTkButton(
            file_frame,
            text="📁 File",
            width=70,
            command=self._browse_file,
        )
        self._send_browse_btn.pack(side="right", padx=(0, 2))
        
        self._send_browse_multi_btn = ctk.CTkButton(
            file_frame,
            text="📚 Multi",
            width=70,
            command=self._browse_files_multi,
            fg_color="#2980b9",
        )
        self._send_browse_multi_btn.pack(side="right", padx=(0, 2))
        
        self._send_browse_folder_btn = ctk.CTkButton(
            file_frame,
            text="📂 Folder",
            width=70,
            command=self._browse_folder,
            fg_color="#27ae60",
        )
        self._send_browse_folder_btn.pack(side="right", padx=(0, 2))

        # File info label (size + warning)
        self.file_info_label = ctk.CTkLabel(
            tab,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        )
        self.file_info_label.pack(anchor="w", padx=14, pady=(2, 0))

        # 5 GB warning (hidden by default)
        self.size_warning_label = ctk.CTkLabel(
            tab,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#e74c3c",
        )
        self.size_warning_label.pack(anchor="w", padx=14, pady=(0, 0))

        # Session code display
        code_frame = ctk.CTkFrame(tab)
        code_frame.pack(fill="x", padx=10, pady=(6, 2))

        self._send_session_lbl = ctk.CTkLabel(
            code_frame,
            text="Session code:",
            font=ctk.CTkFont(size=13),
        )
        self._send_session_lbl.pack(anchor="w", padx=10, pady=(8, 0))

        code_inner = ctk.CTkFrame(code_frame, fg_color="transparent")
        code_inner.pack(fill="x", padx=10, pady=(4, 4))

        self.send_code_label = ctk.CTkLabel(
            code_inner,
            text="— — — —",
            font=ctk.CTkFont(family="Consolas", size=24, weight="bold"),
            text_color="#3498db",
        )
        self.send_code_label.pack(side="left", padx=(0, 10))

        self.copy_code_btn = ctk.CTkButton(
            code_inner,
            text="📋",
            width=36,
            height=36,
            font=ctk.CTkFont(size=16),
            fg_color="#555555",
            hover_color="#666666",
            command=self._copy_session_code,
            state="disabled",
        )
        self.copy_code_btn.pack(side="left", padx=(0, 6))
        
        self.qr_code_btn = ctk.CTkButton(
            code_inner,
            text="📱",
            width=36,
            height=36,
            font=ctk.CTkFont(size=16),
            fg_color="#555555",
            hover_color="#666666",
            command=self._show_qr_code,
            state="disabled",
        )
        self.qr_code_btn.pack(side="left")

        self._send_hint_lbl = ctk.CTkLabel(
            code_frame,
            text="Share this code with the recipient",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        self._send_hint_lbl.pack(padx=10, pady=(0, 8))

        self.send_btn = ctk.CTkButton(
            tab,
            text="🚀 Send",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=38,
            command=self._on_send,
        )
        self.send_btn.pack(fill="x", padx=10, pady=(8, 6))

    # ── Receive tab ────────────────────────────────────────────────

    def _build_recv_tab(self, tab):
        self._recv_enter_lbl = ctk.CTkLabel(
            tab,
            text="Enter the session code from the sender:",
            font=ctk.CTkFont(size=13),
        )
        self._recv_enter_lbl.pack(anchor="w", padx=10, pady=(6, 2))

        self.recv_code_entry = ctk.CTkEntry(
            tab,
            placeholder_text="xxxx-xxxx",
            font=ctk.CTkFont(family="Consolas", size=20),
            height=40,
            justify="center",
        )
        self.recv_code_entry.pack(fill="x", padx=10, pady=2)

        # Paste button next to entry for convenience
        paste_frame = ctk.CTkFrame(tab, fg_color="transparent")
        paste_frame.pack(fill="x", padx=10, pady=(2, 0))
        self._recv_paste_btn = ctk.CTkButton(
            paste_frame,
            text="📋 Paste code",
            width=120,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#3a3a3a",
            hover_color="#4a4a4a",
            border_width=1,
            border_color="#555555",
            command=self._paste_session_code,
        )
        self._recv_paste_btn.pack(side="left")

        # Save directory
        self._recv_save_lbl = ctk.CTkLabel(
            tab,
            text="Save to:",
            font=ctk.CTkFont(size=13),
        )
        self._recv_save_lbl.pack(anchor="w", padx=10, pady=(8, 2))

        dir_frame = ctk.CTkFrame(tab, fg_color="transparent")
        dir_frame.pack(fill="x", padx=10, pady=2)

        self.save_dir_entry = ctk.CTkEntry(dir_frame, state="readonly")
        self.save_dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        # Default save dir = Downloads
        downloads = Path.home() / "Downloads"
        if not downloads.exists():
            downloads = Path.home()
        self._save_dir = str(downloads)
        self.save_dir_entry.configure(state="normal")
        self.save_dir_entry.insert(0, self._save_dir)
        self.save_dir_entry.configure(state="readonly")

        self._recv_browse_btn = ctk.CTkButton(
            dir_frame,
            text="📁 Browse",
            width=100,
            command=self._browse_save_dir,
        )
        self._recv_browse_btn.pack(side="right")

        self.recv_btn = ctk.CTkButton(
            tab,
            text="📥 Receive",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=38,
            command=self._on_receive,
        )
        self.recv_btn.pack(fill="x", padx=10, pady=(10, 6))

    # ── UI helpers ─────────────────────────────────────────────────

    def _browse_file(self):
        path = filedialog.askopenfilename(title="Choose file")
        if path:
            self._set_file_path(path)
    
    def _browse_files_multi(self):
        """Browse for multiple files and create a bundle."""
        paths = filedialog.askopenfilenames(title="Choose files to bundle")
        if paths and len(paths) > 0:
            self._create_and_set_bundle(list(paths))
    
    def _browse_folder(self):
        """Browse for a folder and create a bundle."""
        path = filedialog.askdirectory(title="Choose folder to bundle")
        if path:
            self._create_and_set_bundle([path])
    
    def _create_and_set_bundle(self, paths: list):
        """Create a bundle from paths and set it as the file to send."""
        from .bundler import create_bundle
        import tempfile
        
        try:
            self._log_status("📦 Creating bundle...")
            bundle_path = create_bundle(
                [Path(p) for p in paths],
                output_dir=Path(tempfile.gettempdir()),
            )
            self._set_file_path(str(bundle_path))
            self._log_status(f"📦 Bundle created: {len(paths)} items")
        except Exception as e:
            self._log_status(f"❌ Bundle error: {e}")

    def _set_file_path(self, path: str):
        """Set the file path in the entry (from browse or drag-drop)."""
        # Clean up path (remove curly braces from Windows drag-drop)
        path = path.strip('{}').strip('"').strip("'")
        if not Path(path).is_file():
            return
        
        self.file_entry.configure(state="normal")
        self.file_entry.delete(0, "end")
        self.file_entry.insert(0, path)
        self.file_entry.configure(state="readonly")
        self._update_file_info(path)
        
        # Update drop zone label
        self._drop_label.configure(
            text=f"✅ {Path(path).name}",
            text_color="#2ecc71",
        )
    
    def _setup_drag_drop(self, widget):
        """Setup drag-and-drop for the widget."""
        try:
            # Get the underlying Tk widget
            tk_widget = widget.winfo_toplevel()
            
            # Register drop target
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', self._on_file_drop)
            widget.dnd_bind('<<DragEnter>>', self._on_drag_enter)
            widget.dnd_bind('<<DragLeave>>', self._on_drag_leave)
        except Exception as e:
            log.debug(f"Could not setup drag-drop: {e}")
    
    def _on_file_drop(self, event):
        """Handle file drop event - supports multiple files."""
        data = event.data
        # Parse dropped paths (can be space-separated with {} around paths with spaces)
        paths = []
        if '{' in data:
            # Windows format with braces
            import re
            paths = re.findall(r'\{([^}]+)\}', data)
            # Also get non-braced items
            remaining = re.sub(r'\{[^}]+\}', '', data).strip()
            if remaining:
                paths.extend(remaining.split())
        else:
            paths = data.split('\n') if '\n' in data else data.split()
        
        paths = [p.strip() for p in paths if p.strip()]
        
        if len(paths) == 1:
            path = paths[0]
            if Path(path).is_file():
                self._set_file_path(path)
            elif Path(path).is_dir():
                self._create_and_set_bundle([path])
        elif len(paths) > 1:
            # Multiple files/folders - create bundle
            self._create_and_set_bundle(paths)
        
        return event.action
    
    def _on_drag_enter(self, event):
        """Visual feedback when dragging over drop zone."""
        self._drop_label.configure(
            text="📥 Drop to select file",
            text_color="#3498db",
        )
        return event.action
    
    def _on_drag_leave(self, event):
        """Reset visual feedback when leaving drop zone."""
        current = self.file_entry.get()
        if current:
            self._drop_label.configure(
                text=f"✅ {Path(current).name}",
                text_color="#2ecc71",
            )
        else:
            self._drop_label.configure(
                text="📂 Drop file here or click Browse",
                text_color="gray",
            )
        return event.action

    def _update_file_info(self, path: str):
        """Show file size and 5GB warning after file selection."""
        try:
            size = Path(path).stat().st_size
            name = Path(path).name
            self.file_info_label.configure(
                text=f"📄 {name} — {_human_size(size)}"
            )
            if size > VPS_MAX_FILE_SIZE:
                max_size = _human_size(VPS_MAX_FILE_SIZE)
                self.size_warning_label.configure(
                    text=f"⚠️ File exceeds the {max_size} limit. Transfer may be interrupted by the server."
                )
            else:
                self.size_warning_label.configure(text="")
        except Exception:
            self.file_info_label.configure(text="")
            self.size_warning_label.configure(text="")

    def _browse_save_dir(self):
        path = filedialog.askdirectory(title="Choose save folder")
        if path:
            self._save_dir = path
            self.save_dir_entry.configure(state="normal")
            self.save_dir_entry.delete(0, "end")
            self.save_dir_entry.insert(0, path)
            self.save_dir_entry.configure(state="readonly")

    def _copy_session_code(self):
        """Copy session code to clipboard."""
        code = self.send_code_label.cget("text")
        if code and code != "— — — —":
            self.clipboard_clear()
            self.clipboard_append(code)
            # Brief visual feedback
            old_text = self.copy_code_btn.cget("text")
            self.copy_code_btn.configure(text="✓")
            self.after(1500, lambda: self.copy_code_btn.configure(text=old_text))
    
    def _show_qr_code(self):
        """Display QR code for session code."""
        code = self.send_code_label.cget("text")
        if not code or code == "— — — —":
            return
        
        try:
            import qrcode
            from PIL import Image, ImageTk
        except ImportError:
            messagebox.showinfo(
                "QR Code",
                "QR code feature requires 'qrcode' package.\nInstall with: pip install qrcode[pil]"
            )
            return
        
        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(code)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        img = img.resize((300, 300), Image.Resampling.NEAREST)
        
        # Create popup window
        popup = ctk.CTkToplevel(self)
        popup.title("Session Code QR")
        popup.geometry("350x400")
        popup.resizable(False, False)
        popup.transient(self)
        popup.grab_set()
        
        # Center the popup
        popup.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 350) // 2
        y = self.winfo_y() + (self.winfo_height() - 400) // 2
        popup.geometry(f"+{x}+{y}")
        
        ctk.CTkLabel(
            popup,
            text=f"Scan to receive: {code}",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(pady=(15, 10))
        
        # Display QR code
        photo = ImageTk.PhotoImage(img)
        label = ctk.CTkLabel(popup, image=photo, text="")
        label.image = photo  # Keep reference
        label.pack(pady=10)
        
        ctk.CTkButton(
            popup,
            text="Close",
            width=100,
            command=popup.destroy,
        ).pack(pady=(10, 15))

    def _paste_session_code(self):
        """Paste session code from clipboard into the receive code entry."""
        try:
            text = self.clipboard_get().strip()
        except Exception:
            return
        if text:
            self.recv_code_entry.delete(0, "end")
            self.recv_code_entry.insert(0, text)

    def _copy_log(self):
        """Copy the entire status log to clipboard."""
        self.status_box.configure(state="normal")
        text = self.status_box.get("1.0", "end").strip()
        self.status_box.configure(state="disabled")
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._log("📋 Log copied to clipboard")

    def _save_log(self):
        """Save the status log to a text file."""
        self.status_box.configure(state="normal")
        text = self.status_box.get("1.0", "end").strip()
        self.status_box.configure(state="disabled")
        if not text:
            return

        path = filedialog.asksaveasfilename(
            title="Save log",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"phantomshare_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt",
        )
        if path:
            try:
                header = (
                    f"PhantomShare v{APP_VERSION} — Log Export\n"
                    f"Date: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
                    f"{'=' * 50}\n\n"
                )
                with open(path, "w", encoding="utf-8") as f:
                    f.write(header + text + "\n")
                self._log(f"💾 Log saved: {Path(path).name}")
            except Exception as exc:
                self._log(f"❌ Error saving log: {exc}")

    # ── Diagnostics ──────────────────────────────────────────────────

    def _run_diagnostics(self):
        """Run connectivity diagnostics in a background thread and show results."""
        # Prevent multiple diagnostic windows
        if hasattr(self, "_diag_win") and self._diag_win is not None:
            try:
                self._diag_win.focus()
                return
            except Exception:
                pass

        win = ctk.CTkToplevel(self)
        win.title(f"{APP_NAME} — Diagnostics")
        win.geometry("480x420")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()
        self._diag_win = win

        def _on_close():
            self._diag_win = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

        # Header
        header = ctk.CTkFrame(win, fg_color="#1a3a5c", corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text="🔍  System diagnostics",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color="white",
        ).pack(padx=20, pady=12)

        # Results area
        results_frame = ctk.CTkFrame(win, fg_color="transparent")
        results_frame.pack(fill="both", expand=True, padx=20, pady=(12, 6))

        checks = [
            ("internet",  "🌐  Internet connection"),
            ("dns",       "🔗  Relay server DNS"),
            ("tls",       "🔒  TLS/SSL certificate"),
            ("websocket", "⚡  WebSocket connection"),
            ("latency",   "📊  Latency (ping)"),
        ]

        # Create result rows
        row_widgets = {}
        for i, (key, label_text) in enumerate(checks):
            row = ctk.CTkFrame(results_frame, fg_color="#2a2a2a", corner_radius=8)
            row.pack(fill="x", pady=3)
            row.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                row, text=label_text,
                font=ctk.CTkFont(size=13),
                anchor="w",
            ).grid(row=0, column=0, padx=12, pady=10, sticky="w")

            status_label = ctk.CTkLabel(
                row, text="⏳ Checking...",
                font=ctk.CTkFont(size=12),
                text_color="#f39c12",
                anchor="e",
            )
            status_label.grid(row=0, column=1, padx=12, pady=10, sticky="e")
            row_widgets[key] = (row, status_label)

        # Summary label (below checks)
        summary_label = ctk.CTkLabel(
            win, text="",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        summary_label.pack(pady=(4, 2))

        # Close button
        close_btn = ctk.CTkButton(
            win, text="Close", width=140, height=32,
            fg_color="#1a3a5c", hover_color="#2471a3",
            command=_on_close,
        )
        close_btn.pack(pady=(2, 12))

        def _update_row(key: str, ok: bool, detail: str,
                        color: str | None = None):
            """Thread-safe row update."""
            row_frame, lbl = row_widgets[key]
            if ok:
                txt = f"✅  {detail}"
                clr = color or "#2ecc71"
                bg = "#1a2e1a"
            else:
                txt = f"❌  {detail}"
                clr = color or "#e74c3c"
                bg = "#2e1a1a"

            def _do():
                lbl.configure(text=txt, text_color=clr)
                row_frame.configure(fg_color=bg)
            win.after(0, _do)

        def _run_checks():
            parsed = urlparse(VPS_RELAY_URL)
            host = parsed.hostname or "secureshare-relay.duckdns.org"
            port = parsed.port or 443
            passed = 0
            total = len(checks)

            # 1. Internet connectivity
            try:
                socket.setdefaulttimeout(5)
                socket.create_connection(("8.8.8.8", 53), timeout=5).close()
                _update_row("internet", True, "Connected")
                passed += 1
            except Exception:
                _update_row("internet", False, "No connection")
                # If no internet, mark all remaining as failed
                for key in ["dns", "tls", "websocket", "latency"]:
                    _update_row(key, False, "Skipped (no internet)",
                                "#888888")
                win.after(0, lambda: summary_label.configure(
                    text=f"Result: {passed}/{total} checks passed",
                    text_color="#e74c3c",
                ))
                return

            # 2. DNS resolution
            try:
                t0 = time.perf_counter()
                ip = socket.gethostbyname(host)
                dns_ms = (time.perf_counter() - t0) * 1000
                _update_row("dns", True, f"{ip} ({dns_ms:.0f} ms)")
                passed += 1
            except Exception:
                _update_row("dns", False, f"Could not resolve {host}")
                for key in ["tls", "websocket", "latency"]:
                    _update_row(key, False, "Skipped (DNS error)",
                                "#888888")
                win.after(0, lambda: summary_label.configure(
                    text=f"Result: {passed}/{total} checks passed",
                    text_color="#e74c3c",
                ))
                return

            # 3. TLS/SSL certificate
            try:
                ctx = ssl.create_default_context()
                with socket.create_connection((host, port), timeout=5) as raw:
                    with ctx.wrap_socket(raw, server_hostname=host) as ssock:
                        cert = ssock.getpeercert()
                        issuer_parts = dict(
                            x[0] for x in cert.get("issuer", [])
                        )
                        issuer = issuer_parts.get(
                            "organizationName", "Unknown"
                        )
                        not_after = cert.get("notAfter", "?")
                        _update_row("tls", True,
                                    f"{issuer} ({not_after})")
                        passed += 1
            except ssl.SSLCertVerificationError:
                _update_row("tls", False, "Invalid certificate")
            except Exception as exc:
                _update_row("tls", False, f"Error: {type(exc).__name__}")

            # 4. WebSocket connection
            try:
                import websockets.sync.client as wsc
                t0 = time.perf_counter()
                ws = wsc.connect(
                    f"{VPS_RELAY_URL}/health",
                    open_timeout=5,
                    close_timeout=3,
                )
                ws_ms = (time.perf_counter() - t0) * 1000
                ws.close()
                _update_row("websocket", True, f"OK ({ws_ms:.0f} ms)")
                passed += 1
            except Exception:
                # Try plain HTTPS health check as fallback
                try:
                    import urllib.request
                    health_url = VPS_RELAY_URL.replace(
                        "wss://", "https://"
                    ) + "/health"
                    t0 = time.perf_counter()
                    resp = urllib.request.urlopen(health_url, timeout=5)
                    ws_ms = (time.perf_counter() - t0) * 1000
                    if resp.status == 200:
                        _update_row("websocket", True,
                                    f"OK (HTTP, {ws_ms:.0f} ms)")
                        passed += 1
                    else:
                        _update_row("websocket", False,
                                    f"HTTP {resp.status}")
                except Exception:
                    _update_row("websocket", False,
                                "Could not connect")

            # 5. Latency (3 TCP pings, take median)
            try:
                pings = []
                for _ in range(3):
                    t0 = time.perf_counter()
                    s = socket.create_connection((host, port), timeout=5)
                    elapsed = (time.perf_counter() - t0) * 1000
                    s.close()
                    pings.append(elapsed)
                    time.sleep(0.1)
                pings.sort()
                median = pings[len(pings) // 2]
                if median < 100:
                    quality = "Excellent"
                    clr = "#2ecc71"
                elif median < 250:
                    quality = "Good"
                    clr = "#f1c40f"
                else:
                    quality = "Slow"
                    clr = "#e67e22"
                _update_row("latency", True,
                            f"{median:.0f} ms ({quality})", clr)
                passed += 1
            except Exception:
                _update_row("latency", False, "Could not measure")

            # Summary
            if passed == total:
                s_text = f"✅  All working! ({passed}/{total})"
                s_color = "#2ecc71"
            elif passed >= 3:
                s_text = f"⚠️  Partial ({passed}/{total})"
                s_color = "#f39c12"
            else:
                s_text = f"❌  Problems ({passed}/{total})"
                s_color = "#e74c3c"

            win.after(0, lambda: summary_label.configure(
                text=s_text, text_color=s_color,
            ))

        # Run checks in background thread
        threading.Thread(target=_run_checks, daemon=True).start()

    # ── Help popup ───────────────────────────────────────────────────

    def _toggle_theme(self):
        """Toggle between dark and light mode."""
        if self._current_theme == "dark":
            self._current_theme = "light"
            ctk.set_appearance_mode("light")
            self._tb_theme_btn.configure(text="☀️")
        else:
            self._current_theme = "dark"
            ctk.set_appearance_mode("dark")
            self._tb_theme_btn.configure(text="🌙")

    def _show_help(self):
        """Open a modal help window with step-by-step instructions."""
        # Prevent multiple help windows
        if hasattr(self, "_help_win") and self._help_win is not None:
            try:
                self._help_win.focus()
                return
            except Exception:
                pass

        win = ctk.CTkToplevel(self)
        win.title(f"{APP_NAME} — Help")
        win.geometry("520x560")
        win.resizable(True, True)
        win.transient(self)
        win.grab_set()
        self._help_win = win

        def _on_close():
            self._help_win = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

        # Header with accent background
        header_frame = ctk.CTkFrame(win, fg_color="#1a5276", corner_radius=0)
        header_frame.pack(fill="x")
        ctk.CTkLabel(
            header_frame,
            text="📖  How to use PhantomShare",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="white",
        ).pack(padx=20, pady=14)

        # Scrollable content
        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=12, pady=(8, 6))

        # Section color scheme: (title, body, card_color, title_color, accent_bar)
        max_gb = VPS_MAX_FILE_SIZE // (1024**3)
        help_sections = [
            ("📤 Sending a file", "1. Click 'Browse' and select a file.\n2. Click 'Send' — a session code will appear.\n3. Share this code with the recipient.\n4. Wait for them to connect and verify the code.", "#1a3a2a", "#2ecc71"),
            ("📥 Receiving a file", "1. Enter the session code from the sender.\n2. Choose where to save the file.\n3. Click 'Receive' and wait for the sender.\n4. Verify the code matches and confirm.", "#1a2a3a", "#3498db"),
            ("🔐 Verification", "Both parties must see the same verification code.\nIf codes differ, the connection may be intercepted (MITM attack).\nAlways cancel if codes don't match!", "#2a2a1a", "#f1c40f"),
            ("🔒 Security", "Files are encrypted with AES-256-GCM.\nThe server never sees your data — only encrypted chunks.\nKeys are exchanged using ECDH (Elliptic Curve Diffie-Hellman).", "#1a1a2a", "#9b59b6"),
            (f"📏 Limits", f"Maximum file size: {max_gb} GB\nLarger files may be interrupted by the server.\nFor multiple files, use an archive (ZIP/RAR).", "#2a1a1a", "#e74c3c"),
            ("🔄 Reconnection", "If the connection drops, PhantomShare will try to resume.\nPartial transfers are saved and continued automatically.", "#1a2a3a", "#e67e22"),
        ]

        for item in help_sections:
            title, body, card_bg, title_color = item[:4]
            link_url = item[4] if len(item) > 4 else None

            # Card container
            card = ctk.CTkFrame(scroll, fg_color=card_bg, corner_radius=8)
            card.pack(fill="x", padx=4, pady=4)

            # Colored title
            ctk.CTkLabel(
                card,
                text=title,
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color=title_color,
                anchor="w",
            ).pack(fill="x", padx=12, pady=(10, 4))

            # Body text
            ctk.CTkLabel(
                card,
                text=body,
                font=ctk.CTkFont(size=12),
                text_color="#cccccc",
                anchor="w",
                justify="left",
                wraplength=430,
            ).pack(fill="x", padx=20, pady=(0, 4 if link_url else 10))

            # Clickable link (if provided)
            if link_url:
                _url = link_url  # capture for lambda
                link_btn = ctk.CTkButton(
                    card,
                    text=link_url,
                    font=ctk.CTkFont(size=12, underline=True),
                    text_color="#5dade2",
                    fg_color="transparent",
                    hover_color=card_bg,
                    anchor="w",
                    height=20,
                    command=lambda u=_url: webbrowser.open(u),
                )
                link_btn.pack(fill="x", padx=20, pady=(0, 10))

        # Close button
        ctk.CTkButton(
            win,
            text="Close",
            width=140,
            height=32,
            fg_color="#1a5276",
            hover_color="#2471a3",
            command=_on_close,
        ).pack(pady=(6, 12))

    # ── Update checker ────────────────────────────────────────────

    def _show_startup_tip(self):
        """Show a random helpful tip in the status log on app launch."""
        tip = random.choice(_startup_tips())
        self._log(tip)

    def _set_state(self, state: str):
        """Update the connection status indicator."""
        self._current_state = state
        label_text, color = self._get_state_labels().get(
            state, ("⚪  Ready", "gray")
        )

        def _do():
            self.status_indicator.configure(text=label_text, text_color=color)
        self.after(0, _do)

    def _log(self, text: str):
        """Append a timestamped line to the status textbox (thread-safe)
        and duplicate to Python logger (console + file)."""
        ts = _timestamp()
        line = f"{ts} {text}\n"
        # Duplicate to Python logger so it goes to console + log file
        log.info("[GUI] %s", text)

        def _do():
            self.status_box.configure(state="normal")
            self.status_box.insert("end", line)
            self.status_box.see("end")
            self.status_box.configure(state="disabled")
        self.after(0, _do)

    def _set_progress(self, done: int, total: int, speed: float):
        def _do():
            frac = done / total if total > 0 else 0
            self.progress_bar.set(frac)
            pct = frac * 100
            eta = (total - done) / speed if speed > 0 else 0
            self.progress_label.configure(
                text=(
                    f"{pct:.1f}%  ·  {_human_size(done)} / {_human_size(total)}"
                    f"  ·  ⚡ {_human_speed(speed)}  ·  ⏱ {_human_eta(eta)}"
                )
            )
        self.after(0, _do)

    def _set_buttons(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        cancel_state = "disabled" if enabled else "normal"

        def _do():
            self.send_btn.configure(state=state)
            self.recv_btn.configure(state=state)
            self.cancel_btn.configure(state=cancel_state)
            self.copy_code_btn.configure(
                state="normal" if not enabled else "disabled"
            )
            self.qr_code_btn.configure(
                state="normal" if not enabled else "disabled"
            )
        self.after(0, _do)

    def _reset_ui(self):
        def _do():
            self.progress_bar.set(0)
            self.progress_label.configure(text="")
        self.after(0, _do)

    def _on_cancel(self):
        self._cancel_flag = True
        if self._current_transfer:
            self._current_transfer.cancel()
        self._log("⏹ Cancelled by user")

    # ── Verification dialog (mandatory MITM check) ────────────────

    def _verify_connection(self, code: str) -> bool:
        """Show a modal verification dialog.  Thread-safe (called from worker).

        Returns True if the user confirms the codes match,
        False if cancelled or timed out.
        """
        result: list[Optional[bool]] = [None]
        event = threading.Event()

        def _show():
            dialog = ctk.CTkToplevel(self)
            dialog.title("🔐 Connection verification")
            dialog.geometry("440x320")
            dialog.resizable(False, False)
            dialog.transient(self)
            dialog.grab_set()
            dialog.focus_force()

            # Center over parent
            dialog.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() - 440) // 2
            y = self.winfo_y() + (self.winfo_height() - 320) // 2
            dialog.geometry(f"+{max(0, x)}+{max(0, y)}")

            ctk.CTkLabel(
                dialog,
                text="🔐 Connection verification",
                font=ctk.CTkFont(size=18, weight="bold"),
            ).pack(pady=(20, 8))

            ctk.CTkLabel(
                dialog,
                text="Make sure both participants\nsee the same code:",
                font=ctk.CTkFont(size=13),
                justify="center",
            ).pack(pady=(0, 12))

            code_frame = ctk.CTkFrame(dialog)
            code_frame.pack(padx=40, pady=8, fill="x")
            ctk.CTkLabel(
                code_frame,
                text=code,
                font=ctk.CTkFont(family="Consolas", size=32, weight="bold"),
                text_color="#2ecc71",
            ).pack(pady=16)

            ctk.CTkLabel(
                dialog,
                text="⚠ If codes differ — the connection may be\nintercepted by an attacker (MITM)!",
                font=ctk.CTkFont(size=12),
                text_color="#e74c3c",
                justify="center",
            ).pack(pady=(8, 14))

            btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
            btn_frame.pack(pady=(4, 16))

            def _confirm():
                result[0] = True
                dialog.grab_release()
                dialog.destroy()
                event.set()

            def _cancel():
                result[0] = False
                dialog.grab_release()
                dialog.destroy()
                event.set()

            ctk.CTkButton(
                btn_frame,
                text="✅ Codes match",
                fg_color="#27ae60",
                hover_color="#2ecc71",
                command=_confirm,
                width=170,
            ).pack(side="left", padx=8)

            ctk.CTkButton(
                btn_frame,
                text="❌ Cancel",
                fg_color="#c0392b",
                hover_color="#e74c3c",
                command=_cancel,
                width=170,
            ).pack(side="right", padx=8)

            dialog.protocol("WM_DELETE_WINDOW", _cancel)

        self.after(0, _show)
        event.wait(timeout=120)
        return result[0] if result[0] is not None else False

    # ════════════════════════════════════════════════════════════════
    #  Status callback adapter for ws_relay → GUI state indicator
    # ════════════════════════════════════════════════════════════════

    def _make_status_cb(self):
        """Return a status callback that updates both the log and the state indicator."""
        def _on_status(msg: str):
            self._log(msg)
            # Detect state from emoji prefixes (language-independent)
            if "🌐" in msg:  # 🌐
                self._set_state(self.STATE_CONNECTING)
            elif "🔑" in msg and "..." in msg:  # 🔑 + ...
                self._set_state(self.STATE_KEY_EXCHANGE)
            elif "🔑" in msg:  # 🔑
                self._set_state(self.STATE_VERIFYING)
            elif "⏳" in msg:  # ⏳
                self._set_state(self.STATE_WAITING)
            elif "📦" in msg or ("📥" in msg and ":" in msg):  # 📦 or 📥:
                self._set_state(self.STATE_TRANSFERRING)
            elif "🎉" in msg:  # 🎉
                self._set_state(self.STATE_DONE)
            elif "✅" in msg and "/" in msg:  # ✅ x/y
                self._set_state(self.STATE_DONE)
            elif "❌" in msg:  # ❌
                self._set_state(self.STATE_ERROR)
        return _on_status

    # ════════════════════════════════════════════════════════════════
    #  SEND workflow
    # ════════════════════════════════════════════════════════════════

    def _on_send(self):
        filepath = self.file_entry.get()
        if not filepath or not Path(filepath).is_file():
            messagebox.showwarning("File", "Please choose a file to send.")
            return

        # Check file size > 5 GB — warn but allow
        file_size = Path(filepath).stat().st_size
        if file_size > VPS_MAX_FILE_SIZE:
            proceed = messagebox.askyesno(
                "Large file",
                f"File ({_human_size(file_size)}) exceeds the server limit ({_human_size(VPS_MAX_FILE_SIZE)}).\n\nTransfer may be interrupted.\nContinue?",
            )
            if not proceed:
                return

        code = _generate_code()
        self.send_code_label.configure(text=code)
        self._cancel_flag = False
        self._reset_ui()
        self._set_buttons(False)
        self._set_state(self.STATE_IDLE)

        # Clear status
        self.status_box.configure(state="normal")
        self.status_box.delete("1.0", "end")
        self.status_box.configure(state="disabled")

        self._worker_thread = threading.Thread(
            target=self._send_worker,
            args=(filepath, code),
            daemon=True,
        )
        self._worker_thread.start()

    def _send_worker(self, filepath: str, code: str):
        """Send a file through the VPS relay server."""
        t_start = time.monotonic()
        file_size = Path(filepath).stat().st_size
        outcome = "error"
        error_type = ""
        try:
            sender = VPSRelaySender(
                session_code=code,
                filepath=filepath,
                on_progress=self._set_progress,
                on_status=self._make_status_cb(),
                on_verify=self._verify_connection,
            )
            self._current_transfer = sender

            ok = sender.send()

            if ok:
                outcome = "success"
                self._set_state(self.STATE_DONE)
                self._log("🎉 Transfer complete!")
            elif self._cancel_flag:
                outcome = "cancelled"
                self._set_state(self.STATE_IDLE)
                self._log("⏹ Transfer cancelled")
            else:
                outcome = "error"
                self._set_state(self.STATE_ERROR)
                self._log("❌ Transfer failed")

        except Exception as exc:
            outcome = "error"
            error_type = type(exc).__name__
            self._set_state(self.STATE_ERROR)
            self._log(f"❌ Error: {exc}")
            log.exception("Send worker error")
            report_crash(exc, state="send_worker")
        finally:
            self._current_transfer = None
            self._set_buttons(True)
            # Anonymous session telemetry
            report_session(
                role="sender",
                outcome=outcome,
                file_size=file_size,
                duration_s=time.monotonic() - t_start,
                error_type=error_type,
            )

    # ════════════════════════════════════════════════════════════════
    #  RECEIVE workflow
    # ════════════════════════════════════════════════════════════════

    def _on_receive(self):
        code = self.recv_code_entry.get().strip().lower()
        if not code or len(code.replace("-", "")) < SESSION_CODE_LENGTH:
            messagebox.showwarning("Code", "Enter the session code from the sender.")
            return

        save_dir = self._save_dir
        if not save_dir or not Path(save_dir).is_dir():
            messagebox.showwarning("Folder", "Choose a folder to save to.")
            return

        self._cancel_flag = False
        self._reset_ui()
        self._set_buttons(False)
        self._set_state(self.STATE_IDLE)

        self.status_box.configure(state="normal")
        self.status_box.delete("1.0", "end")
        self.status_box.configure(state="disabled")

        self._worker_thread = threading.Thread(
            target=self._recv_worker,
            args=(code, save_dir),
            daemon=True,
        )
        self._worker_thread.start()

    def _recv_worker(self, code: str, save_dir: str):
        """Receive a file through the VPS relay server."""
        t_start = time.monotonic()
        outcome = "error"
        error_type = ""
        file_size = 0
        try:
            receiver = VPSRelayReceiver(
                session_code=code,
                save_dir=save_dir,
                on_progress=self._set_progress,
                on_status=self._make_status_cb(),
                on_verify=self._verify_connection,
            )
            self._current_transfer = receiver

            result = receiver.receive()

            if result:
                outcome = "success"
                try:
                    file_size = Path(result).stat().st_size
                except Exception:
                    pass
                
                # Auto-extract bundles
                from .bundler import is_bundle, extract_bundle, get_bundle_info
                if is_bundle(result):
                    try:
                        info = get_bundle_info(result)
                        self._log(f"📦 Bundle detected: {info['file_count']} files")
                        extract_dir = Path(save_dir) / result.stem.replace('.phantombundle', '')
                        extracted = extract_bundle(result, extract_dir)
                        self._log(f"📦 Extracted {len(extracted)} files to {extract_dir.name}/")
                        # Remove the bundle file after extraction
                        result.unlink()
                    except Exception as e:
                        self._log(f"⚠️ Bundle extraction failed: {e}")
                
                self._set_state(self.STATE_DONE)
                self._log(f"🎉 File saved: {result}")
            elif self._cancel_flag:
                outcome = "cancelled"
                self._set_state(self.STATE_IDLE)
                self._log("⏹ Transfer cancelled")
            else:
                outcome = "error"
                self._set_state(self.STATE_ERROR)
                self._log("❌ Transfer failed")

        except Exception as exc:
            outcome = "error"
            error_type = type(exc).__name__
            self._set_state(self.STATE_ERROR)
            self._log(f"❌ Error: {exc}")
            log.exception("Receive worker error")
            report_crash(exc, state="recv_worker")
        finally:
            self._current_transfer = None
            self._set_buttons(True)
            # Anonymous session telemetry
            report_session(
                role="receiver",
                outcome=outcome,
                file_size=file_size,
                duration_s=time.monotonic() - t_start,
                error_type=error_type,
            )
