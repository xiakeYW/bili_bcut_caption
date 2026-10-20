import os
import tkinter as tk
from dataclasses import dataclass
from enum import Enum, auto
from tkinter import filedialog, ttk
from typing import Callable

from .find import find_bcut_drafts
from .core import (
    backup_project,
    build_caption,
    export_captions,
    extract_template,
    find_latest_bjson,
    find_track_by_template,
    fmt_ms,
    get_caption_tracks,
    get_track_captions,
    inject_captions,
    list_template_texts,
    load_json,
    make_id_factory,
    new_caption_track,
    parse_subtitles,
    save_json,
)


class EventType(Enum):
    """事件类型枚举"""
    FOLDER_SELECTED = auto()  # 选择文件夹
    FILE_SELECTED = auto()    # 选择文件
    INPUT_CHANGED = auto()    # 输入框内容变化


def _set_placeholder(entry: tk.Entry, placeholder: str) -> None:
    """给 entry 添加占位提示文本，获得焦点时消失，失去焦点时恢复"""
    entry._placeholder = placeholder
    entry.insert(0, placeholder)
    entry.config(foreground="gray")

    def on_focus_in(e):
        if entry.get() == placeholder:
            entry.delete(0, tk.END)
            entry.config(foreground="black")

    def on_focus_out(e):
        if not entry.get():
            entry.insert(0, placeholder)
            entry.config(foreground="gray")

    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)


def _entry_value(entry: tk.Entry) -> str:
    """读取 entry 内容，跳过占位提示文本"""
    value = entry.get().strip()
    if value == getattr(entry, "_placeholder", ""):
        return ""
    return value


TRACK_NEW = -1
TRACK_UNSET = -2


@dataclass
class AppData:
    """UI 输入数据的载体"""
    drafts_dir: str = ""
    project_dir: str = ""
    project_name: str = ""
    style_text: str = ""
    subtitle_path: str = ""
    track_index: int = TRACK_UNSET


