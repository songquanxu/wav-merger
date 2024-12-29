"""
WAV音频文件合并工具 (WAV Audio Merger)
版本：1.0.0
创建日期：2023.12.29

版权声明：
本程序在 AI 辅助下开发完成（基于 Cursor AI）。
作者保留版权，但允许在以下条件下自由使用：
1. 允许任何人以非盈利方式下载、使用、分发和修改本程序
2. 必须保留此版权声明
3. 修改后的版本必须明确声明已经过修改
4. 不得用于商业目的

使用的开源组件：
- Python 3.13+ (https://www.python.org/)
- tkinter - Python 标准 GUI 库
- pygame 2.6.1+ (https://www.pygame.org/) - 用于音频播放
- mutagen (https://mutagen.readthedocs.io/) - 用于音频元数据处理
- FFmpeg (https://ffmpeg.org/) - 用于音频处理和合并

依赖说明：
1. 系统需求：
   - 操作系统：Windows/macOS/Linux
   - Python 3.13 或更高版本
   - FFmpeg

2. Python 包依赖：
   - pygame>=2.6.1
   - mutagen
   
项目主页：https://github.com/songquanxu/wav-merger
问题反馈：https://github.com/songquanxu/wav-merger/issues

更新历史：
- 1.0.0 (2023.12.29): 首次发布
  * 支持 WAV 文件合并
  * 支持音频预览
  * 支持导出为 MP3
  * 文件属性查看
"""

import json
import os
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import glob
import time
import pygame
from mutagen.wave import WAVE  # 用于获取音频时长
import tempfile

