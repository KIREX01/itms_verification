"""
Native desktop dialog for selecting evidence photos or folders.

Runs as a lightweight standalone Tkinter subprocess so that:
- It pops up directly on top of the operator's desktop.
- It never blocks or interferes with the TUI's asyncio event loop.
- It supports classified Front-only, Rear-only, Batch Folder (with front/rear subfolder detection),
  and General mixed selection.
- It returns JSON-encoded records [{"path": str, "orientation": str}] on stdout.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


def scan_batch_folder(folder_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Scans a batch directory for classified front/ and rear/ subfolders.

    Standard Ingestion Structure:
      batch_root/
        ├── front/  (or Front, FRONT, fronts, forward)
        └── rear/   (or Rear, REAR, rears, back, backs)

    Returns:
      {
        "items": List[Dict[str, str]] (path, orientation),
        "front_count": int,
        "rear_count": int,
        "other_count": int,
        "is_symmetric": bool,
        "discrepancy": int,
        "status": "SYMMETRIC" | "ASYMMETRIC" | "MISSING_FRONT" | "MISSING_REAR" | "EMPTY",
        "message": str,
        "front_files": List[Path],
        "rear_files": List[Path],
      }
    """
    p_folder = Path(folder_path).resolve()
    if not p_folder.is_dir():
        return {
            "items": [],
            "front_count": 0,
            "rear_count": 0,
            "other_count": 0,
            "is_symmetric": False,
            "discrepancy": 0,
            "status": "EMPTY",
            "message": f"Specified path is not a valid directory: {p_folder}",
            "front_files": [],
            "rear_files": [],
        }

    front_files: List[Path] = []
    rear_files: List[Path] = []
    other_files: List[Path] = []
    seen: set = set()

    # Discover classified subdirectories
    subdirs = [d for d in p_folder.iterdir() if d.is_dir()]
    front_subdirs = [
        d for d in subdirs if any(f in d.name.lower() for f in ("front", "forward", "fronts"))
    ]
    rear_subdirs = [
        d for d in subdirs if any(r in d.name.lower() for r in ("rear", "back", "rears", "backs"))
    ]

    for fd in front_subdirs:
        for p in fd.rglob("*"):
            if p.is_file() and p.suffix.lower() in VALID_EXTENSIONS and p.resolve() not in seen:
                seen.add(p.resolve())
                front_files.append(p.resolve())

    for rd in rear_subdirs:
        for p in rd.rglob("*"):
            if p.is_file() and p.suffix.lower() in VALID_EXTENSIONS and p.resolve() not in seen:
                seen.add(p.resolve())
                rear_files.append(p.resolve())

    # Check remaining images in root or other subdirectories
    for p in p_folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in VALID_EXTENSIONS and p.resolve() not in seen:
            seen.add(p.resolve())
            try:
                rel_parts = [seg.lower() for seg in p.relative_to(p_folder).parts[:-1]]
            except Exception:
                rel_parts = [seg.lower() for seg in p.parts[:-1]]

            orient = ""
            for seg in reversed(rel_parts):
                tokens = [t.strip() for t in seg.replace("_", " ").replace("-", " ").split()]
                if any(f in tokens or f == seg for f in ("front", "forward", "fronts")):
                    orient = "FRONT"
                    break
                if any(r in tokens or r == seg for r in ("rear", "back", "rears", "backs")):
                    orient = "REAR"
                    break

            # Check filename stem if path was inconclusive
            if not orient:
                stem_lower = p.stem.lower()
                tokens = [t.strip() for t in stem_lower.replace("_", " ").replace("-", " ").split()]
                if any(f in tokens for f in ("front", "forward", "fronts")):
                    orient = "FRONT"
                elif any(r in tokens for r in ("rear", "back", "rears", "backs")):
                    orient = "REAR"

            if orient == "FRONT":
                front_files.append(p.resolve())
            elif orient == "REAR":
                rear_files.append(p.resolve())
            else:
                other_files.append(p.resolve())

    # Build items list
    items: List[Dict[str, str]] = []
    for p in front_files:
        items.append({"path": str(p), "orientation": "FRONT"})
    for p in rear_files:
        items.append({"path": str(p), "orientation": "REAR"})
    for p in other_files:
        items.append({"path": str(p), "orientation": ""})

    n_front = len(front_files)
    n_rear = len(rear_files)
    n_other = len(other_files)
    discrepancy = abs(n_front - n_rear)

    if n_front == 0 and n_rear == 0 and n_other == 0:
        status = "EMPTY"
        msg = "No valid evidence photos found in the selected folder."
        is_sym = False
    elif n_front == 0 and n_rear > 0:
        status = "MISSING_FRONT"
        msg = f"Incomplete batch: Found {n_rear} REAR photos, but 0 FRONT photos. Pairing requires both sides."
        is_sym = False
    elif n_rear == 0 and n_front > 0:
        status = "MISSING_REAR"
        msg = f"Incomplete batch: Found {n_front} FRONT photos, but 0 REAR photos. Pairing requires both sides."
        is_sym = False
    elif n_front != n_rear:
        status = "ASYMMETRIC"
        msg = (
            f"Photo count mismatch detected: {n_front} FRONT vs {n_rear} REAR "
            f"({discrepancy} photo difference). Pairs will be incomplete."
        )
        is_sym = False
    else:
        status = "SYMMETRIC"
        msg = f"✓ Symmetric batch validated: {n_front} FRONT and {n_rear} REAR photos detected (1:1 ratio)."
        is_sym = True

    return {
        "items": items,
        "front_count": n_front,
        "rear_count": n_rear,
        "other_count": n_other,
        "is_symmetric": is_sym,
        "discrepancy": discrepancy,
        "status": status,
        "message": msg,
        "front_files": front_files,
        "rear_files": rear_files,
    }