class BcutApp:
    """封装 UI 控件，并提供数据读写接口"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._callbacks: dict[EventType, list[Callable]] = {}
        self._projects: list[tuple[str, str]] = []
        self._track_map: list[int] = []
        self._build_ui()

    # ---------- 事件系统 ----------
    def on(self, event: EventType, callback: Callable) -> None:
        """注册事件回调"""
        self._callbacks.setdefault(event, []).append(callback)

    def _emit(self, event: EventType, data=None) -> None:
        """触发事件，调用所有已注册的回调"""
        for cb in self._callbacks.get(event, []):
            cb(data)

    def _select_folder(self, entry: tk.Entry) -> None:
        """选择文件夹并填入 entry，触发 FOLDER_SELECTED"""
        folder = filedialog.askdirectory(title="选择必剪数据文件夹")
        if folder:
            self.select_drafts_dir(folder)
            self._emit(EventType.FOLDER_SELECTED, folder)

    def _select_file(self, entry: tk.Entry) -> None:
        """选择文件并填入 entry，触发 FILE_SELECTED"""
        filepath = filedialog.askopenfilename(
            title="选择字幕文件",
            filetypes=[("字幕文件", "*.srt *.lrc *.ass *.ssa")]
        )
        if filepath:
            self.select_subtitle_file(filepath)
            self._emit(EventType.FILE_SELECTED, filepath)

    def _on_entry_change(self, field_name: str, entry: tk.Entry) -> None:
        """输入框变化回调，触发 INPUT_CHANGED"""
        value = _entry_value(entry)
        self._emit(EventType.INPUT_CHANGED, (field_name, value))

    def _bind_entry_events(self, entry: tk.Entry, field_name: str) -> None:
        """绑定输入框的键盘输入和失去焦点事件"""
        entry.bind(
            "<KeyRelease>",
            lambda e: self._on_entry_change(field_name, entry)
        )
        entry.bind(
            "<FocusOut>",
            lambda e: self._on_entry_change(field_name, entry)
        )

    # ---------- UI 构建 ----------
    def _build_ui(self) -> None:
        self.root.title("必剪字幕加载工具")
        self.root.geometry("560x460")

        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        content = tk.Frame(self.root, padx=20, pady=20)
        content.grid(row=0, column=0, sticky="nsew")

        content.grid_columnconfigure(0, weight=0)
        content.grid_columnconfigure(1, weight=1)
        content.grid_columnconfigure(2, weight=0)

        row = 0

        # Bcut Drafts: [选择必剪数据目录] [扫描目录] [选择文件夹]
        content.grid_rowconfigure(row, weight=0)
        tk.Label(content, text="Bcut Drafts:").grid(row=row, column=0, sticky="w", pady=6, padx=(0, 10))
        self.drafts_entry = tk.Entry(content)
        self.drafts_entry.grid(row=row, column=1, sticky="ew", pady=6)
        _set_placeholder(self.drafts_entry, "选择必剪数据目录")
        drafts_btns = tk.Frame(content)
        drafts_btns.grid(row=row, column=2, sticky="e", pady=6, padx=(10, 0))
        tk.Button(
            drafts_btns, text="扫描目录", width=10,
            command=self.on_scan
        ).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(
            drafts_btns, text="选择文件夹", width=10,
            command=lambda: self._select_folder(self.drafts_entry)
        ).pack(side=tk.LEFT)
        self._bind_entry_events(self.drafts_entry, "drafts_dir")
        row += 1

        # 选择项目: [选择必剪项目文件夹] [选择项目枚举]
        content.grid_rowconfigure(row, weight=0)
        tk.Label(content, text="选择项目:").grid(row=row, column=0, sticky="w", pady=6, padx=(0, 10))
        self.project_entry = tk.Entry(content)
        self.project_entry.grid(row=row, column=1, sticky="ew", pady=6)
        _set_placeholder(self.project_entry, "选择必剪项目文件夹")
        self.project_combo = ttk.Combobox(content, state="readonly", width=18)
        self.project_combo.grid(row=row, column=2, sticky="e", pady=6, padx=(10, 0))
        self.project_combo.bind("<<ComboboxSelected>>", self._on_project_selected)
        self._bind_entry_events(self.project_entry, "project_dir")
        row += 1

        # 样式模板: [输入样式模板文本]
        content.grid_rowconfigure(row, weight=0)
        tk.Label(content, text="样式模板:").grid(row=row, column=0, sticky="w", pady=6, padx=(0, 10))
        self.style_entry = tk.Entry(content)
        self.style_entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=6)
        _set_placeholder(self.style_entry, "样式模板字幕文本(留空取第一条)")
        self._bind_entry_events(self.style_entry, "style_text")
        row += 1

        # 轨道层级: [选择字幕写入的轨道]
        content.grid_rowconfigure(row, weight=0)
        tk.Label(content, text="轨道层级:").grid(row=row, column=0, sticky="w", pady=6, padx=(0, 10))
        self.track_combo = ttk.Combobox(content, state="readonly")
        self.track_combo.grid(row=row, column=1, columnspan=2, sticky="ew", pady=6)
        self.track_combo.bind("<<ComboboxSelected>>", self._on_track_selected)
        row += 1

        # 字幕文件: [选择字幕文件] [选择文件]
        content.grid_rowconfigure(row, weight=0)
        tk.Label(content, text="字幕文件:").grid(row=row, column=0, sticky="w", pady=6, padx=(0, 10))
        self.subtitle_entry = tk.Entry(content)
        self.subtitle_entry.grid(row=row, column=1, sticky="ew", pady=6)
        _set_placeholder(self.subtitle_entry, "选择字幕文件")
        self._bind_entry_events(self.subtitle_entry, "subtitle_path")
        tk.Button(
            content, text="选择文件",
            command=lambda: self._select_file(self.subtitle_entry)
        ).grid(row=row, column=2, sticky="e", pady=6, padx=(10, 0))
        row += 1

        # [日志显示]
        content.grid_rowconfigure(row, weight=1)
        log_frame = tk.Frame(content, bd=1, relief="sunken")
        log_frame.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=(10, 6))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        log_scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.config(yscrollcommand=log_scrollbar.set)
        row += 1

        # [备份按钮] [导出按钮] [加载按钮]
        content.grid_rowconfigure(row, weight=0)
        btn_frame = tk.Frame(content)
        btn_frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        tk.Button(btn_frame, text="备份", width=12, command=self.on_backup).pack(side=tk.LEFT)
        tk.Button(btn_frame, text="导出", width=12, command=self.on_export).pack(side=tk.LEFT, expand=True)
        tk.Button(btn_frame, text="加载", width=12, command=self.on_load).pack(side=tk.RIGHT)

    # ---------- 输入接口 ----------
    def get_data(self) -> AppData:
        """读取所有输入控件的当前值"""
        current = self.track_combo.current()
        if current < 0 or current >= len(self._track_map):
            track_index = TRACK_UNSET
        else:
            track_index = self._track_map[current]
        return AppData(
            drafts_dir=_entry_value(self.drafts_entry),
            project_dir=_entry_value(self.project_entry),
            project_name=self.project_combo.get().strip(),
            style_text=_entry_value(self.style_entry),
            subtitle_path=_entry_value(self.subtitle_entry),
            track_index=track_index,
        )

    def set_data(self, data: AppData) -> None:
        """把数据写回输入控件，触发 INPUT_CHANGED"""
        for entry, value, field_name in (
            (self.drafts_entry, data.drafts_dir, "drafts_dir"),
            (self.project_entry, data.project_dir, "project_dir"),
            (self.style_entry, data.style_text, "style_text"),
            (self.subtitle_entry, data.subtitle_path, "subtitle_path"),
        ):
            entry.delete(0, tk.END)
            if value:
                entry.insert(0, value)
                entry.config(foreground="black")
            else:
                _set_placeholder(entry, getattr(entry, "_placeholder", ""))
            self._emit(EventType.INPUT_CHANGED, (field_name, value))

        self.project_combo.set(data.project_name)

    def set_projects(self, names: list[str]) -> None:
        """填充项目枚举下拉框"""
        self.project_combo["values"] = list(names)
        if names:
            self.project_combo.set(names[0])

    # ---------- 输出接口 ----------
    def append_log(self, text: str) -> None:
        """追加一行日志到日志显示区"""
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def clear_log(self) -> None:
        """清空日志显示区"""
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")

    # ---------- 选择处理 ----------
    def select_drafts_dir(self, path: str) -> None:
        """写入必剪数据目录并加载其中的项目列表"""
        self.drafts_entry.delete(0, tk.END)
        self.drafts_entry.insert(0, path)
        self.drafts_entry.config(foreground="black")
        self._emit(EventType.INPUT_CHANGED, ("drafts_dir", path))
        self._load_projects(path)

    def select_subtitle_file(self, path: str) -> None:
        """写入字幕文件路径并立即解析校验"""
        self.subtitle_entry.delete(0, tk.END)
        self.subtitle_entry.insert(0, path)
        self.subtitle_entry.config(foreground="black")
        self._emit(EventType.INPUT_CHANGED, ("subtitle_path", path))
        self.append_log(f"[字幕] {path}")
        try:
            captions = parse_subtitles(path)
        except Exception as e:
            self.append_log(f"[校验失败] 字幕解析失败: {e}")
            return
        if not captions:
            self.append_log("[校验失败] 字幕文件中没有有效字幕")
            return
        self.append_log(
            f"[字幕] 共 {len(captions)} 条，"
            f"{fmt_ms(captions[0].start_ms)} ~ {fmt_ms(captions[-1].end_ms)}"
        )

    def _load_projects(self, drafts_dir: str) -> None:
        """读取 draftInfo.json，填充项目下拉框"""
        self._projects = []
        self.set_projects([])
        if not os.path.isdir(drafts_dir):
            self.append_log(f"[校验失败] 必剪数据目录不存在: {drafts_dir}")
            return
        info_path = os.path.join(drafts_dir, "draftInfo.json")
        if not os.path.isfile(info_path):
            self.append_log(f"[校验失败] 目录中没有 draftInfo.json: {drafts_dir}")
            return
        try:
            info = load_json(info_path)
        except Exception as e:
            self.append_log(f"[校验失败] draftInfo.json 解析失败: {e}")
            return
        drafts = info.get("draftInfos", [])
        self._projects = [
            (d.get("name", ""), d["id"])
            for d in drafts
            if d.get("id")
        ]
        self.append_log(f"[目录] {drafts_dir}")
        self.append_log(
            "[目录] 共 {} 个项目: {}".format(
                len(self._projects),
                "、".join(name for name, _ in self._projects) or "无",
            )
        )
        if self._projects:
            self.set_projects([name for name, _ in self._projects])
            self._apply_project(self._projects[0])

    def _on_project_selected(self, event=None) -> None:
        """下拉框选择项目"""
        name = self.project_combo.get().strip()
        for project in self._projects:
            if project[0] == name:
                self._apply_project(project)
                return
        self.append_log(f"[校验失败] 未找到项目: {name}")

    def _on_track_selected(self, event=None) -> None:
        """下拉框选择轨道层级"""
        current = self.track_combo.current()
        if current < 0 or current >= len(self._track_map):
            return
        track = self._track_map[current]
        if track == TRACK_NEW:
            self.append_log("[轨道] 将新建一条轨道（显示在最上层）")
        else:
            self.append_log(f"[轨道] 字幕将写入第 {track + 1} 轨（数值越小越靠底层）")

    def _refresh_tracks(self, project_dir: str) -> None:
        """读取项目 bjson，填充轨道层级下拉框"""
        self._track_map = []
        self.track_combo["values"] = []
        self.track_combo.set("")
        latest = find_latest_bjson(project_dir)
        if not latest:
            return
        try:
            data = load_json(latest)
        except Exception as e:
            self.append_log(f"[校验失败] bjson 读取失败，无法列出轨道: {e}")
            return
        tracks = get_caption_tracks(data)
        values = []
        for i, track in enumerate(tracks):
            captions = track.get("captions", [])
            first = (captions[0].get("captionText", "") if captions else "").strip()
            label = f"{i + 1}. {first[:10] or '空轨道'} ({len(captions)}条)"
            values.append(label)
            self._track_map.append(i)
        values.append(f"{len(tracks) + 1}. 新建轨道")
        self._track_map.append(TRACK_NEW)
        self.track_combo["values"] = values
        if tracks:
            self.track_combo.current(0)
            self.append_log(
                "[轨道] 共 {} 轨: {}".format(
                    len(tracks),
                    " / ".join(v.rsplit(" (", 1)[0] for v in values[:-1]),
                )
            )
        else:
            self.track_combo.current(len(values) - 1)
            self.append_log("[轨道] 项目中没有字幕轨，加载时将新建轨道")

    def _apply_project(self, project: tuple[str, str]) -> None:
        """把选中的项目写入项目文件夹输入框并校验"""
        name, project_id = project
        drafts_dir = _entry_value(self.drafts_entry)
        path = os.path.join(drafts_dir, project_id)
        self.project_entry.delete(0, tk.END)
        self.project_entry.insert(0, path)
        self.project_entry.config(foreground="black")
        self._emit(EventType.INPUT_CHANGED, ("project_dir", path))
        if not os.path.isdir(path):
            self.append_log(f"[校验失败] 项目文件夹不存在: {path}")
            return
        bjson_count = len([
            f for f in os.listdir(path) if f.lower().endswith(".bjson")
        ])
        self.append_log(f"[项目] {name} -> {path}（{bjson_count} 个 bjson）")
        latest = find_latest_bjson(path)
        if latest:
            self.append_log(f"[项目] 最新工程文件: {os.path.basename(latest)}")
        else:
            self.append_log(f"[校验失败] 项目文件夹中没有 bjson 文件: {path}")
            return
        self._refresh_tracks(path)

    # ---------- 按钮接口 ----------
    def on_scan(self) -> None:
        """扫描系统，自动查找必剪数据目录"""
        self.append_log("[扫描] 开始搜索 Bcut Drafts ...")
        path = find_bcut_drafts()
        if not path:
            self.append_log("[扫描] 未找到 Bcut Drafts 文件夹")
            return
        self.append_log(f"[扫描] 找到 {path}")
        self.select_drafts_dir(path)

    def on_backup(self) -> None:
        """备份当前项目的所有 bjson 文件"""
        data = self.get_data()
        project_dir = data.project_dir or self._project_path_by_name(data.project_name)
        if not project_dir or not os.path.isdir(project_dir):
            self.append_log("[备份失败] 项目文件夹不存在，请先选择必剪数据目录并选定项目")
            return
        dest = backup_project(project_dir)
        if not dest:
            self.append_log(f"[备份失败] 未找到可备份的 bjson 文件: {project_dir}")
            return
        self.append_log(f"[备份] {project_dir}")
        self.append_log(f"[备份] 已保存到 {dest}")

    def on_export(self) -> None:
        """校验项目后弹出导出对话框"""
        data = self.get_data()
        project_dir = data.project_dir or self._project_path_by_name(data.project_name)
        if not project_dir or not os.path.isdir(project_dir):
            self.append_log("[导出失败] 项目文件夹不存在，请先选择必剪数据目录并选定项目")
            return
        bjson_path = find_latest_bjson(project_dir)
        if not bjson_path:
            self.append_log(f"[导出失败] 项目文件夹中没有 bjson 文件: {project_dir}")
            return
        try:
            bjson_data = load_json(bjson_path)
        except Exception as e:
            self.append_log(f"[导出失败] bjson 读取失败: {e}")
            return
        tracks = get_caption_tracks(bjson_data)
        if not tracks:
            self.append_log("[导出失败] 项目中没有字幕轨")
            return
        self._show_export_dialog(bjson_data, tracks, bjson_path)

    def _show_export_dialog(self, bjson_data: dict, tracks: list[dict], bjson_path: str) -> None:
        """弹出导出设置对话框"""
        dlg = tk.Toplevel(self.root)
        dlg.title("导出字幕")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        frame = tk.Frame(dlg, padx=16, pady=16)
        frame.pack(fill="both", expand=True)

        # 导出方式
        mode_frame = tk.LabelFrame(frame, text="导出方式", padx=8, pady=6)
        mode_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        mode_var = tk.StringVar(value="track")
        tk.Radiobutton(mode_frame, text="按轨道导出", variable=mode_var, value="track").pack(anchor="w")
        tk.Radiobutton(mode_frame, text="按模板文本导出", variable=mode_var, value="template").pack(anchor="w")

        # 轨道选择
        tk.Label(frame, text="轨道:").grid(row=1, column=0, sticky="w", pady=4)
        track_combo = ttk.Combobox(frame, state="readonly", width=30)
        track_combo.grid(row=1, column=1, sticky="ew", pady=4, padx=(8, 0))
        track_labels = []
        for i, track in enumerate(tracks):
            captions = track.get("captions", [])
            first = (captions[0].get("captionText", "") if captions else "").strip()
            track_labels.append(f"{i + 1}. {first[:10] or '空轨道'} ({len(captions)}条)")
        track_combo["values"] = track_labels
        if track_labels:
            track_combo.current(0)

        # 模板文本
        tk.Label(frame, text="模板文本:").grid(row=2, column=0, sticky="w", pady=4)
        template_combo = ttk.Combobox(frame, width=30)
        template_combo.grid(row=2, column=1, sticky="ew", pady=4, padx=(8, 0))
        template_texts = list_template_texts(bjson_data)
        template_combo["values"] = template_texts
        if template_texts:
            template_combo.set(template_texts[0])

        def on_mode_change():
            mode = mode_var.get()
            track_combo.config(state="readonly" if mode == "track" else "disabled")
            template_combo.config(state="normal" if mode == "template" else "disabled")

        for rb in mode_frame.winfo_children():
            rb.config(command=on_mode_change)
        on_mode_change()

        # 导出格式
        fmt_frame = tk.LabelFrame(frame, text="导出格式", padx=8, pady=6)
        fmt_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        fmt_var = tk.StringVar(value="srt")
        fmts = [("SRT", "srt"), ("LRC", "lrc"), ("ASS", "ass"), ("SSA", "ssa")]
        for i, (label, value) in enumerate(fmts):
            tk.Radiobutton(fmt_frame, text=label, variable=fmt_var, value=value).grid(row=0, column=i, padx=4)

        def on_confirm():
            mode = mode_var.get()
            fmt = fmt_var.get()
            if mode == "track":
                idx = track_combo.current()
                if idx < 0:
                    self.append_log("[导出失败] 未选择轨道")
                    dlg.destroy()
                    return
                captions = get_track_captions(bjson_data, idx)
                desc = f"第 {idx + 1} 轨"
            else:
                template_text = template_combo.get().strip()
                if not template_text:
                    self.append_log("[导出失败] 未输入模板文本")
                    dlg.destroy()
                    return
                idx = find_track_by_template(bjson_data, template_text)
                if idx is None:
                    self.append_log(f"[导出失败] 未找到模板「{template_text}」所在的轨道")
                    dlg.destroy()
                    return
                captions = get_track_captions(bjson_data, idx)
                desc = f"模板「{template_text}」所在第 {idx + 1} 轨"

            if not captions:
                self.append_log(f"[导出失败] {desc} 没有可导出的字幕")
                dlg.destroy()
                return

            default_ext = {"srt": ".srt", "lrc": ".lrc", "ass": ".ass", "ssa": ".ssa"}[fmt]
            save_path = filedialog.asksaveasfilename(
                title="保存字幕文件",
                defaultextension=default_ext,
                filetypes=[
                    ("SRT 字幕", "*.srt"),
                    ("LRC 歌词", "*.lrc"),
                    ("ASS 字幕", "*.ass"),
                    ("SSA 字幕", "*.ssa"),
                    ("所有文件", "*.*"),
                ],
            )
            if not save_path:
                dlg.destroy()
                return

            try:
                export_captions(captions, save_path, fmt)
            except Exception as e:
                self.append_log(f"[导出失败] 写入失败: {e}")
                dlg.destroy()
                return

            self.append_log(f"[导出] {desc}，共 {len(captions)} 条 -> {save_path}")
            dlg.destroy()

        btn_frame = tk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(12, 0))
        tk.Button(btn_frame, text="导出", width=10, command=on_confirm).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(btn_frame, text="取消", width=10, command=dlg.destroy).pack(side=tk.LEFT)

        dlg.wait_window()

    def on_load(self) -> None:
        """校验全部输入后，把字幕写入项目 bjson"""
        self.append_log("[加载] 开始")
        data = self.get_data()

        project_dir = data.project_dir or self._project_path_by_name(data.project_name)
        if not project_dir or not os.path.isdir(project_dir):
            self.append_log("[校验失败] 项目文件夹不存在，请先选择必剪数据目录并选定项目")
            return
        bjson_path = find_latest_bjson(project_dir)
        if not bjson_path:
            self.append_log(f"[校验失败] 项目文件夹中没有 bjson 文件: {project_dir}")
            return
        self.append_log(f"[校验] 项目文件夹 {project_dir}")
        self.append_log(f"[校验] 目标文件 {os.path.basename(bjson_path)}")

        if not data.subtitle_path or not os.path.isfile(data.subtitle_path):
            self.append_log("[校验失败] 字幕文件不存在，请先选择字幕文件")
            return
        try:
            captions = parse_subtitles(data.subtitle_path)
        except Exception as e:
            self.append_log(f"[校验失败] 字幕解析失败: {e}")
            return
        if not captions:
            self.append_log("[校验失败] 字幕文件中没有有效字幕")
            return
        self.append_log(
            f"[校验] 字幕 {len(captions)} 条，"
            f"{fmt_ms(captions[0].start_ms)} ~ {fmt_ms(captions[-1].end_ms)}"
        )

        try:
            bjson_data = load_json(bjson_path)
        except Exception as e:
            self.append_log(f"[校验失败] bjson 读取失败: {e}")
            return
        template = extract_template(bjson_data, data.style_text)
        if template is None:
            texts = list_template_texts(bjson_data)
            detail = (
                "、".join(texts)
                if texts
                else "项目中没有任何字幕，请先在必剪里添加一条字幕作为样式模板"
            )
            self.append_log(f"[校验失败] 未找到样式模板「{data.style_text}」，可用模板: {detail}")
            return
        track_index, template_cap = template
        self.append_log(
            f"[校验] 样式模板「{template_cap.get('captionText', '')}」(第 {track_index + 1} 轨)"
        )

        tracks = get_caption_tracks(bjson_data)
        choice = data.track_index
        if choice == TRACK_UNSET:
            target_index = track_index
            self.append_log(f"[校验] 轨道未选择，默认写入模板所在轨道 {target_index + 1}")
        elif choice == TRACK_NEW:
            target_index = None
            self.append_log("[校验] 将新建轨道（将显示在最上层）")
        elif 0 <= choice < len(tracks):
            target_index = choice
            self.append_log(f"[校验] 写入第 {target_index + 1} 轨")
        else:
            self.append_log(f"[校验失败] 轨道选择已失效，请重新选择（当前共 {len(tracks)} 轨）")
            return

        backup_dir = backup_project(project_dir)
        if not backup_dir:
            self.append_log("[校验失败] 备份失败，已中止加载")
            return
        self.append_log(f"[备份] {backup_dir}")

        new_id = make_id_factory()
        items = [build_caption(template_cap, caption, new_id) for caption in captions]
        if target_index is None:
            target_index = new_caption_track(bjson_data, new_id)
            self.append_log(f"[加载] 已新建轨道 {target_index + 1}")
        inject_captions(bjson_data, target_index, items)
        try:
            save_json(bjson_data, bjson_path)
            check = load_json(bjson_path)
            written = len(get_caption_tracks(check)[target_index]["captions"])
        except Exception as e:
            self.append_log(f"[校验失败] 写入失败: {e}")
            return
        if written != len(items):
            self.append_log(f"[校验失败] 写入条数不符: 期望 {len(items)}，实际 {written}")
            return
        self.append_log(
            f"[加载] 完成，第 {target_index + 1} 轨写入 {written} 条字幕 -> {os.path.basename(bjson_path)}"
        )
        self.append_log("[提示] 请重新打开或刷新必剪工程以查看效果")
        self._refresh_tracks(project_dir)

    def _project_path_by_name(self, name: str) -> str | None:
        """按项目名推导项目文件夹路径"""
        drafts_dir = _entry_value(self.drafts_entry)
        for project_name, project_id in self._projects:
            if project_name == name and drafts_dir:
                return os.path.join(drafts_dir, project_id)
        return None


def main() -> None:
    root = tk.Tk()
    BcutApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