class WavMerger:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("WAV文件合并工具")
        self.window.geometry("1000x700")
        
        # 加载配置
        self.config_file = os.path.join(os.path.expanduser("~"), ".wav_merger_config.json")
        self.load_config()
        
        # 初始化pygame音频系统
        pygame.mixer.init()
        
        # 音频播放状态
        self.is_playing = False
        self.current_audio_length = 0
        self.current_audio_path = None
        self.is_dragging = False  # 添加拖动状态标记
        
        # 添加播放位置跟踪
        self.current_position = 0
        self.start_offset = 0
        
        # 创建界面元素
        self.create_widgets()
        
        # 初始化按钮状态
        self.update_button_states()
        
        # 绑定窗口关闭事件
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # 创建定时器用于更新进度条
        self.update_progress()
        
        # 显示初始使用说明
        self.show_usage_guide()
        
    def load_config(self):
        """加载配置文件"""
        self.config = {}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    self.config = json.load(f)
            except:
                pass

    def save_config(self):
        """保存配置文件"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f)
        except:
            pass

    def create_widgets(self):
        # 主框架
        main_frame = ttk.Frame(self.window)
        main_frame.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)
        
        # 创建左右分隔的面板
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        
        # 左侧面板
        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=3)  # 左侧占比更大
        
        # 右侧属性面板
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)  # 右侧占比更小
        
        # === 左侧面板内容 ===
        # 顶部按钮框架
        top_button_frame = ttk.Frame(left_frame)
        top_button_frame.pack(fill=tk.X, pady=5)
        
        # 移除选择文件夹按钮，只保留添加文件按钮
        self.add_btn = ttk.Button(top_button_frame, text="添加WAV文件", command=self.add_files)
        self.add_btn.pack(side=tk.LEFT, padx=5)
        
        # 移除选中按钮
        self.remove_btn = ttk.Button(top_button_frame, text="移除选中", command=self.remove_selected)
        self.remove_btn.pack(side=tk.LEFT, padx=5)
        
        # 上移和下移按钮
        self.up_btn = ttk.Button(top_button_frame, text="↑ 上移", command=self.move_up)
        self.up_btn.pack(side=tk.LEFT, padx=5)
        
        self.down_btn = ttk.Button(top_button_frame, text="↓ 下移", command=self.move_down)
        self.down_btn.pack(side=tk.LEFT, padx=5)
        
        # 清空列表按钮
        self.clear_btn = ttk.Button(top_button_frame, text="清空列表", command=self.clear_list)
        self.clear_btn.pack(side=tk.LEFT, padx=5)
        
        # 文件列表框架
        list_frame = ttk.Frame(left_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 显示选中文件的列表框
        self.file_listbox = tk.Listbox(list_frame, width=70, height=15, selectmode=tk.EXTENDED)
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 绑定选择事件
        self.file_listbox.bind('<<ListboxSelect>>', self.on_select)
        
        # 滚动条
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.file_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_listbox.config(yscrollcommand=scrollbar.set)
        
        # 添加预览区域分隔线
        ttk.Separator(left_frame, orient='horizontal').pack(fill='x', pady=10)
        
        # 预览区域标签
        ttk.Label(left_frame, text="音频预览", font=('', 10, 'bold')).pack(pady=5)
        
        # 音频控制框架
        audio_control_frame = ttk.Frame(left_frame)
        audio_control_frame.pack(fill=tk.X, pady=5)
        
        # 音频进度条
        self.audio_progress_var = tk.DoubleVar()
        self.audio_progress = ttk.Scale(audio_control_frame, from_=0, to=100, 
                                      orient=tk.HORIZONTAL, variable=self.audio_progress_var)
        self.audio_progress.bind("<ButtonPress-1>", self.on_progress_press)
        self.audio_progress.bind("<ButtonRelease-1>", self.on_progress_release)
        self.audio_progress.pack(fill=tk.X, padx=5)
        
        # 音频控制按钮框架
        audio_buttons_frame = ttk.Frame(left_frame)
        audio_buttons_frame.pack(pady=5)
        
        # 合并预览和停止按钮
        self.play_btn = ttk.Button(audio_buttons_frame, text="▶ 预览", command=self.toggle_preview)
        self.play_btn.pack(side=tk.LEFT, padx=5)
        
        # 时间标签
        self.time_label = ttk.Label(audio_buttons_frame, text="00:00 / 00:00")
        self.time_label.pack(side=tk.LEFT, padx=10)
        
        # 添加合并区域分隔线
        ttk.Separator(left_frame, orient='horizontal').pack(fill='x', pady=10)
        
        # 合并区域标签
        ttk.Label(left_frame, text="合并操作", font=('', 10, 'bold')).pack(pady=5)
        
        # 添加输出格式选择框架
        format_frame = ttk.Frame(left_frame)
        format_frame.pack(pady=5)
        
        # 输出格式选择
        self.output_format = tk.StringVar(value="wav")
        ttk.Label(format_frame, text="输出格式：").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(format_frame, text="WAV", variable=self.output_format, 
                        value="wav").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(format_frame, text="MP3", variable=self.output_format, 
                        value="mp3").pack(side=tk.LEFT, padx=5)
        
        # MP3比特率选择
        self.mp3_bitrate = tk.StringVar(value="192")
        self.mp3_bitrate_frame = ttk.Frame(left_frame)
        self.mp3_bitrate_frame.pack(pady=5)
        ttk.Label(self.mp3_bitrate_frame, text="MP3比特率：").pack(side=tk.LEFT, padx=5)
        bitrate_combo = ttk.Combobox(self.mp3_bitrate_frame, textvariable=self.mp3_bitrate, 
                                    values=["128", "192", "256", "320"], width=5)
        bitrate_combo.pack(side=tk.LEFT, padx=5)
        ttk.Label(self.mp3_bitrate_frame, text="kbps").pack(side=tk.LEFT)
        
        # 合并按钮
        self.merge_btn = ttk.Button(left_frame, text="合并文件", command=self.merge_files)
        self.merge_btn.pack(pady=10)
        
        # 合并进度条
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(left_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=10)
        
        # === 右侧属性面板内容 ===
        # 信息面板标题框架
        info_title_frame = ttk.Frame(right_frame)
        info_title_frame.pack(fill=tk.X, pady=10)
        
        # 信息标题
        ttk.Label(info_title_frame, text="信息", font=('', 12, 'bold')).pack(side=tk.LEFT, padx=5)
        
        # 帮助按钮
        help_btn = ttk.Button(info_title_frame, text="?", width=3, 
                             command=self.show_usage_guide,
                             style='Circle.TButton')
        help_btn.pack(side=tk.RIGHT, padx=5)
        
        # 创建圆形按钮样式
        style = ttk.Style()
        style.configure('Circle.TButton', borderwidth=1, relief="circular",
                       padding=0, width=3, bordercolor='gray')
        
        # 信息文本框
        self.info_text = tk.Text(right_frame, wrap=tk.WORD, width=30, height=30)
        self.info_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.info_text.config(state=tk.DISABLED)  # 设置为只读
        
        # 显示初始的使用说明
        self.show_usage_guide()
        
    def show_usage_guide(self):
        """显示使用说明"""
        guide = """
使用说明：

1. 添加文件
   - 点击"添加WAV文件"选择一个或多个WAV文件
   - 支持多选文件

