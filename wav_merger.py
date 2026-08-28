"""
DJI Mic recording organizer.

This app scans WAV files, groups adjacent DJI Mic chunks into recording
sessions, and exports each session as a compact audio file.
"""

from __future__ import annotations

import json
import os
import plistlib
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

try:
    from send2trash import send2trash
except ImportError:  # setup installs it for normal use.
    send2trash = None


APP_TITLE = "DJI Mic 录音整理工具"
CONFIG_PATH = Path.home() / ".wav_merger_config.json"
SUPPORTED_EXTENSIONS = {".wav", ".wave"}
DJI_FOLDER_PATTERN = re.compile(r"^TX_MIC\d{3}_\d{8}_\d{6}$", re.IGNORECASE)
DJI_FILE_PATTERN = re.compile(r"^TX\d+_MIC\d+_\d{8}_\d{6}.*\.(?:wav|wave)$", re.IGNORECASE)


FORMAT_PRESETS = {
    "m4a": {
        "label": "M4A / AAC（推荐）",
        "extension": ".m4a",
        "codec_args": ["-c:a", "aac"],
        "bitrates": ["48", "64", "96", "128"],
        "default_bitrate": "64",
    },
    "mp3": {
        "label": "MP3（兼容优先）",
        "extension": ".mp3",
        "codec_args": ["-c:a", "libmp3lame"],
        "bitrates": ["64", "96", "128", "192"],
        "default_bitrate": "96",
    },
    "wav": {
        "label": "WAV（无压缩）",
        "extension": ".wav",
        "codec_args": ["-c:a", "pcm_s16le"],
        "bitrates": [],
        "default_bitrate": "",
    },
}


@dataclass
class AudioFile:
    path: Path
    duration: float
    size: int
    start_time: datetime

    @property
    def end_time(self) -> datetime:
        return self.start_time + timedelta(seconds=self.duration)

    @property
    def display_name(self) -> str:
        return self.path.name


@dataclass
class RecordingGroup:
    files: list[AudioFile] = field(default_factory=list)
    title: str = ""

    @property
    def start_time(self) -> datetime | None:
        return self.files[0].start_time if self.files else None

    @property
    def end_time(self) -> datetime | None:
        return self.files[-1].end_time if self.files else None

    @property
    def duration(self) -> float:
        return sum(item.duration for item in self.files)

    @property
    def size(self) -> int:
        return sum(item.size for item in self.files)


@dataclass(frozen=True)
class DjiMicVolume:
    mount_point: Path
    device_identifier: str
    volume_name: str
    volume_uuid: str


def path_is_within(path: Path, parent: Path) -> bool:
    """Return whether path belongs to parent, including not-yet-created paths."""
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def dji_recording_layout_present(mount_point: Path) -> bool:
    """Recognize the directory and filename layout written by DJI Mic transmitters."""
    try:
        for child in mount_point.iterdir():
            if not child.is_dir() or not DJI_FOLDER_PATTERN.match(child.name):
                continue
            try:
                if any(item.is_file() and DJI_FILE_PATTERN.match(item.name) for item in child.iterdir()):
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def disk_info_is_dji_mic(info: dict, mount_point: Path) -> bool:
    """Use both disk metadata and DJI's recording layout to avoid ejecting other disks."""
    is_external = bool(
        info.get("RemovableMediaOrExternalDevice")
        or info.get("RemovableMedia")
        or info.get("Removable")
        or info.get("Ejectable")
    ) and not bool(info.get("Internal") or info.get("OSInternalMedia"))
    if not is_external:
        return False

    metadata = " ".join(
        str(info.get(key, ""))
        for key in (
            "VolumeName",
            "MediaName",
            "IORegistryEntryName",
            "DeviceVendor",
            "DeviceModel",
        )
    ).casefold()
    explicit_dji_name = bool(re.search(r"\bdji[\s_-]*mic\b", metadata))
    transmitter_name = bool(re.search(r"\bwireles+s?\s+mic\s+tx\b|\bmic\s+tx\b", metadata))
    has_layout = dji_recording_layout_present(mount_point)
    return (explicit_dji_name and has_layout) or (transmitter_name and has_layout)


class WavMergerApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1220x800")
        self.root.minsize(1040, 680)

        self.config = self.load_config()
        self.ffmpeg = self.locate_ffmpeg()

        self.audio_files: list[AudioFile] = []
        self.groups: list[RecordingGroup] = []
        self.selected_folder = tk.StringVar(value=self.config.get("last_folder", ""))
        self.output_folder = tk.StringVar(value=self.config.get("output_folder", ""))
        self.threshold_minutes = tk.StringVar(value=str(self.config.get("threshold_minutes", 2)))
        self.format_choice = tk.StringVar(value=self.normalize_format_key(self.config.get("format", "m4a")))
        self.format_label = tk.StringVar()
        self.bitrate = tk.StringVar(value=self.config.get("bitrate", "64"))
        self.mix_to_mono = tk.BooleanVar(value=self.config.get("mix_to_mono", True))
        self.recursive_scan = tk.BooleanVar(value=self.config.get("recursive_scan", True))
        self.export_selected_only = tk.BooleanVar(value=False)
        self.delete_sources_after_export = tk.BooleanVar(value=self.config.get("delete_sources_after_export", False))
        self.status_text = tk.StringVar(value="请选择 DJI Mic 录音文件夹。")
        self.library_summary = tk.StringVar(value="尚未导入录音")
        self.selection_summary = tk.StringVar(value="选择左侧会话，查看其中的录音文件")
        self.export_button_text = tk.StringVar(value="导出会话")
        self.progress_text = tk.StringVar(value="")
        self.progress_value = tk.DoubleVar(value=0)
        self.work_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.is_exporting = False
        self.is_ejecting = False
        self.pending_eject: DjiMicVolume | None = None
        self.active_export_paths: list[Path] = []
        self.current_process: subprocess.Popen[str] | None = None

        self.build_ui()
        self.update_format_controls()
        self.update_button_states()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(120, self.drain_work_queue)

        if not self.ffmpeg:
            self.status_text.set("未找到 ffmpeg。请先运行 setup.sh，或用 Homebrew 安装 ffmpeg。")

    def load_config(self) -> dict:
        if not CONFIG_PATH.exists():
            return {}
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save_config(self) -> None:
        data = {
            "last_folder": self.selected_folder.get(),
            "output_folder": self.output_folder.get(),
            "threshold_minutes": self.get_threshold_minutes(),
            "format": self.format_choice.get(),
            "bitrate": self.bitrate.get(),
            "mix_to_mono": self.mix_to_mono.get(),
            "recursive_scan": self.recursive_scan.get(),
            "delete_sources_after_export": self.delete_sources_after_export.get(),
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def locate_ffmpeg(self) -> str | None:
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return shutil.which("ffmpeg")

    def configure_styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        background = "#f4f6f8"
        panel = "#ffffff"
        text = "#18212f"
        muted = "#667085"
        border = "#d9dee7"
        accent = "#1769e0"

        self.root.configure(background=background)
        style.configure(".", font=("Helvetica Neue", 13), foreground=text)
        style.configure("TFrame", background=panel)
        style.configure("Header.TFrame", background="#eef4ff")
        style.configure("Panel.TFrame", background=panel, borderwidth=1, relief="solid")
        style.configure("Status.TFrame", background=background)
        style.configure("TLabel", background=panel, foreground=text)
        style.configure(
            "Title.TLabel",
            background="#eef4ff",
            foreground="#12213a",
            font=("Helvetica Neue", 22, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background="#eef4ff",
            foreground=muted,
            font=("Helvetica Neue", 12),
        )
        style.configure(
            "Summary.TLabel",
            background="#dce9ff",
            foreground="#174ea6",
            font=("Helvetica Neue", 12, "bold"),
            padding=(12, 7),
        )
        style.configure("Section.TLabel", background=panel, foreground=text, font=("Helvetica Neue", 15, "bold"))
        style.configure("Muted.TLabel", background=panel, foreground=muted, font=("Helvetica Neue", 12))
        style.configure("Status.TLabel", background=background, foreground=muted, font=("Helvetica Neue", 12))
        style.configure("TButton", padding=(12, 7), background="#f7f8fa", foreground=text, bordercolor=border)
        style.map("TButton", background=[("active", "#edf0f4")], bordercolor=[("focus", "#94b9f5")])
        style.configure("Accent.TButton", background="#e8f0fe", foreground="#174ea6", bordercolor="#a9c7f5")
        style.map("Accent.TButton", background=[("active", "#d7e6fd")])
        style.configure(
            "Primary.TButton",
            background=accent,
            foreground="#ffffff",
            bordercolor=accent,
            font=("Helvetica Neue", 13, "bold"),
            padding=(18, 10),
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#0f56bd"), ("disabled", "#aebed6")],
            foreground=[("disabled", "#eef2f7")],
        )
        style.configure("Danger.TButton", foreground="#b42318", background="#fff7f6", bordercolor="#efc6c2")
        style.map("Danger.TButton", background=[("active", "#fee9e7")])
        style.configure("TEntry", padding=(8, 7), fieldbackground="#ffffff", bordercolor=border)
        style.configure("Path.TEntry", padding=(9, 8))
        style.configure("TCombobox", padding=(7, 6), fieldbackground="#ffffff", bordercolor=border)
        style.configure("TCheckbutton", background=panel, foreground=text)
        style.configure("TRadiobutton", background=panel, foreground=text)
        style.configure(
            "Treeview",
            rowheight=32,
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground=text,
            bordercolor=border,
        )
        style.configure(
            "Treeview.Heading",
            background="#f6f7f9",
            foreground="#475467",
            font=("Helvetica Neue", 11, "bold"),
            padding=(7, 8),
            relief="flat",
        )
        style.map("Treeview", background=[("selected", "#dce9ff")], foreground=[("selected", "#123b73")])
        style.configure("TPanedwindow", background=background)
        style.configure("TSeparator", background=border)

    def build_ui(self) -> None:
        self.configure_styles()
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = ttk.Frame(self.root, style="Header.TFrame", padding=(24, 18))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="DJI Mic 录音整理", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="按录制时间自动整理分段录音，合并并压缩为更方便使用的音频文件",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Label(header, textvariable=self.library_summary, style="Summary.TLabel").grid(
            row=0, column=1, rowspan=2, sticky="e"
        )

        source = ttk.Frame(self.root, style="Panel.TFrame", padding=(18, 14))
        source.grid(row=1, column=0, sticky="ew", padx=18, pady=(16, 12))
        source.columnconfigure(1, weight=1)
        ttk.Label(source, text="1  导入录音", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 16))
        ttk.Entry(source, textvariable=self.selected_folder, style="Path.TEntry").grid(row=0, column=1, sticky="ew")
        ttk.Button(source, text="选择文件夹", command=self.choose_folder).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(source, text="扫描文件夹", command=self.scan_selected_folder, style="Accent.TButton").grid(
            row=0, column=3, padx=(8, 0)
        )
        ttk.Button(source, text="添加 WAV", command=self.add_files).grid(row=0, column=4, padx=(8, 0))
        self.eject_button = ttk.Button(
            source,
            text="弹出 DJI Mic",
            command=self.request_dji_mic_eject,
            style="Accent.TButton",
        )
        self.eject_button.grid(row=0, column=5, padx=(8, 0))

        source_options = ttk.Frame(source)
        source_options.grid(row=1, column=1, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Checkbutton(source_options, text="包含子文件夹", variable=self.recursive_scan).pack(side=tk.LEFT)
        ttk.Separator(source_options, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=14)
        ttk.Label(source_options, text="相邻录音间隔超过", style="Muted.TLabel").pack(side=tk.LEFT)
        threshold = ttk.Combobox(
            source_options,
            textvariable=self.threshold_minutes,
            values=["0.5", "1", "2", "5", "10"],
            width=5,
        )
        threshold.pack(side=tk.LEFT, padx=6)
        threshold.bind("<<ComboboxSelected>>", lambda _event: self.regroup_files())
        ttk.Label(source_options, text="分钟时分为新会话", style="Muted.TLabel").pack(side=tk.LEFT)

        body = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        body.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 12))
        left = ttk.Frame(body, style="Panel.TFrame", padding=16)
        right = ttk.Frame(body, style="Panel.TFrame", padding=16)
        body.add(left, weight=3)
        body.add(right, weight=2)

        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        group_toolbar = ttk.Frame(left)
        group_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        group_toolbar.columnconfigure(0, weight=1)
        ttk.Label(group_toolbar, text="2  整理会话", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(group_toolbar, text="自动分组", command=self.regroup_files).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(group_toolbar, text="合并选中", command=self.merge_selected_groups).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(
            group_toolbar,
            text="移到废纸篓",
            command=self.delete_selected_groups_from_disk,
            style="Danger.TButton",
        ).grid(row=0, column=3, padx=(8, 0))
        ttk.Label(
            left,
            text="系统已按时间自动分组；可多选会话后合并，也可在右侧从某个文件处拆分。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 12))

        group_columns = ("start", "files", "duration", "size")
        self.group_tree = ttk.Treeview(left, columns=group_columns, show="headings", selectmode="extended")
        self.group_tree.heading("start", text="录音会话")
        self.group_tree.heading("files", text="分段")
        self.group_tree.heading("duration", text="时长")
        self.group_tree.heading("size", text="原始体积")
        self.group_tree.column("start", width=190, minwidth=150, anchor=tk.W)
        self.group_tree.column("files", width=62, anchor=tk.CENTER, stretch=False)
        self.group_tree.column("duration", width=92, anchor=tk.CENTER, stretch=False)
        self.group_tree.column("size", width=100, anchor=tk.E, stretch=False)
        self.group_tree.grid(row=2, column=0, sticky="nsew")
        self.group_tree.bind("<<TreeviewSelect>>", self.on_group_select)
        group_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.group_tree.yview)
        group_scroll.grid(row=2, column=1, sticky="ns")
        self.group_tree.configure(yscrollcommand=group_scroll.set)

        file_toolbar = ttk.Frame(right)
        file_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(file_toolbar, text="会话详情", style="Section.TLabel").pack(side=tk.LEFT)
        ttk.Label(right, textvariable=self.selection_summary, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(5, 12)
        )

        file_columns = ("name", "duration", "size")
        self.file_tree = ttk.Treeview(right, columns=file_columns, show="headings", selectmode="extended")
        self.file_tree.heading("name", text="文件名")
        self.file_tree.heading("duration", text="时长")
        self.file_tree.heading("size", text="大小")
        self.file_tree.column("name", width=260, minwidth=170, anchor=tk.W)
        self.file_tree.column("duration", width=76, anchor=tk.CENTER, stretch=False)
        self.file_tree.column("size", width=88, anchor=tk.E, stretch=False)
        self.file_tree.grid(row=2, column=0, sticky="nsew")
        file_scroll = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.file_tree.yview)
        file_scroll.grid(row=2, column=1, sticky="ns")
        self.file_tree.configure(yscrollcommand=file_scroll.set)

        file_actions = ttk.Frame(right)
        file_actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(file_actions, text="从选中文件处拆分", command=self.split_group_at_file).pack(side=tk.LEFT)
        ttk.Button(file_actions, text="从列表移除", command=self.remove_selected_files).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            file_actions,
            text="移到废纸篓",
            command=self.delete_selected_files_from_disk,
            style="Danger.TButton",
        ).pack(side=tk.RIGHT)

        export_panel = ttk.Frame(self.root, style="Panel.TFrame", padding=(18, 14))
        export_panel.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 12))
        export_panel.columnconfigure(1, weight=1)
        ttk.Label(export_panel, text="3  导出", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 16))
        ttk.Entry(export_panel, textvariable=self.output_folder, style="Path.TEntry").grid(row=0, column=1, sticky="ew")
        ttk.Button(export_panel, text="选择目录", command=self.choose_output_folder).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(export_panel, text="打开目录", command=self.open_output_folder).grid(row=0, column=3, padx=(8, 18))

        export_options = ttk.Frame(export_panel)
        export_options.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(export_options, text="格式", style="Muted.TLabel").pack(side=tk.LEFT)
        self.format_combo = ttk.Combobox(
            export_options,
            textvariable=self.format_label,
            values=[preset["label"] for preset in FORMAT_PRESETS.values()],
            state="readonly",
            width=17,
        )
        self.format_combo.pack(side=tk.LEFT, padx=(6, 14))
        self.format_combo.bind("<<ComboboxSelected>>", self.on_format_label_change)
        self.bitrate_label = ttk.Label(export_options, text="码率", style="Muted.TLabel")
        self.bitrate_label.pack(side=tk.LEFT)
        self.bitrate_combo = ttk.Combobox(export_options, textvariable=self.bitrate, state="readonly", width=5)
        self.bitrate_combo.pack(side=tk.LEFT, padx=(6, 4))
        ttk.Label(export_options, text="kbps", style="Muted.TLabel").pack(side=tk.LEFT, padx=(0, 14))
        ttk.Checkbutton(export_options, text="转为单声道", variable=self.mix_to_mono).pack(side=tk.LEFT)
        ttk.Separator(export_options, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=14)
        ttk.Radiobutton(
            export_options,
            text="全部会话",
            variable=self.export_selected_only,
            value=False,
            command=self.update_button_states,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            export_options,
            text="仅选中会话",
            variable=self.export_selected_only,
            value=True,
            command=self.update_button_states,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(
            export_panel,
            text="导出成功后将源 WAV 移到废纸篓",
            variable=self.delete_sources_after_export,
        ).grid(row=2, column=1, columnspan=3, sticky="w", pady=(8, 0))

        self.export_button = ttk.Button(
            export_panel,
            textvariable=self.export_button_text,
            command=self.start_export,
            style="Primary.TButton",
        )
        self.export_button.grid(row=0, column=4, rowspan=3, sticky="nsew", ipadx=10)

        bottom = ttk.Frame(self.root, style="Status.TFrame", padding=(18, 8, 18, 12))
        bottom.grid(row=4, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status_text, style="Status.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(bottom, textvariable=self.progress_text, style="Status.TLabel", width=12, anchor=tk.E).grid(
            row=0, column=1, padx=(10, 0)
        )
        ttk.Progressbar(bottom, variable=self.progress_value, maximum=100).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(7, 0)
        )

        self.format_label.set(FORMAT_PRESETS[self.format_choice.get()]["label"])

    def choose_folder(self) -> None:
        initial = self.selected_folder.get() or str(Path.home())
        folder = filedialog.askdirectory(title="选择 DJI Mic 录音文件夹", initialdir=initial)
        if folder:
            self.selected_folder.set(folder)
            if not self.output_folder.get():
                self.output_folder.set(str(Path(folder) / "converted"))
            self.scan_selected_folder()

    def choose_output_folder(self) -> None:
        initial = self.output_folder.get() or self.selected_folder.get() or str(Path.home())
        folder = filedialog.askdirectory(title="选择输出目录", initialdir=initial)
        if folder:
            self.output_folder.set(folder)
            self.save_config()

    def open_output_folder(self) -> None:
        raw_folder = self.output_folder.get().strip()
        if not raw_folder:
            messagebox.showinfo("提示", "请先选择输出目录。")
            return
        folder = Path(raw_folder).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            elif sys.platform.startswith("win"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc))

    def disk_info(self, target: Path | str) -> dict | None:
        if sys.platform != "darwin":
            return None
        try:
            result = subprocess.run(
                ["diskutil", "info", "-plist", str(target)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=8,
                check=False,
            )
            if result.returncode != 0:
                return None
            parsed = plistlib.loads(result.stdout)
            return parsed if isinstance(parsed, dict) else None
        except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
            return None

    def volume_from_mount_point(self, mount_point: Path) -> DjiMicVolume | None:
        info = self.disk_info(mount_point)
        if not info or not disk_info_is_dji_mic(info, mount_point):
            return None
        identifier = str(info.get("ParentWholeDisk") or info.get("DeviceIdentifier") or "")
        if not re.fullmatch(r"disk\d+(?:s\d+)*", identifier):
            return None
        return DjiMicVolume(
            mount_point=mount_point,
            device_identifier=identifier,
            volume_name=str(info.get("VolumeName") or mount_point.name or "DJI Mic"),
            volume_uuid=str(info.get("VolumeUUID") or ""),
        )

    def mounted_volume_for_path(self, path: Path) -> Path | None:
        resolved = path.expanduser().resolve(strict=False)
        volumes_root = Path("/Volumes")
        try:
            relative = resolved.relative_to(volumes_root)
        except ValueError:
            return None
        return volumes_root / relative.parts[0] if relative.parts else None

    def find_dji_mic_volumes(self) -> list[DjiMicVolume]:
        mount_points: list[Path] = []
        source_paths = [item.path for item in self.audio_files]
        selected = self.selected_folder.get().strip()
        if selected:
            source_paths.insert(0, Path(selected))
        for path in source_paths:
            mount_point = self.mounted_volume_for_path(path)
            if mount_point and mount_point not in mount_points:
                mount_points.append(mount_point)

        volumes: list[DjiMicVolume] = []
        for mount_point in mount_points:
            volume = self.volume_from_mount_point(mount_point)
            if volume and volume.device_identifier not in {item.device_identifier for item in volumes}:
                volumes.append(volume)

        # If nothing in the current library is a DJI Mic, look for a connected
        # transmitter. Multiple matches are deliberately not guessed.
        if not volumes:
            try:
                mount_points = [path for path in Path("/Volumes").iterdir() if path.is_dir()]
            except OSError:
                mount_points = []
            for mount_point in mount_points:
                volume = self.volume_from_mount_point(mount_point)
                if volume and volume.device_identifier not in {item.device_identifier for item in volumes}:
                    volumes.append(volume)
        return volumes

    def request_dji_mic_eject(self) -> None:
        if sys.platform != "darwin":
            messagebox.showinfo("暂不支持", "一键弹出 DJI Mic 目前仅支持 macOS。")
            return
        if self.is_ejecting:
            return

        volumes = self.find_dji_mic_volumes()
        if not volumes:
            messagebox.showinfo(
                "未找到 DJI Mic",
                "没有找到可安全确认的 DJI Mic。请确认设备已连接，并先选择或扫描设备中的录音文件夹。",
            )
            return
        if len(volumes) > 1:
            messagebox.showwarning(
                "发现多个 DJI Mic",
                "当前有多个符合条件的设备。为避免弹错设备，请只保留一个设备连接后再试。",
            )
            return

        volume = volumes[0]
        export_uses_volume = self.is_exporting and any(
            path_is_within(path, volume.mount_point) for path in self.active_export_paths
        )
        if export_uses_volume:
            self.pending_eject = volume
            self.status_text.set("正在从 DJI Mic 导出；导出完成后将自动弹出。")
            messagebox.showinfo(
                "导出后弹出",
                "程序正在使用 DJI Mic 中的文件。导出完成后会自动弹出，并通知你何时可以安全断开。",
            )
            self.update_button_states()
            return

        self.begin_dji_mic_eject(volume)

    def begin_dji_mic_eject(self, volume: DjiMicVolume) -> None:
        self.pending_eject = None
        self.is_ejecting = True
        self.status_text.set(f"正在弹出 DJI Mic（{volume.volume_name}）...")
        self.update_button_states()
        threading.Thread(target=self.eject_worker, args=(volume,), daemon=True).start()

    def eject_worker(self, volume: DjiMicVolume) -> None:
        try:
            # The recording files may have been moved to Trash after export, so
            # re-check the captured disk identity rather than its file layout.
            info = self.disk_info(volume.mount_point)
            if not info:
                raise RuntimeError("设备已断开，或无法读取设备信息。")
            current_identifier = str(info.get("ParentWholeDisk") or info.get("DeviceIdentifier") or "")
            current_uuid = str(info.get("VolumeUUID") or "")
            still_external = bool(
                info.get("RemovableMediaOrExternalDevice")
                or info.get("RemovableMedia")
                or info.get("Removable")
                or info.get("Ejectable")
            ) and not bool(info.get("Internal") or info.get("OSInternalMedia"))
            if not still_external or current_identifier != volume.device_identifier:
                raise RuntimeError("设备标识已发生变化，为避免弹错设备，已取消操作。")
            if volume.volume_uuid and current_uuid != volume.volume_uuid:
                raise RuntimeError("设备卷标识已发生变化，为避免弹错设备，已取消操作。")

            result = subprocess.run(
                ["diskutil", "eject", volume.device_identifier],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise RuntimeError(detail or "macOS 未能弹出设备。")
            self.work_queue.put(("eject_done", volume.volume_name))
        except Exception as exc:
            self.work_queue.put(("eject_error", str(exc)))

    def add_files(self) -> None:
        initial = self.selected_folder.get() or str(Path.home())
        paths = filedialog.askopenfilenames(
            title="添加 WAV 文件",
            initialdir=initial,
            filetypes=[("WAV files", "*.wav *.WAV *.wave *.WAVE"), ("All files", "*.*")],
        )
        if not paths:
            return
        new_files = self.inspect_paths([Path(path) for path in paths])
        existing = {item.path for item in self.audio_files}
        self.audio_files.extend(item for item in new_files if item.path not in existing)
        self.audio_files.sort(key=lambda item: (item.start_time, item.path.name))
        self.regroup_files()

    def scan_selected_folder(self) -> None:
        folder = Path(self.selected_folder.get()).expanduser()
        if not folder.exists() or not folder.is_dir():
            messagebox.showerror("错误", "请选择一个有效的文件夹。")
            return

        pattern = "**/*" if self.recursive_scan.get() else "*"
        paths = [
            path
            for path in folder.glob(pattern)
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        self.status_text.set(f"正在读取 {len(paths)} 个 WAV 文件...")
        self.root.update_idletasks()

        self.audio_files = self.inspect_paths(paths)
        if not self.output_folder.get():
            self.output_folder.set(str(folder / "converted"))
        self.regroup_files()

    def inspect_paths(self, paths: list[Path]) -> list[AudioFile]:
        inspected: list[AudioFile] = []
        skipped = 0
        for path in sorted(paths, key=lambda item: item.name):
            try:
                stat = path.stat()
                duration = self.probe_duration(path)
                start_time = self.extract_start_time(path, stat.st_mtime)
                inspected.append(AudioFile(path=path, duration=duration, size=stat.st_size, start_time=start_time))
            except Exception:
                skipped += 1

        inspected.sort(key=lambda item: (item.start_time, item.path.name))
        if skipped:
            self.status_text.set(f"读取完成：{len(inspected)} 个文件可用，{skipped} 个文件被跳过。")
        return inspected

    def probe_duration(self, path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                if frame_rate > 0:
                    return wav_file.getnframes() / frame_rate
        except wave.Error:
            pass

        if not self.ffmpeg:
            raise RuntimeError("ffmpeg is not available")

        cmd = [
            self.ffmpeg,
            "-hide_banner",
            "-i",
            str(path),
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
        if not match:
            raise RuntimeError(f"无法读取音频时长：{path}")
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        return max(0.0, hours * 3600 + minutes * 60 + seconds)

    def extract_start_time(self, path: Path, fallback_timestamp: float) -> datetime:
        name = path.stem
        compact_match = re.search(r"(20\d{12})", name)
        if compact_match:
            try:
                return datetime.strptime(compact_match.group(1), "%Y%m%d%H%M%S")
            except ValueError:
                pass

        separated_match = re.search(
            r"(20\d{2})[-_. ]?(\d{2})[-_. ]?(\d{2})[-_ T]?(\d{2})[-_. ]?(\d{2})[-_. ]?(\d{2})",
            name,
        )
        if separated_match:
            try:
                return datetime(
                    int(separated_match.group(1)),
                    int(separated_match.group(2)),
                    int(separated_match.group(3)),
                    int(separated_match.group(4)),
                    int(separated_match.group(5)),
                    int(separated_match.group(6)),
                )
            except ValueError:
                pass

        return datetime.fromtimestamp(fallback_timestamp)

    def regroup_files(self) -> None:
        threshold_seconds = self.get_threshold_minutes() * 60
        groups: list[RecordingGroup] = []
        current = RecordingGroup()

        for audio_file in sorted(self.audio_files, key=lambda item: (item.start_time, item.path.name)):
            if not current.files:
                current.files.append(audio_file)
                continue

            previous = current.files[-1]
            gap = (audio_file.start_time - previous.end_time).total_seconds()
            if gap > threshold_seconds:
                groups.append(current)
                current = RecordingGroup(files=[audio_file])
            else:
                current.files.append(audio_file)

        if current.files:
            groups.append(current)

        self.groups = groups
        self.refresh_group_titles()
        self.refresh_group_tree()
        self.refresh_file_tree()
        self.save_config()
        if self.audio_files:
            self.status_text.set(f"已识别 {len(self.audio_files)} 个 WAV 文件，自动分成 {len(self.groups)} 个录音会话。")
        else:
            self.status_text.set("没有找到 WAV 文件。")
        self.update_button_states()

    def refresh_group_titles(self) -> None:
        for index, group in enumerate(self.groups, start=1):
            if group.start_time:
                group.title = f"{group.start_time:%Y-%m-%d_%H-%M-%S}_session-{index:02d}"
            else:
                group.title = f"session-{index:02d}"

    def refresh_group_tree(self) -> None:
        selected_indices = self.get_selected_group_indices()
        self.group_tree.delete(*self.group_tree.get_children())
        for index, group in enumerate(self.groups):
            self.group_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    self.format_datetime(group.start_time),
                    len(group.files),
                    self.format_duration(group.duration),
                    self.format_size(group.size),
                ),
                tags=("even" if index % 2 == 0 else "odd",),
            )

        self.group_tree.tag_configure("even", background="#ffffff")
        self.group_tree.tag_configure("odd", background="#fafbfc")

        for index in selected_indices:
            if 0 <= index < len(self.groups):
                self.group_tree.selection_add(str(index))
        if self.groups and not self.group_tree.selection():
            self.group_tree.selection_set("0")

        total_duration = sum(group.duration for group in self.groups)
        total_size = sum(group.size for group in self.groups)
        if self.audio_files:
            self.library_summary.set(
                f"{len(self.audio_files)} 个 WAV  ·  {len(self.groups)} 个会话  ·  "
                f"{self.format_duration(total_duration)}  ·  {self.format_size(total_size)}"
            )
        else:
            self.library_summary.set("尚未导入录音")

    def refresh_file_tree(self) -> None:
        self.file_tree.delete(*self.file_tree.get_children())
        group = self.get_primary_selected_group()
        if not group:
            self.selection_summary.set("选择左侧会话，查看其中的录音文件")
            return
        for index, audio_file in enumerate(group.files):
            self.file_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    audio_file.display_name,
                    self.format_duration(audio_file.duration),
                    self.format_size(audio_file.size),
                ),
                tags=("even" if index % 2 == 0 else "odd",),
            )
        self.file_tree.tag_configure("even", background="#ffffff")
        self.file_tree.tag_configure("odd", background="#fafbfc")
        self.selection_summary.set(
            f"{len(group.files)} 个分段  ·  {self.format_duration(group.duration)}  ·  {self.format_size(group.size)}"
        )

    def on_group_select(self, _event: tk.Event) -> None:
        self.refresh_file_tree()
        self.update_button_states()

    def merge_selected_groups(self) -> None:
        indices = sorted(self.get_selected_group_indices())
        if len(indices) < 2:
            messagebox.showinfo("提示", "请选择至少两个录音会话。")
            return

        merged_files: list[AudioFile] = []
        new_groups: list[RecordingGroup] = []
        for index, group in enumerate(self.groups):
            if index in indices:
                merged_files.extend(group.files)
                if index == indices[-1]:
                    new_groups.append(RecordingGroup(files=sorted(merged_files, key=lambda item: item.start_time)))
            else:
                new_groups.append(group)

        self.groups = new_groups
        self.refresh_group_titles()
        self.refresh_group_tree()
        self.refresh_file_tree()
        self.update_button_states()
        self.status_text.set("已合并选中的录音会话。")

    def split_group_at_file(self) -> None:
        group_index = self.get_primary_selected_group_index()
        if group_index is None:
            return
        file_indices = sorted(self.get_selected_file_indices())
        if not file_indices:
            messagebox.showinfo("提示", "请选择要作为新会话开头的文件。")
            return

        split_at = file_indices[0]
        group = self.groups[group_index]
        if split_at <= 0 or split_at >= len(group.files):
            messagebox.showinfo("提示", "请选择会话中间的文件来拆分。")
            return

        first = RecordingGroup(files=group.files[:split_at])
        second = RecordingGroup(files=group.files[split_at:])
        self.groups[group_index : group_index + 1] = [first, second]
        self.refresh_group_titles()
        self.refresh_group_tree()
        self.group_tree.selection_set(str(group_index + 1))
        self.refresh_file_tree()
        self.update_button_states()
        self.status_text.set("已拆分录音会话。")

    def remove_selected_files(self) -> None:
        group_index = self.get_primary_selected_group_index()
        if group_index is None:
            return
        file_indices = sorted(self.get_selected_file_indices(), reverse=True)
        if not file_indices:
            return

        group = self.groups[group_index]
        removed_paths = set()
        for index in file_indices:
            if 0 <= index < len(group.files):
                removed_paths.add(group.files[index].path)
                del group.files[index]

        self.audio_files = [item for item in self.audio_files if item.path not in removed_paths]
        if not group.files:
            del self.groups[group_index]

        self.refresh_group_titles()
        self.refresh_group_tree()
        self.refresh_file_tree()
        self.update_button_states()
        self.status_text.set("已移除选中的文件。")

    def delete_selected_files_from_disk(self) -> None:
        group_index = self.get_primary_selected_group_index()
        if group_index is None:
            return

        group = self.groups[group_index]
        file_indices = self.get_selected_file_indices()
        files = [group.files[index] for index in file_indices if 0 <= index < len(group.files)]
        self.delete_audio_files_from_disk(files)

    def delete_selected_groups_from_disk(self) -> None:
        indices = self.get_selected_group_indices()
        files: list[AudioFile] = []
        for index in indices:
            if 0 <= index < len(self.groups):
                files.extend(self.groups[index].files)
        self.delete_audio_files_from_disk(files)

    def delete_audio_files_from_disk(self, files: list[AudioFile]) -> None:
        if not files:
            return

        count = len(files)
        if not messagebox.askyesno(
            "确认删除源文件",
            f"将 {count} 个源 WAV 文件移到废纸篓/回收站。\n\n这个操作不会删除已经导出的文件。是否继续？",
        ):
            return

        paths = [audio_file.path for audio_file in files]
        try:
            self.move_paths_to_trash(paths)
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc))
            return

        self.remove_paths_from_state(set(paths))
        self.status_text.set(f"已将 {count} 个源 WAV 文件移到废纸篓/回收站。")

    def move_paths_to_trash(self, paths: list[Path]) -> None:
        existing_paths = [path for path in paths if path.exists()]
        if send2trash is None:
            raise RuntimeError("缺少 send2trash 依赖，请先运行 ./setup.sh。")

        for path in existing_paths:
            send2trash(str(path))

    def remove_paths_from_state(self, paths: set[Path]) -> None:
        self.audio_files = [item for item in self.audio_files if item.path not in paths]
        for group in self.groups:
            group.files = [item for item in group.files if item.path not in paths]
        self.groups = [group for group in self.groups if group.files]
        self.refresh_group_titles()
        self.refresh_group_tree()
        self.refresh_file_tree()
        self.update_button_states()

    def start_export(self) -> None:
        if self.is_exporting:
            return
        if not self.ffmpeg:
            messagebox.showerror("错误", "未找到 ffmpeg，请先安装 ffmpeg。")
            return

        output_folder = Path(self.output_folder.get()).expanduser()
        if not output_folder:
            messagebox.showerror("错误", "请选择输出目录。")
            return

        groups = self.get_groups_to_export()
        if not groups:
            messagebox.showerror("错误", "没有可导出的录音会话。")
            return

        delete_sources = self.delete_sources_after_export.get()
        self.save_config()
        self.is_exporting = True
        self.active_export_paths = [audio_file.path for group in groups for audio_file in group.files]
        self.active_export_paths.append(output_folder)
        self.progress_value.set(0)
        self.progress_text.set("准备导出...")
        self.status_text.set("正在导出，请稍等。")
        self.update_button_states()

        worker = threading.Thread(target=self.export_worker, args=(groups, output_folder, delete_sources), daemon=True)
        worker.start()

    def export_worker(self, groups: list[RecordingGroup], output_folder: Path, delete_sources: bool) -> None:
        try:
            output_folder.mkdir(parents=True, exist_ok=True)
            total_duration = max(1.0, sum(group.duration for group in groups))
            completed_duration = 0.0
            outputs: list[Path] = []
            source_paths = [audio_file.path for group in groups for audio_file in group.files]

            for group_index, group in enumerate(groups, start=1):
                output_path = self.unique_output_path(output_folder / self.output_name_for_group(group))
                self.work_queue.put(("status", f"正在导出 {group_index}/{len(groups)}：{output_path.name}"))
                self.export_group(group, output_path, completed_duration, total_duration)
                completed_duration += group.duration
                outputs.append(output_path)
                self.work_queue.put(("progress", min(100.0, completed_duration / total_duration * 100)))

            deleted_paths: list[Path] = []
            if delete_sources:
                self.move_paths_to_trash(source_paths)
                deleted_paths = source_paths

            self.work_queue.put(("done", {"outputs": outputs, "deleted_paths": deleted_paths}))
        except Exception as exc:
            self.work_queue.put(("error", str(exc)))
        finally:
            self.current_process = None

    def export_group(
        self,
        group: RecordingGroup,
        output_path: Path,
        completed_duration: float,
        total_duration: float,
    ) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as filelist:
            filelist_path = Path(filelist.name)
            for audio_file in group.files:
                filelist.write(f"file '{self.escape_concat_path(audio_file.path)}'\n")

        try:
            cmd = self.build_ffmpeg_command(filelist_path, output_path)
            self.current_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert self.current_process.stdout is not None
            output_lines: list[str] = []
            for line in self.current_process.stdout:
                output_lines.append(line)
                progress_seconds = self.parse_progress_seconds(line)
                if progress_seconds is not None:
                    overall = (completed_duration + min(progress_seconds, group.duration)) / total_duration * 100
                    self.work_queue.put(("progress", min(99.0, overall)))

            return_code = self.current_process.wait()
            if return_code != 0:
                raise RuntimeError("ffmpeg 导出失败：\n" + "".join(output_lines[-40:]))
        finally:
            try:
                filelist_path.unlink()
            except OSError:
                pass

    def build_ffmpeg_command(self, filelist_path: Path, output_path: Path) -> list[str]:
        output_format = self.format_choice.get()
        preset = FORMAT_PRESETS[output_format]
        cmd = [
            self.ffmpeg or "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(filelist_path),
            "-vn",
            *preset["codec_args"],
        ]

        if output_format in {"m4a", "mp3"}:
            cmd.extend(["-b:a", f"{self.bitrate.get()}k"])
            if self.mix_to_mono.get():
                cmd.extend(["-ac", "1"])
            cmd.extend(["-ar", "48000"])

        if output_format == "m4a":
            cmd.extend(["-movflags", "+faststart"])

        cmd.extend(["-progress", "pipe:1", "-nostats", str(output_path)])
        return cmd

    def drain_work_queue(self) -> None:
        try:
            while True:
                kind, payload = self.work_queue.get_nowait()
                if kind == "progress":
                    self.progress_value.set(float(payload))
                    self.progress_text.set(f"{float(payload):.1f}%")
                elif kind == "status":
                    self.status_text.set(str(payload))
                elif kind == "done":
                    result = payload if isinstance(payload, dict) else {}
                    outputs = result.get("outputs", [])
                    deleted_paths = result.get("deleted_paths", [])
                    if deleted_paths:
                        self.remove_paths_from_state(set(deleted_paths))
                    self.is_exporting = False
                    self.active_export_paths = []
                    self.progress_value.set(100)
                    self.progress_text.set("完成")
                    self.update_button_states()
                    suffix = f"，并移除了 {len(deleted_paths)} 个源 WAV" if deleted_paths else ""
                    self.status_text.set(f"导出完成：{len(outputs)} 个文件{suffix}。")
                    if self.pending_eject:
                        self.begin_dji_mic_eject(self.pending_eject)
                    else:
                        messagebox.showinfo("完成", f"已导出 {len(outputs)} 个文件{suffix}。")
                elif kind == "error":
                    self.is_exporting = False
                    self.active_export_paths = []
                    eject_was_pending = self.pending_eject is not None
                    self.pending_eject = None
                    self.progress_value.set(0)
                    self.progress_text.set("失败")
                    self.update_button_states()
                    self.status_text.set("导出失败；DJI Mic 未弹出。" if eject_was_pending else "导出失败。")
                    suffix = "\n\n由于导出未成功，DJI Mic 没有自动弹出。" if eject_was_pending else ""
                    messagebox.showerror("导出失败", f"{payload}{suffix}")
                elif kind == "eject_done":
                    self.is_ejecting = False
                    self.update_button_states()
                    self.status_text.set("DJI Mic 已弹出，可以安全断开设备。")
                    messagebox.showinfo("可以安全断开", f"DJI Mic（{payload}）已弹出，可以安全断开设备。")
                elif kind == "eject_error":
                    self.is_ejecting = False
                    self.update_button_states()
                    self.status_text.set("DJI Mic 弹出失败。")
                    messagebox.showerror(
                        "无法弹出 DJI Mic",
                        f"{payload}\n\n请关闭可能正在使用设备文件的其他程序后重试。",
                    )
        except queue.Empty:
            pass
        self.root.after(120, self.drain_work_queue)

    def update_format_controls(self) -> None:
        output_format = self.format_choice.get()
        preset = FORMAT_PRESETS[output_format]
        self.format_label.set(preset["label"])
        self.bitrate_combo.configure(values=preset["bitrates"])
        if preset["bitrates"]:
            if self.bitrate.get() not in preset["bitrates"]:
                self.bitrate.set(preset["default_bitrate"])
            self.bitrate_combo.configure(state="readonly")
            self.bitrate_label.configure(state="normal")
        else:
            self.bitrate.set("")
            self.bitrate_combo.configure(state="disabled")
            self.bitrate_label.configure(state="disabled")
        self.refresh_group_tree()

    def on_format_label_change(self, _event: tk.Event) -> None:
        label = self.format_label.get()
        for key, preset in FORMAT_PRESETS.items():
            if preset["label"] == label:
                self.format_choice.set(key)
                break
        self.update_format_controls()
        self.save_config()

    def normalize_format_key(self, value: object) -> str:
        text = str(value)
        if text in FORMAT_PRESETS:
            return text
        for key, preset in FORMAT_PRESETS.items():
            if text == preset["label"]:
                return key
        return "m4a"

    def update_button_states(self) -> None:
        has_files = bool(self.audio_files)
        has_groups = bool(self.groups)
        export_count = len(self.get_groups_to_export()) if has_groups else 0
        if self.is_exporting:
            self.export_button_text.set("正在导出…")
        elif self.export_selected_only.get():
            self.export_button_text.set(f"导出选中的 {export_count} 个会话")
        else:
            self.export_button_text.set(f"导出全部 {export_count} 个会话")
        self.export_button.configure(
            state=tk.DISABLED if self.is_exporting or not has_groups or export_count == 0 else tk.NORMAL
        )
        if self.pending_eject:
            self.eject_button.configure(text="导出后弹出", state=tk.DISABLED)
        elif self.is_ejecting:
            self.eject_button.configure(text="正在弹出…", state=tk.DISABLED)
        else:
            self.eject_button.configure(
                text="弹出 DJI Mic",
                state=tk.NORMAL if sys.platform == "darwin" else tk.DISABLED,
            )
        for widget in (self.group_tree, self.file_tree):
            widget.configure(selectmode="none" if self.is_exporting else "extended")
        if not has_files and not self.is_exporting:
            self.progress_text.set("")
            self.progress_value.set(0)

    def get_groups_to_export(self) -> list[RecordingGroup]:
        if self.export_selected_only.get():
            indices = self.get_selected_group_indices()
            return [self.groups[index] for index in indices if 0 <= index < len(self.groups)]
        return list(self.groups)

    def get_primary_selected_group(self) -> RecordingGroup | None:
        index = self.get_primary_selected_group_index()
        return self.groups[index] if index is not None else None

    def get_primary_selected_group_index(self) -> int | None:
        indices = self.get_selected_group_indices()
        return indices[0] if indices else None

    def get_selected_group_indices(self) -> list[int]:
        return sorted(int(item) for item in self.group_tree.selection() if item.isdigit())

    def get_selected_file_indices(self) -> list[int]:
        return sorted(int(item) for item in self.file_tree.selection() if item.isdigit())

    def get_threshold_minutes(self) -> float:
        try:
            return max(0.0, float(self.threshold_minutes.get()))
        except ValueError:
            return 2.0

    def output_name_for_group(self, group: RecordingGroup) -> str:
        extension = FORMAT_PRESETS[self.format_choice.get()]["extension"]
        return self.sanitize_filename(group.title) + extension

    def unique_output_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 2
        while True:
            candidate = parent / f"{stem}-{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def parse_progress_seconds(self, line: str) -> float | None:
        if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
            try:
                return int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                return None
        if line.startswith("out_time="):
            value = line.split("=", 1)[1].strip()
            try:
                hours, minutes, seconds = value.split(":")
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            except ValueError:
                return None
        return None

    def escape_concat_path(self, path: Path) -> str:
        return str(path).replace("'", "'\\''")

    def sanitize_filename(self, name: str) -> str:
        cleaned = re.sub(r"[\\/:*?\"<>|]+", "-", name)
        cleaned = re.sub(r"\s+", "_", cleaned).strip("._-")
        return cleaned or "recording"

    def format_datetime(self, value: datetime | None) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else "-"

    def format_duration(self, seconds: float) -> str:
        seconds = int(round(seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def format_size(self, size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024:
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} TB"

    def on_close(self) -> None:
        self.save_config()
        if self.current_process and self.current_process.poll() is None:
            self.current_process.terminate()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    app = WavMergerApp()
    app.run()