def _run_native_picker_gui() -> List[Dict[str, str]]:
    """Runs the standardized Tkinter UI and returns selected image items with orientation."""
    try:
        import tkinter as tk
        from tkinter import filedialog, font, messagebox
    except ImportError:
        return []

    selected_items: List[Dict[str, str]] = []

    root = tk.Tk()
    root.title("ITMS - Standardized Evidence Vault Ingestion")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    # Styling & Dimensions
    w, h = 620, 370
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.configure(bg="#f8fafc")

    title_font = font.Font(family="Segoe UI", size=12, weight="bold")
    sub_font = font.Font(family="Segoe UI", size=9)
    btn_font = font.Font(family="Segoe UI", size=10, weight="bold")
    code_font = font.Font(family="Consolas", size=9)

    # Header frame
    header_frame = tk.Frame(root, bg="#1e3a8a", padx=18, pady=12)
    header_frame.pack(fill="x")

    lbl_title = tk.Label(
        header_frame,
        text="Standardized Evidence Vault Ingestion",
        font=title_font,
        fg="#ffffff",
        bg="#1e3a8a",
    )
    lbl_title.pack(anchor="w")

    lbl_sub = tk.Label(
        header_frame,
        text="Unified batch folder ingestion with strict front/rear photo count validation",
        font=sub_font,
        fg="#bfdbfe",
        bg="#1e3a8a",
    )
    lbl_sub.pack(anchor="w")

    # Body frame
    body_frame = tk.Frame(root, bg="#f8fafc", padx=22, pady=14)
    body_frame.pack(fill="both", expand=True)

    # Architectural Guidance Box
    guide_frame = tk.Frame(body_frame, bg="#f1f5f9", padx=14, pady=10, relief="solid", bd=1)
    guide_frame.pack(fill="x", pady=(0, 14))

    lbl_guide_title = tk.Label(
        guide_frame,
        text="Standard Batch Folder Structure:",
        font=font.Font(family="Segoe UI", size=9, weight="bold"),
        fg="#1e293b",
        bg="#f1f5f9",
    )
    lbl_guide_title.pack(anchor="w")

    struct_text = (
        "📁 Batch_Folder/\n"
        "   ├── 🚘 front/   (Front-facing plates & motorcycle headlamps)\n"
        "   └── 🏍️ rear/    (Rear-facing plates & motorcycle brackets/taillights)"
    )
    lbl_struct = tk.Label(
        guide_frame,
        text=struct_text,
        font=code_font,
        fg="#0f766e",
        bg="#f1f5f9",
        justify="left",
    )
    lbl_struct.pack(anchor="w", pady=(2, 4))

    lbl_rule = tk.Label(
        guide_frame,
        text="• Ingests both orientations into ONE single batch ID so photos are never compromised.\n"
             "• Validates that /front and /rear photo counts match 1:1 before proceeding.",
        font=sub_font,
        fg="#475569",
        bg="#f1f5f9",
        justify="left",
    )
    lbl_rule.pack(anchor="w")

    def handle_scanned_result(scan_res: Dict[str, Any]) -> bool:
        """Evaluates scan result and displays appropriate validation dialog."""
        status = scan_res["status"]
        n_front = scan_res["front_count"]
        n_rear = scan_res["rear_count"]
        diff = scan_res["discrepancy"]

        if status == "EMPTY":
            messagebox.showerror(
                "No Photos Found",
                "No valid evidence photos (.jpg, .png, etc.) were found in the selected folder.\n\n"
                "Please verify the folder contains 'front' and 'rear' subfolders with evidence images.",
                parent=root,
            )
            return False

        if status == "MISSING_FRONT":
            messagebox.showerror(
                "Incomplete Batch Structure - Missing FRONT",
                f"Incomplete batch folder:\n\n"
                f"• FRONT photos: 0\n"
                f"• REAR photos:  {n_rear}\n\n"
                "Vehicle verification requires front photos to form 1:1 pairs.\n"
                "Please add the 'front' subfolder with corresponding photos.",
                parent=root,
            )
            return False

        if status == "MISSING_REAR":
            messagebox.showerror(
                "Incomplete Batch Structure - Missing REAR",
                f"Incomplete batch folder:\n\n"
                f"• FRONT photos: {n_front}\n"
                f"• REAR photos:  0\n\n"
                "Vehicle verification requires rear photos to form 1:1 pairs.\n"
                "Please add the 'rear' subfolder with corresponding photos.",
                parent=root,
            )
            return False

        if status == "ASYMMETRIC":
            missing_side = "REAR" if n_front > n_rear else "FRONT"
            warn_msg = (
                f"⚠️ Photo Count Mismatch Warning!\n\n"
                f"• FRONT photos found: {n_front}\n"
                f"• REAR photos found:  {n_rear}\n"
                f"• Discrepancy:        {diff} photo(s) missing from {missing_side}!\n\n"
                "Vehicle verification requires an equal 1:1 ratio between front and rear.\n"
                f"Asymmetric ingestion will leave {diff} vehicle(s) unpaired (INCOMPLETE).\n\n"
                "Do you want to proceed with asymmetric ingestion anyway?"
            )
            proceed = messagebox.askyesno(
                "Confirm Asymmetric Photo Count",
                warn_msg,
                icon="warning",
                parent=root,
                default="no",
            )
            return proceed

        # Symmetric batch validated!
        messagebox.showinfo(
            "Batch Symmetry Validated",
            f"✓ Symmetric Batch Validated!\n\n"
            f"• FRONT photos: {n_front}\n"
            f"• REAR photos:  {n_rear}\n\n"
            f"1:1 pair ratio confirmed ({n_front} pairs expected).\n"
            f"Ingesting into a single unified Evidence Vault batch...",
            parent=root,
        )
        return True

    def on_select_batch_folder():
        root.attributes("-topmost", False)
        folder = filedialog.askdirectory(
            parent=root,
            title="Select Batch Folder (containing front/ and rear/ subfolders)",
        )
        if folder:
            scan_res = scan_batch_folder(folder)
            if handle_scanned_result(scan_res):
                selected_items.extend(scan_res["items"])
                root.destroy()
            else:
                root.attributes("-topmost", True)
        else:
            root.attributes("-topmost", True)

    def on_select_custom_folder():
        root.attributes("-topmost", False)
        folder = filedialog.askdirectory(
            parent=root,
            title="Select Custom / Field Folder to Inspect and Auto-Classify",
        )
        if folder:
            scan_res = scan_batch_folder(folder)
            if handle_scanned_result(scan_res):
                selected_items.extend(scan_res["items"])
                root.destroy()
            else:
                root.attributes("-topmost", True)
        else:
            root.attributes("-topmost", True)

    def on_cancel():
        root.destroy()

    root.bind("<Escape>", lambda e: on_cancel())

    # Primary Action Button
    btn_batch = tk.Button(
        body_frame,
        text="📂  Select Unified Batch Folder (with front/ & rear/ subfolders)",
        font=btn_font,
        bg="#0d9488",
        fg="#ffffff",
        activebackground="#0f766e",
        activeforeground="#ffffff",
        padx=14,
        pady=10,
        relief="flat",
        cursor="hand2",
        command=on_select_batch_folder,
    )
    btn_batch.pack(fill="x", pady=(0, 8))

    # Secondary Action Button
    btn_custom = tk.Button(
        body_frame,
        text="🔍  Inspect & Auto-Classify Other Folder Structure",
        font=font.Font(family="Segoe UI", size=9, weight="bold"),
        bg="#0284c7",
        fg="#ffffff",
        activebackground="#0369a1",
        activeforeground="#ffffff",
        padx=10,
        pady=8,
        relief="flat",
        cursor="hand2",
        command=on_select_custom_folder,
    )
    btn_custom.pack(fill="x", pady=(0, 8))

    # Footer row with Cancel button
    footer_row = tk.Frame(body_frame, bg="#f8fafc")
    footer_row.pack(fill="x", pady=(4, 0))

    btn_cancel = tk.Button(
        footer_row,
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
    btn_cancel.pack(side="right")

    root.mainloop()
    return selected_items


def prompt_native_photo_selection() -> List[Dict[str, str]]:
    """
    Launches the standardized native photo picker in a separate Python process.
    Returns a list of dicts [{"path": str, "orientation": str}] chosen by operator, or [] if cancelled.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "core.services.file_dialog"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout.strip())
            if isinstance(data, list):
                results = []
                for item in data:
                    if isinstance(item, dict) and "path" in item and os.path.isfile(item["path"]):
                        results.append(item)
                    elif isinstance(item, str) and os.path.isfile(item):
                        results.append({"path": item, "orientation": ""})
                return results
        return []
    except Exception:
        return _run_native_picker_gui()


def prompt_native_directory_selection(initial_dir: Optional[str] = None, title: str = "Select Directory") -> Optional[str]:
    """
    Launches a native OS directory picker dialog in a lightweight subprocess.
    Returns the chosen directory path as a string, or None if cancelled.
    """
    code = f"""
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
dir_path = filedialog.askdirectory(title={title!r}, initialdir={initial_dir!r} or None)
root.destroy()
if dir_path:
    print(dir_path)
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            selected = proc.stdout.strip().splitlines()[-1].strip()
            if os.path.isdir(selected):
                return selected
    except Exception as exc:
        logger.debug("Native directory picker subprocess error: %s", exc)
    return None


if __name__ == "__main__":
    results = _run_native_picker_gui()
    print(json.dumps(results))

