"""
DJI Mic recording organizer.

This app scans WAV files, groups adjacent DJI Mic chunks into recording
sessions, and exports each session as a compact audio file.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
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


class WavMergerApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1180x760")
        self.root.minsize(980, 640)

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
        self.progress_text = tk.StringVar(value="")
        self.progress_value = tk.DoubleVar(value=0)
        self.work_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.is_exporting = False
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

    def build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = ttk.Frame(self.root, padding=(12, 12, 12, 8))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Button(top, text="选择文件夹", command=self.choose_folder).grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(top, textvariable=self.selected_folder).grid(row=0, column=1, sticky="ew")
        ttk.Button(top, text="扫描", command=self.scan_selected_folder).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(top, text="添加文件", command=self.add_files).grid(row=0, column=3, padx=(8, 0))

        ttk.Checkbutton(top, text="包含子文件夹", variable=self.recursive_scan).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(top, text="分组间隔").grid(row=1, column=1, sticky="e", pady=(8, 0), padx=(0, 8))
        threshold = ttk.Combobox(top, textvariable=self.threshold_minutes, values=["0.5", "1", "2", "5", "10"], width=8)
        threshold.grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Label(top, text="分钟").grid(row=1, column=3, sticky="w", pady=(8, 0), padx=(6, 0))

        body = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))

        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=3)
        body.add(right, weight=2)

        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        group_toolbar = ttk.Frame(left)
        group_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(group_toolbar, text="录音会话").pack(side=tk.LEFT)
        ttk.Button(group_toolbar, text="删除选中会话源文件", command=self.delete_selected_groups_from_disk).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(group_toolbar, text="重新分组", command=self.regroup_files).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(group_toolbar, text="合并选中组", command=self.merge_selected_groups).pack(side=tk.RIGHT, padx=(6, 0))

        group_columns = ("index", "start", "files", "duration", "size", "output")
        self.group_tree = ttk.Treeview(left, columns=group_columns, show="headings", selectmode="extended")
        self.group_tree.heading("index", text="#")
        self.group_tree.heading("start", text="开始时间")
        self.group_tree.heading("files", text="文件数")
        self.group_tree.heading("duration", text="时长")
        self.group_tree.heading("size", text="原始大小")
        self.group_tree.heading("output", text="输出文件名")
        self.group_tree.column("index", width=48, anchor=tk.CENTER, stretch=False)
        self.group_tree.column("start", width=150, anchor=tk.W, stretch=False)
        self.group_tree.column("files", width=72, anchor=tk.CENTER, stretch=False)
        self.group_tree.column("duration", width=92, anchor=tk.CENTER, stretch=False)
        self.group_tree.column("size", width=96, anchor=tk.E, stretch=False)
        self.group_tree.column("output", width=260, anchor=tk.W)
        self.group_tree.grid(row=1, column=0, sticky="nsew")
        self.group_tree.bind("<<TreeviewSelect>>", self.on_group_select)

        group_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.group_tree.yview)
        group_scroll.grid(row=1, column=1, sticky="ns")
        self.group_tree.configure(yscrollcommand=group_scroll.set)

        export_panel = ttk.LabelFrame(left, text="导出设置", padding=10)
        export_panel.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        export_panel.columnconfigure(1, weight=1)

        ttk.Label(export_panel, text="输出目录").grid(row=0, column=0, sticky="w")
        ttk.Entry(export_panel, textvariable=self.output_folder).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(export_panel, text="选择", command=self.choose_output_folder).grid(row=0, column=2)

        ttk.Label(export_panel, text="格式").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.format_combo = ttk.Combobox(
            export_panel,
            textvariable=self.format_label,
            values=[preset["label"] for preset in FORMAT_PRESETS.values()],
            state="readonly",
            width=18,
        )
        self.format_combo.grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
        self.format_combo.bind("<<ComboboxSelected>>", self.on_format_label_change)

        self.bitrate_label = ttk.Label(export_panel, text="码率")
        self.bitrate_label.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.bitrate_combo = ttk.Combobox(export_panel, textvariable=self.bitrate, state="readonly", width=8)
        self.bitrate_combo.grid(row=2, column=1, sticky="w", padx=8, pady=(8, 0))
        ttk.Checkbutton(export_panel, text="转单声道", variable=self.mix_to_mono).grid(
            row=2, column=1, sticky="w", padx=(100, 0), pady=(8, 0)
        )

        ttk.Checkbutton(export_panel, text="只导出选中会话", variable=self.export_selected_only).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Checkbutton(export_panel, text="导出成功后将源 WAV 移到废纸篓", variable=self.delete_sources_after_export).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )
        self.export_button = ttk.Button(export_panel, text="开始批量导出", command=self.start_export)
        self.export_button.grid(row=3, column=2, rowspan=2, sticky="e", pady=(8, 0))

        file_toolbar = ttk.Frame(right)
        file_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(file_toolbar, text="会话内文件").pack(side=tk.LEFT)
        ttk.Button(file_toolbar, text="删除源文件", command=self.delete_selected_files_from_disk).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(file_toolbar, text="移除文件", command=self.remove_selected_files).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(file_toolbar, text="从此拆分", command=self.split_group_at_file).pack(side=tk.RIGHT, padx=(6, 0))

        file_columns = ("name", "start", "duration", "size")
        self.file_tree = ttk.Treeview(right, columns=file_columns, show="headings", selectmode="extended")
        self.file_tree.heading("name", text="文件名")
        self.file_tree.heading("start", text="开始时间")
        self.file_tree.heading("duration", text="时长")
        self.file_tree.heading("size", text="大小")
        self.file_tree.column("name", width=250, anchor=tk.W)
        self.file_tree.column("start", width=142, anchor=tk.W, stretch=False)
        self.file_tree.column("duration", width=82, anchor=tk.CENTER, stretch=False)
        self.file_tree.column("size", width=86, anchor=tk.E, stretch=False)
        self.file_tree.grid(row=1, column=0, sticky="nsew")

        file_scroll = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.file_tree.yview)
        file_scroll.grid(row=1, column=1, sticky="ns")
        self.file_tree.configure(yscrollcommand=file_scroll.set)

        info = ttk.Frame(right)
        info.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        info.columnconfigure(0, weight=1)
        ttk.Label(info, textvariable=self.status_text, wraplength=430).grid(row=0, column=0, sticky="ew")

        bottom = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        ttk.Progressbar(bottom, variable=self.progress_value, maximum=100).grid(row=0, column=0, sticky="ew")
        ttk.Label(bottom, textvariable=self.progress_text, width=28).grid(row=0, column=1, padx=(10, 0))

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
                    index + 1,
                    self.format_datetime(group.start_time),
                    len(group.files),
                    self.format_duration(group.duration),
                    self.format_size(group.size),
                    self.output_name_for_group(group),
                ),
            )

        for index in selected_indices:
            if 0 <= index < len(self.groups):
                self.group_tree.selection_add(str(index))

    def refresh_file_tree(self) -> None:
        self.file_tree.delete(*self.file_tree.get_children())
        group = self.get_primary_selected_group()
        if not group:
            return
        for index, audio_file in enumerate(group.files):
            self.file_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    audio_file.display_name,
                    self.format_datetime(audio_file.start_time),
                    self.format_duration(audio_file.duration),
                    self.format_size(audio_file.size),
                ),
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
                    self.progress_value.set(100)
                    self.progress_text.set("完成")
                    self.update_button_states()
                    suffix = f"，并移除了 {len(deleted_paths)} 个源 WAV" if deleted_paths else ""
                    self.status_text.set(f"导出完成：{len(outputs)} 个文件{suffix}。")
                    messagebox.showinfo("完成", f"已导出 {len(outputs)} 个文件{suffix}。")
                elif kind == "error":
                    self.is_exporting = False
                    self.progress_value.set(0)
                    self.progress_text.set("失败")
                    self.update_button_states()
                    self.status_text.set("导出失败。")
                    messagebox.showerror("导出失败", str(payload))
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
        self.export_button.configure(state=tk.DISABLED if self.is_exporting or not has_groups else tk.NORMAL)
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
