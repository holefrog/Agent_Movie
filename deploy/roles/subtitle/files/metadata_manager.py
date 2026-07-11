"""
metadata_manager.py - 状态文件管理模块
封装 [视频名].json 的读写，提供统一的接口访问影片状态
"""
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Any
import fcntl  # 文件锁，确保并发安全

logger = logging.getLogger(__name__)


class Metadata:
    """影片元数据管理类，封装状态文件的读写操作"""
    
    def __init__(self, video_path: Path):
        """
        初始化元数据管理器
        
        Args:
            video_path: 视频文件路径，状态文件名为 [视频名].json
        """
        self.video_path = video_path
        self.metadata_path = video_path.parent / f"{video_path.stem}.json"
        self._data = None
    
    def _load(self) -> dict:
        """加载状态文件内容"""
        if self._data is not None:
            return self._data
        
        if not self.metadata_path.exists():
            # 文件不存在，返回空数据结构
            self._data = self._get_empty_structure()
            return self._data
        
        try:
            with open(self.metadata_path, 'r', encoding='utf-8') as f:
                self._data = json.load(f)
            
            # 如果文件内容不是字典，重置为空结构
            if not isinstance(self._data, dict):
                logger.warning(f"状态文件格式错误 ({type(self._data)})，重置: {self.metadata_path.name}")
                self._data = self._get_empty_structure()
                self.save()
                return self._data
            
            # 确保结构完整并迁移
            self._ensure_structure()
            
            return self._data
        except Exception as e:
            logger.error(f"加载状态文件失败 {self.metadata_path}: {e}")
            self._data = self._get_empty_structure()
            return self._data
    
    def _get_empty_structure(self) -> dict:
        """返回空的数据结构"""
        return {
            "version": 2,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "movie": {
                "title": "",
                "year": "",
                "imdb_id": "",
                "tmdb_id": "",
                "version": ""
            },
            "video_file": {
                "path": str(self.video_path),
                "duration_seconds": 0
            },
            "subtitles_assessment": {
                "done": False,
                "has_chinese": False,
                "has_english": False,
                "has_garbage": False,
                "error": None
            },
            "subtitles_cleanup": {
                "done": False,
                "files_deleted": 0,
                "files_renamed": 0,
                "error": None
            },
            "audio_tracks": {
                "done": False,
                "primary_language": "",
                "is_chinese_audio": False,
                "tracks": [],
                "error": None
            },
            "subtitle_tracks": {
                "done": False,
                "has_internal_chinese_sub": None,
                "streams": [],
                "error": None
            },
            "subtitle_completion": {
                "done": False,
                "method": "",
                "translator": "",
                "chinese_subtitle": "",
                "sync_offset": 0.0,
                "mismatch_detected": False,
                "error": None
            }
        }
    
    def _ensure_structure(self):
        """确保数据结构完整（修复由于历史遗留导致的部分缺失）"""
        empty = self._get_empty_structure()
        changed = False
        
        # 遍历所有空结构的键
        for key, default_val in empty.items():
            if key not in self._data:
                # 针对多层嵌套的字典我们需要 copy 以防止引用同一个字典
                if isinstance(default_val, dict):
                    self._data[key] = default_val.copy()
                else:
                    self._data[key] = default_val
                changed = True
            elif isinstance(default_val, dict):
                # 确保存量也是 dict
                if not isinstance(self._data[key], dict):
                    self._data[key] = default_val.copy()
                    changed = True
                else:
                    # 遍历子字典，补充缺少的键
                    for sub_key, sub_val in default_val.items():
                        if sub_key not in self._data[key]:
                            self._data[key][sub_key] = sub_val
                            changed = True
            
        if changed:
            self.save()
    
    def save(self):
        """保存状态文件到磁盘（使用原子替换，解决并发读取到空文件的问题）"""
        if self._data is None:
            return
        
        try:
            self._data["last_updated"] = self._get_current_timestamp()
            
            import tempfile
            import os
            # 使用相同目录以确保 rename 在同一个文件系统上（特别是在 NAS/NFS 环境）
            fd, tmp_path = tempfile.mkstemp(dir=self.metadata_path.parent, prefix=".", suffix=".tmp")
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(self._data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())  # 确保数据真正落盘
                # 原子替换目标文件
                os.replace(tmp_path, self.metadata_path)
            except Exception as inner_e:
                # 出现异常时清理临时文件
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise inner_e
            
            logger.debug(f"保存状态文件成功: {self.metadata_path.name}")
        except Exception as e:
            logger.error(f"保存状态文件失败 {self.metadata_path}: {e}")
    
    def _get_current_timestamp(self) -> str:
        """获取当前时间戳（ISO8601格式）"""
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"
    
    # ==================== Stage 1: 影片信息 ====================
    
    def set_movie_info(self, title: str, year: str, imdb_id: str, version: str = "", tmdb_id: str = ""):
        """设置影片信息"""
        data = self._load()
        data["movie"]["title"] = title
        data["movie"]["year"] = year
        data["movie"]["imdb_id"] = imdb_id
        data["movie"]["tmdb_id"] = tmdb_id
        data["movie"]["version"] = version
        self.save()
    
    def get_movie_info(self) -> dict:
        """获取影片信息"""
        data = self._load()
        return data["movie"]
    
    def set_video_info(self, duration_seconds: float):
        """设置视频信息"""
        data = self._load()
        data["video_file"]["path"] = str(self.video_path)
        data["video_file"]["duration_seconds"] = duration_seconds
        self.save()
    
    def get_video_info(self) -> dict:
        """获取视频信息"""
        data = self._load()
        return data["video_file"]
    
    # ==================== Stage 2: 字幕评估 ====================
    
    def set_subtitles_assessment(self, has_chinese: bool, has_english: bool, has_garbage: bool, error: Optional[str] = None):
        """设置字幕评估结果"""
        data = self._load()
        data["subtitles_assessment"]["done"] = True
        data["subtitles_assessment"]["has_chinese"] = has_chinese
        data["subtitles_assessment"]["has_english"] = has_english
        data["subtitles_assessment"]["has_garbage"] = has_garbage
        data["subtitles_assessment"]["error"] = error
        self.save()
    
    def get_subtitles_assessment(self) -> dict:
        """获取字幕评估结果"""
        data = self._load()
        return data["subtitles_assessment"]
    
    # ==================== Stage 3: 字幕清洗 ====================
    
    def set_subtitles_cleanup(self, files_deleted: int, files_renamed: int, error: Optional[str] = None):
        """设置字幕清洗结果"""
        data = self._load()
        data["subtitles_cleanup"]["done"] = True
        data["subtitles_cleanup"]["files_deleted"] = files_deleted
        data["subtitles_cleanup"]["files_renamed"] = files_renamed
        data["subtitles_cleanup"]["error"] = error
        self.save()
    
    def get_subtitles_cleanup(self) -> dict:
        """获取字幕清洗结果"""
        data = self._load()
        return data["subtitles_cleanup"]
    
    # ==================== Stage 4: 媒体语言鉴定 ====================
    
    def set_audio_tracks(self, primary_language: str, is_chinese_audio: bool, tracks: list, error: Optional[str] = None):
        """设置音轨识别结果"""
        data = self._load()
        data["audio_tracks"]["done"] = True
        data["audio_tracks"]["primary_language"] = primary_language
        data["audio_tracks"]["is_chinese_audio"] = is_chinese_audio
        data["audio_tracks"]["tracks"] = tracks
        data["audio_tracks"]["error"] = error
        self.save()
    
    def get_audio_tracks(self) -> dict:
        """获取音轨识别结果"""
        data = self._load()
        return data["audio_tracks"]
    
    def set_subtitle_tracks(self, has_internal_chinese_sub: bool | None, streams: list, error: Optional[str] = None):
        """设置内置字幕流识别结果（Stage 4 运行）"""
        data = self._load()
        data["subtitle_tracks"]["done"] = True
        data["subtitle_tracks"]["has_internal_chinese_sub"] = has_internal_chinese_sub
        data["subtitle_tracks"]["streams"] = streams
        data["subtitle_tracks"]["error"] = error
        self.save()
    
    def get_subtitle_tracks(self) -> dict:
        """获取内置字幕流识别结果"""
        data = self._load()
        return data["subtitle_tracks"]
    
    # ==================== Stage 5: 字幕补全 ====================
    
    def set_subtitle_completion(self, method: str, translator: str, chinese_subtitle: str, 
                                sync_offset: float = 0.0, mismatch_detected: bool = False, 
                                error: Optional[str] = None):
        """设置字幕补全结果"""
        data = self._load()
        data["subtitle_completion"]["done"] = True
        data["subtitle_completion"]["method"] = method
        data["subtitle_completion"]["translator"] = translator
        data["subtitle_completion"]["chinese_subtitle"] = chinese_subtitle
        data["subtitle_completion"]["sync_offset"] = sync_offset
        data["subtitle_completion"]["mismatch_detected"] = mismatch_detected
        data["subtitle_completion"]["error"] = error
        self.save()
    
    def get_subtitle_completion(self) -> dict:
        """获取字幕补全结果"""
        data = self._load()
        return data["subtitle_completion"]
    
    # ==================== 通用接口 ====================
    
    def is_done(self, stage: int) -> bool:
        """
        检查指定Stage是否已完成
        
        Args:
            stage: Stage编号 (1-5)
        
        Returns:
            True表示已完成，False表示未完成
        """
        stage_map = {
            1: lambda d: bool(d["movie"]["title"] and d["movie"]["imdb_id"]),
            2: lambda d: d["subtitles_assessment"]["done"],
            3: lambda d: d["subtitles_cleanup"]["done"],
            4: lambda d: d["audio_tracks"]["done"],
            5: lambda d: d["subtitle_completion"]["done"]
        }
        
        if stage not in stage_map:
            logger.warning(f"无效的Stage编号: {stage}")
            return False
        
        data = self._load()
        return stage_map[stage](data)
    
    def get_error(self, stage: int) -> Optional[str]:
        """
        获取指定Stage的错误信息
        
        Args:
            stage: Stage编号 (1-5)
        
        Returns:
            错误信息，无错误返回None
        """
        stage_map = {
            1: lambda d: None,  # Stage 1没有error字段
            2: lambda d: d["subtitles_assessment"]["error"],
            3: lambda d: d["subtitles_cleanup"]["error"],
            4: lambda d: d["audio_tracks"]["error"],
            5: lambda d: d["subtitle_completion"]["error"]
        }
        
        if stage not in stage_map:
            logger.warning(f"无效的Stage编号: {stage}")
            return None
        
        data = self._load()
        return stage_map[stage](data)
    
    def set_error(self, stage: int, error: str):
        """
        设置指定Stage的错误信息
        
        Args:
            stage: Stage编号 (1-5)
            error: 错误信息
        """
        if stage == 1:
            logger.warning("Stage 1不支持设置错误信息")
            return
        
        stage_map = {
            2: "subtitles_assessment",
            3: "subtitles_cleanup",
            4: "audio_tracks",
            5: "subtitle_completion"
        }
        
        if stage not in stage_map:
            logger.warning(f"无效的Stage编号: {stage}")
            return
        
        data = self._load()
        data[stage_map[stage]]["error"] = error
        self.save()
    
    def reset_stage(self, stage: int):
        """
        重置指定Stage的状态（用于重新处理）
        
        Args:
            stage: Stage编号 (1-5)
        """
        stage_map = {
            1: lambda d: self._reset_stage1(d),
            2: lambda d: self._reset_stage2(d),
            3: lambda d: self._reset_stage3(d),
            4: lambda d: self._reset_stage4(d),
            5: lambda d: self._reset_stage5(d)
        }
        
        if stage not in stage_map:
            logger.warning(f"无效的Stage编号: {stage}")
            return
        
        data = self._load()
        stage_map[stage](data)
        self.save()
        logger.info(f"重置Stage {stage}状态: {self.metadata_path.name}")
    
    def _reset_stage1(self, data: dict):
        """重置Stage 1"""
        data["movie"] = {"title": "", "year": "", "imdb_id": "", "version": ""}
        data["video_file"] = {"path": str(self.video_path), "duration_seconds": 0}
    
    def _reset_stage2(self, data: dict):
        """重置Stage 2"""
        data["subtitles_assessment"] = {
            "done": False,
            "has_chinese": False,
            "has_english": False,
            "has_garbage": False,
            "error": None
        }
    
    def _reset_stage3(self, data: dict):
        """重置Stage 3"""
        data["subtitles_cleanup"] = {
            "done": False,
            "files_deleted": 0,
            "files_renamed": 0,
            "error": None
        }
    
    def _reset_stage4(self, data: dict):
        """重置Stage 4"""
        data["audio_tracks"] = {
            "done": False,
            "primary_language": "",
            "is_chinese_audio": False,
            "tracks": [],
            "error": None
        }
    
    def _reset_stage5(self, data: dict):
        """重置Stage 5"""
        data["subtitle_completion"] = {
            "done": False,
            "method": "",
            "translator": "",
            "chinese_subtitle": "",
            "sync_offset": 0.0,
            "mismatch_detected": False,
            "error": None
        }
    
    def exists(self) -> bool:
        """检查状态文件是否存在"""
        return self.metadata_path.exists()
    
    def delete(self):
        """删除状态文件"""
        if self.metadata_path.exists():
            try:
                self.metadata_path.unlink()
                logger.info(f"删除状态文件: {self.metadata_path.name}")
            except Exception as e:
                logger.error(f"删除状态文件失败 {self.metadata_path}: {e}")
    
    def get_all_data(self) -> dict:
        """获取完整的数据结构"""
        return self._load()
