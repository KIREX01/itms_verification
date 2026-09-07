"""
Native desktop dialog for selecting evidence photos or folders.

Runs as a lightweight standalone Tkinter subprocess so that:
- It pops up directly on top of the operator's desktop.
- It never blocks or interferes with the TUI's asyncio event loop.
- It supports both multi-file selection and whole folder/SD-card selection.
- It returns JSON-encoded file paths on stdout.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


def _run_native_picker_gui() -> List[str]:
    """Runs the Tkinter UI and returns selected image paths."""
    try:
        import tkinter as tk
        from tkinter import filedialog, font, messagebox
    except ImportError:
        return []

    selected_paths: List[str] = []

    root = tk.Tk()
    root.title("ITMS - Ingest Evidence Photos")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    # Styling & Dimensions
    w, h = 460, 220
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.configure(bg="#f8fafc")

    title_font = font.Font(family="Segoe UI", size=12, weight="bold")
    sub_font = font.Font(family="Segoe UI", size=9)
    btn_font = font.Font(family="Segoe UI", size=10, weight="bold")

    # Header frame
    header_frame = tk.Frame(root, bg="#1e3a8a", padx=16, pady=12)
    header_frame.pack(fill="x")

    lbl_title = tk.Label(
        header_frame,
        text="Evidence Photo Vault Ingestion",
        font=title_font,
        fg="#ffffff",
        bg="#1e3a8a",
    )
    lbl_title.pack(anchor="w")

    lbl_sub = tk.Label(
        header_frame,
        text="Select photos from camera, phone export, or SD card folder",
        font=sub_font,
        fg="#bfdbfe",
        bg="#1e3a8a",
    )
    lbl_sub.pack(anchor="w")

    # Body frame
    body_frame = tk.Frame(root, bg="#f8fafc", padx=20, pady=16)
    body_frame.pack(fill="both", expand=True)

    def on_select_files():
        root.attributes("-topmost", False)
        filetypes = [
            ("Evidence Photos", "*.jpg *.jpeg *.png *.webp *.bmp *.tiff *.tif"),
            ("All Files", "*.*"),
        ]
        chosen = filedialog.askopenfilenames(
            parent=root,
            title="Select Evidence Photos to Ingest",
            filetypes=filetypes,
        )
        if chosen:
            selected_paths.extend([os.path.abspath(p) for p in chosen if Path(p).suffix.lower() in VALID_EXTENSIONS])
            root.destroy()
        else:
            root.attributes("-topmost", True)

    def on_select_folder():
        root.attributes("-topmost", False)
        folder = filedialog.askdirectory(
            parent=root,
            title="Select Folder Containing Evidence Photos (e.g. SD Card)",
        )
        if folder:
            p_folder = Path(folder)
            found = [
                str(p.resolve()) for p in p_folder.rglob("*")
                if p.is_file() and p.suffix.lower() in VALID_EXTENSIONS
            ]
            selected_paths.extend(found)
            root.destroy()
        else:
            root.attributes("-topmost", True)

    def on_cancel():
        root.destroy()

    root.bind("<Escape>", lambda e: on_cancel())

    # Buttons layout
    btn_container = tk.Frame(body_frame, bg="#f8fafc")
    btn_container.pack(fill="x", pady=6)

    btn_files = tk.Button(
        btn_container,
        text="🖼️  Select Photo Files",
        font=btn_font,
        bg="#2563eb",
        fg="#ffffff",
        activebackground="#1d4ed8",
        activeforeground="#ffffff",
        padx=12,
        pady=8,
        relief="flat",
        cursor="hand2",
        command=on_select_files,
    )
    btn_files.pack(side="left", fill="x", expand=True, padx=(0, 6))

    btn_folder = tk.Button(
        btn_container,
        text="📂  Select Folder / SD",
        font=btn_font,
        bg="#0d9488",
        fg="#ffffff",
        activebackground="#0f766e",
        activeforeground="#ffffff",
        padx=12,
        pady=8,
        relief="flat",
        cursor="hand2",
        command=on_select_folder,
    )
    btn_folder.pack(side="right", fill="x", expand=True, padx=(6, 0))

    btn_cancel = tk.Button(
        body_frame,
        text="Cancel (Esc)",
        font=sub_font,
        bg="#e2e8f0",
        fg="#475569",
        activebackground="#cbd5e1",
        activeforeground="#1e293b",
        relief="flat",
        cursor="hand2",
        command=on_cancel,
    )
    btn_cancel.pack(anchor="e", pady=(10, 0))

    root.mainloop()
    return selected_paths


def prompt_native_photo_selection() -> List[str]:
    """
    Launches the native photo picker in a separate Python process.
    Returns a list of absolute file paths chosen by the operator, or [] if cancelled.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "core.services.file_dialog"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            # Parse JSON returned on stdout
            data = json.loads(proc.stdout.strip())
            if isinstance(data, list):
                return [p for p in data if os.path.isfile(p)]
        return []
    except Exception:
        # Fallback to in-process if subprocess execution is restricted
        return _run_native_picker_gui()


if __name__ == "__main__":
    results = _run_native_picker_gui()
    print(json.dumps(results))