2. 管理文件
   - 选择文件后可以上移/下移调整顺序
   - 可以移除选中的文件
   - 可以清空整个列表

3. 预览音频
   - 选中文件后点击"预览"按钮
   - 使用进度条控制播放位置
   - 点击"停止"结束预览

4. 合并文件
   - 添加完所有文件后
   - 选择输出格式（WAV/MP3）
   - 如果选择MP3可以设置比特率
   - 点击"合并文件"
   - 选择保存位置即可

提示：选中文件后将在此处显示
详细的文件属性信息。
"""
        self.info_text.config(state=tk.NORMAL)
        self.info_text.delete(1.0, tk.END)
        self.info_text.insert(tk.END, guide)
        self.info_text.config(state=tk.DISABLED)
        
    def show_file_properties(self, file_path):
        """显示文件属性"""
        try:
            # 获取文件基本信息
            file_size = os.path.getsize(file_path)
            audio = WAVE(file_path)
            
            # 使用 ffmpeg 获取详细信息
            cmd = ['ffmpeg', '-i', file_path, '-hide_banner']
            process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            # 解析 ffmpeg 输出
            info = process.stderr
            
            # 提取关键信息
            properties = f"文件名：{os.path.basename(file_path)}\n\n"
            properties += f"文件大小：{self.format_size(file_size)}\n\n"
            properties += f"时长：{self.format_time(audio.info.length)}\n\n"
            
            # 提取音频详细信息
            if 'Audio:' in info:
                audio_info = info.split('Audio:')[1].split('\n')[0]
                properties += f"音频信息：\n{audio_info.strip()}\n\n"
            
            # 提取采样率、声道等信息
            properties += f"采样率：{audio.info.sample_rate} Hz\n"
            properties += f"声道数：{audio.info.channels}\n"
            properties += f"比特率：{int(audio.info.bitrate/1000)} kbps\n"
            
            # 更新显示
            self.update_info(properties)
            
        except Exception as e:
            self.update_info(f"无法读取文件属性：{str(e)}")
        
    def format_size(self, size):
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TB"
        
    def add_files(self):
        try:
            initial_dir = self.config.get('last_folder', os.path.expanduser("~"))
            files = filedialog.askopenfilenames(
                filetypes=[("WAV files", "*.wav *.WAV")],  # 修改文件类型格式
                title="选择WAV文件",
                initialdir=initial_dir  # 使用上次的目录
            )
            
            if files:  # files 是一个元组
                if not hasattr(self, 'wav_files'):
                    self.wav_files = []
                
                # 保存最后使用的文件夹路径
                last_folder = os.path.dirname(files[0])
                self.config['last_folder'] = last_folder
                self.save_config()
                
                # 添加文件到列表
                for file in files:
                    if file:  # 确保文件路径不为空
                        self.wav_files.append(file)
                        self.file_listbox.insert(tk.END, os.path.basename(file))
                
                # 更新按钮状态
                self.update_button_states()
                
        except Exception as e:
            messagebox.showerror("错误", f"添加文件时发生错误：{str(e)}")
    
    def remove_selected(self):
        selected = self.file_listbox.curselection()
        if not selected:
            return
        
        # 从后往前删除，避免索引变化
        for idx in reversed(selected):
            self.file_listbox.delete(idx)
            self.wav_files.pop(idx)
    
    def clear_list(self):
        if messagebox.askyesno("确认", "确定要清空列表吗？"):
            self.file_listbox.delete(0, tk.END)
            self.wav_files = []
    
    def format_time(self, seconds):
        minutes = int(seconds // 60)
        seconds = int(seconds % 60)
        return f"{minutes:02d}:{seconds:02d}"
    
    def update_progress(self):
        """更新进度条"""
        if self.is_playing and not self.is_dragging:
            if pygame.mixer.music.get_busy():
                current_pos = pygame.mixer.music.get_pos() / 1000.0  # 转换为秒
                if current_pos >= 0:
                    # 计算实际播放位置
                    actual_pos = current_pos + self.start_offset
                    self.current_position = actual_pos
                    
                    # 更新进度条
                    if actual_pos <= self.current_audio_length:
                        progress = (actual_pos / self.current_audio_length) * 100
                        self.audio_progress_var.set(progress)
                        self.time_label.config(text=f"{self.format_time(actual_pos)} / {self.format_time(self.current_audio_length)}")
            else:
                # 播放结束
                self.is_playing = False
                self.start_offset = 0
                self.current_position = 0
                self.audio_progress_var.set(0)
                self.time_label.config(text=f"00:00 / {self.format_time(self.current_audio_length)}")
                self.update_button_states()
        
        self.window.after(100, self.update_progress)
    
    def on_progress_press(self, event):
        """进度条按下事件"""
        if self.current_audio_path:
            self.is_dragging = True
            pygame.mixer.music.pause()
    
    def on_progress_release(self, event):
        """进度条释放事件"""
        if self.current_audio_path and self.is_dragging:
            # 计算新的播放位置
            pos = (self.audio_progress_var.get() / 100.0) * self.current_audio_length
            self.start_offset = pos  # 记录开始位置
            self.current_position = pos
            
            # 重新加载并播放
            pygame.mixer.music.load(self.current_audio_path)
            pygame.mixer.music.play(start=pos)
            
            # 如果之前不是播放状态，则暂停
            if not self.is_playing:
                pygame.mixer.music.pause()
            
        self.is_dragging = False
    
    def toggle_preview(self):
        """切换预览/停止状态"""
        if self.is_playing:
            self.stop_preview()
        else:
            self.preview_audio()
    
    def preview_audio(self):
        selected = self.file_listbox.curselection()
        if not selected:
            return
        
        try:
            # 获取选中文件路径
            idx = selected[0]
            file_path = self.wav_files[idx]
            
            # 获取音频时长
            audio = WAVE(file_path)
            self.current_audio_length = audio.info.length
            self.current_audio_path = file_path
            
            # 重置播放位置
            self.start_offset = 0
            self.current_position = 0
            
            # 加载并播放音频
            pygame.mixer.music.load(file_path)
            pygame.mixer.music.play()
            self.is_playing = True
            
            # 更新状态
            self.update_info(f"正在播放: {os.path.basename(file_path)}")
            self.play_btn.config(text="■ 停止")
            
        except Exception as e:
            messagebox.showerror("错误", f"无法播放音频：{str(e)}")
    
    def stop_preview(self):
        if pygame.mixer.music.get_busy() or self.is_playing:
            pygame.mixer.music.stop()
            self.is_playing = False
            self.start_offset = 0
            self.current_position = 0
            self.play_btn.config(text="▶ 预览")
            self.update_info("")  # 清空播放状态信息
            self.audio_progress_var.set(0)
            self.time_label.config(text="00:00 / 00:00")
    
    def on_closing(self):
        """窗口关闭时保存配置"""
        self.save_config()
        self.stop_preview()
        pygame.mixer.quit()
        self.window.destroy()
    
    def move_up(self):
        selected = self.file_listbox.curselection()
        if not selected or selected[0] == 0:
            return
        
        idx = selected[0]
        text = self.file_listbox.get(idx)
        self.file_listbox.delete(idx)
        self.file_listbox.insert(idx-1, text)
        self.file_listbox.selection_set(idx-1)
        self.update_wav_files_order()
        
    def move_down(self):
        selected = self.file_listbox.curselection()
        if not selected or selected[0] == self.file_listbox.size()-1:
            return
        
        idx = selected[0]
        text = self.file_listbox.get(idx)
        self.file_listbox.delete(idx)
        self.file_listbox.insert(idx+1, text)
        self.file_listbox.selection_set(idx+1)
        self.update_wav_files_order()
        
    def update_wav_files_order(self):
        # 更新wav_files列表以匹配显示顺序
        new_wav_files = []
        for i in range(self.file_listbox.size()):
            filename = self.file_listbox.get(i)
            for file in self.wav_files:
                if os.path.basename(file) == filename:
                    new_wav_files.append(file)
                    break
        self.wav_files = new_wav_files
            
    def merge_files(self):
        if not hasattr(self, 'wav_files') or not self.wav_files:
            messagebox.showerror("错误", "请先添加WAV文件！")
            return
        
        try:
            # 选择输出文件位置
            initial_dir = self.config.get('last_folder', os.path.expanduser("~"))
            output_format = self.output_format.get()
            filetypes = [("WAV files", "*.wav")] if output_format == "wav" else [("MP3 files", "*.mp3")]
            defaultext = ".wav" if output_format == "wav" else ".mp3"
            
            output_path = filedialog.asksaveasfilename(
                defaultextension=defaultext,
                filetypes=filetypes,
                initialdir=initial_dir,
                title="选择保存位置"
            )
            
            if output_path:
                # 使用临时文件
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as temp_file:
                    filelist_path = temp_file.name
                    
                    # 写入文件列表
                    for wav_file in self.wav_files:
                        temp_file.write(f"file '{wav_file}'\n")
                
                try:
                    # 保存最后使用的文件夹路径
                    self.config['last_folder'] = os.path.dirname(output_path)
                    self.save_config()
                    
                    # 计算总时长
                    total_duration = 0
                    for wav_file in self.wav_files:
                        audio = WAVE(wav_file)
                        total_duration += audio.info.length
                    
                    self.progress_var.set(0)
                    self.update_info("正在合并文件... 0%")
                    self.window.update()
                    
                    # 根据输出格式设置ffmpeg命令
                    if output_format == "wav":
                        cmd = [
                            'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                            '-i', filelist_path, '-c', 'copy', output_path,
                            '-progress', 'pipe:1'
                        ]
                    else:
                        # MP3输出
                        bitrate = self.mp3_bitrate.get()
                        cmd = [
                            'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                            '-i', filelist_path,
                            '-c:a', 'libmp3lame', '-b:a', f'{bitrate}k',
                            output_path,
                            '-progress', 'pipe:1'
                        ]
                    
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        bufsize=1
                    )
                    
                    # 读取进度
                    while True:
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        
                        if line.startswith('out_time_ms='):
                            try:
                                current_ms = int(line.split('=')[1])
                                current_duration = current_ms / 1000000
                                progress = min(100, (current_duration / total_duration) * 100)
                                self.progress_var.set(progress)
                                self.update_info(f"正在合并文件... {progress:.1f}%")
                                self.window.update()
                            except (ValueError, IndexError):
                                continue
                    
                    if process.returncode == 0:
                        self.progress_var.set(100)
                        success_msg = "合并完成！\n\n"
                        # 添加输出文件信息
                        try:
                            cmd = ['ffmpeg', '-i', output_path, '-hide_banner']
                            process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                            if process.stderr:
                                success_msg += "输出文件信息：\n\n"
                                info_lines = process.stderr.split('\n')
                                for line in info_lines:
                                    if any(key in line.lower() for key in ['duration', 'audio:', 'stream']):
                                        success_msg += f"{line.strip()}\n"
                        except Exception as e:
                            success_msg += f"无法获取输出文件信息：{str(e)}"
                        
                        self.update_info(success_msg)
                        self.window.bell()
                        messagebox.showinfo("成功", "文件合并完成！")
                    else:
                        self.progress_var.set(0)
                        error_message = process.stderr.read()
                        self.update_info(f"合并失败：\n\n{error_message}")
                        messagebox.showerror("错误", "合并失败，详细信息请查看右侧面板。")
                    
                finally:
                    # 清理临时文件
                    try:
                        os.unlink(filelist_path)
                    except:
                        pass
                    
        except Exception as e:
            self.progress_var.set(0)
            messagebox.showerror("错误", f"发生错误：{str(e)}")
    
    def update_info(self, text):
        """更新信息面板内容"""
        self.info_text.config(state=tk.NORMAL)
        self.info_text.delete(1.0, tk.END)
        self.info_text.insert(tk.END, text)
        self.info_text.config(state=tk.DISABLED)
    
    def update_button_states(self):
        """更新按钮状态"""
        has_files = hasattr(self, 'wav_files') and len(self.wav_files) > 0
        has_selection = len(self.file_listbox.curselection()) > 0
        
        # 设置按钮状态
        self.play_btn.state(['!disabled'] if has_selection else ['disabled'])
        self.up_btn.state(['!disabled'] if has_selection else ['disabled'])
        self.down_btn.state(['!disabled'] if has_selection else ['disabled'])
        self.remove_btn.state(['!disabled'] if has_selection else ['disabled'])
        self.merge_btn.state(['!disabled'] if has_files else ['disabled'])
        self.clear_btn.state(['!disabled'] if has_files else ['disabled'])

    def on_select(self, event):
        """当列表选择改变时调用"""
        self.update_button_states()
        
        # 更新属性显示
        selected = self.file_listbox.curselection()
        if selected:
            # 如果正在播放，停止播放
            if self.is_playing:
                self.stop_preview()
            
            # 如果选中多个文件，显示选中数量
            if len(selected) > 1:
                total_files = self.file_listbox.size()
                self.update_info(f"已选择 {len(selected)} 个文件（共 {total_files} 个文件）")
            else:
                # 显示单个文件的属性
                file_path = self.wav_files[selected[0]]
                self.show_file_properties(file_path)
        else:
            # 不再自动显示使用说明
            self.update_info("")
    
    def run(self):
        self.window.mainloop()

if __name__ == "__main__":
    app = WavMerger()
    app.run()