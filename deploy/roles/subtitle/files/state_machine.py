"""
state_machine.py - 状态机模块
根据所有影片的状态文件计算全局页面状态
"""
import logging
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass

from metadata_manager import Metadata

logger = logging.getLogger(__name__)


@dataclass
class PageState:
    """页面状态数据类"""
    current_stage: int  # 当前应该显示的Stage (1-5)
    is_blocking: bool  # 是否阻塞（需要用户操作）
    message: str  # 提示信息
    stats: Dict  # 统计信息
    movies: List[Dict]  # 影片列表


class StateMachine:
    """状态机类，计算全局页面状态"""
    
    def __init__(self, media_paths: List[str]):
        """
        初始化状态机
        
        Args:
            media_paths: 媒体库路径列表
        """
        self.media_paths = media_paths
        self.all_movies = []
    
    def scan_all_movies(self) -> List[Dict]:
        """
        获取影片列表
        """
        from scanner import get_all_movies
        self.all_movies = get_all_movies(self.media_paths, force_refresh=False)
        return self.all_movies
    
    def compute_page_state(self) -> PageState:
        """
        计算当前页面状态
        """
        import scanner
        if scanner.scan_status["is_scanning"]:
            return PageState(
                current_stage=1,
                is_blocking=False,
                message="全库扫描正在后台进行中...",
                stats={"total": 0, "stage1_done": 0, "stage2_done": 0},
                movies=[]
            )

        if not self.all_movies:
            self.scan_all_movies()
        
        # 统计各Stage的状态
        stats = self._compute_stats()
        
        # 判断当前应该显示哪个Stage
        current_stage, is_blocking, message = self._determine_current_stage(stats)
        
        return PageState(
            current_stage=current_stage,
            is_blocking=is_blocking,
            message=message,
            stats=stats,
            movies=self.all_movies
        )
    
    def _compute_stats(self) -> Dict:
        """
        计算各Stage的统计信息
        
        Returns:
            统计信息字典
        """
        total = len(self.all_movies)
        
        stats = {
            "total": total,
            "stage1_done": 0,
            "stage2_done": 0,
            "stage3_done": 0,
            "stage4_done": 0,
            "stage5_done": 0,
            "stage2_error": 0,
            "stage3_error": 0,
            "stage4_error": 0,
            "stage5_error": 0,
            "need_cleanup": 0,
            "not_identified": 0,
            "need_subtitle": 0,
            "chinese_audio": 0
        }
        
        for movie in self.all_movies:
            video_path = Path(movie["video_path"])
            metadata = Metadata(video_path)
            
            # 统计各Stage完成情况
            if metadata.is_done(1):
                stats["stage1_done"] += 1
            if metadata.is_done(2):
                stats["stage2_done"] += 1
                if metadata.get_error(2):
                    stats["stage2_error"] += 1
            if metadata.is_done(3):
                stats["stage3_done"] += 1
                if metadata.get_error(3):
                    stats["stage3_error"] += 1
            if metadata.is_done(4):
                stats["stage4_done"] += 1
                if metadata.get_error(4):
                    stats["stage4_error"] += 1
                # 统计中文音频
                audio_info = metadata.get_audio_tracks()
                if audio_info.get("is_chinese_audio"):
                    stats["chinese_audio"] += 1
            if metadata.is_done(5):
                stats["stage5_done"] += 1
                if metadata.get_error(5):
                    stats["stage5_error"] += 1
            
            # 统计需要清洗的影片
            if metadata.is_done(2):
                assessment = metadata.get_subtitles_assessment()
                if assessment.get("has_garbage"):
                    stats["need_cleanup"] += 1
            
            # 统计未识别音轨的影片
            if not metadata.is_done(4):
                stats["not_identified"] += 1
            
            # 统计需要字幕的影片（非中文音频且无中文字幕）
            if metadata.is_done(4):
                audio_info = metadata.get_audio_tracks()
                completion = metadata.get_subtitle_completion()
                if not audio_info.get("is_chinese_audio") and not completion.get("done"):
                    stats["need_subtitle"] += 1
        
        return stats
    
    def _determine_current_stage(self, stats: Dict) -> tuple:
        """
        判断当前应该显示哪个Stage
        """
        total = stats["total"]
        valid_total = stats["stage1_done"]
        
        # Stage 1: 如果没有影片信息，显示Stage 1
        if valid_total < total:
            import scanner
            if scanner.scan_status["is_scanning"]:
                return 1, False, f"正在扫描媒体库... ({valid_total}/{total})"
        
        warning_msg = ""
        if valid_total < total and not scanner.scan_status["is_scanning"]:
            warning_msg = f" (注：有 {total - valid_total} 部影片无元数据被跳过)"

        # Stage 2: 如果未评估字幕，显示Stage 2
        if stats["stage2_done"] < valid_total:
            import scanner
            if scanner.scan_status["is_scanning"]:
                return 2, False, f"正在评估字幕... ({stats['stage2_done']}/{valid_total})"
        
        # Stage 3: 如果需要清洗，显示Stage 3（阻塞）
        if stats["need_cleanup"] > 0:
            return 3, True, f"发现 {stats['need_cleanup']} 部影片需要清洗字幕" + warning_msg
        
        # Stage 3错误处理
        if stats["stage3_error"] > 0:
            return 3, True, f"有 {stats['stage3_error']} 部影片清洗失败，请手动处理" + warning_msg
        
        # Stage 4: 如果未识别音轨，显示Stage 4
        if stats["not_identified"] > 0:
            return 4, True, f"有 {stats['not_identified']} 部影片需要识别音轨" + warning_msg
        
        # Stage 4错误处理
        if stats["stage4_error"] > 0:
            return 4, True, f"有 {stats['stage4_error']} 部影片音轨识别失败" + warning_msg
        
        # Stage 5: 如果需要字幕，显示Stage 5
        if stats["need_subtitle"] > 0:
            return 5, False, f"有 {stats['need_subtitle']} 部影片需要补全字幕" + warning_msg
        
        # Stage 5错误处理
        if stats["stage5_error"] > 0:
            return 5, False, f"有 {stats['stage5_error']} 部影片字幕补全失败" + warning_msg
        
        # 全部完成
        return 5, False, f"全部完成！共 {valid_total} 部影片进入自动化" + warning_msg
    
    def get_movies_for_stage(self, stage: int) -> List[Dict]:
        """
        获取需要执行指定Stage的影片列表
        
        Args:
            stage: Stage编号 (1-5)
        
        Returns:
            影片列表
        """
        movies = []
        
        for movie in self.all_movies:
            video_path = Path(movie["video_path"])
            metadata = Metadata(video_path)
            
            # 跳过已完成的Stage
            if metadata.is_done(stage):
                continue
            
            # 检查前序Stage是否完成
            if not self._check_prerequisites(metadata, stage):
                continue
            
            movies.append(movie)
        
        return movies
    
    def _check_prerequisites(self, metadata: Metadata, stage: int) -> bool:
        """
        检查执行指定Stage的前置条件
        
        Args:
            metadata: 元数据对象
            stage: Stage编号 (1-5)
        
        Returns:
            True表示满足前置条件，False表示不满足
        """
        # Stage 1没有前置条件
        if stage == 1:
            return True
        
        # Stage 2需要Stage 1完成
        if stage == 2:
            return metadata.is_done(1)
        
        # Stage 3需要Stage 2完成
        if stage == 3:
            return metadata.is_done(2)
        
        # Stage 4需要Stage 3完成
        if stage == 4:
            return metadata.is_done(3)
        
        # Stage 5需要Stage 4完成
        if stage == 5:
            return metadata.is_done(4)
        
        return False
    
    def get_stage_progress(self, stage: int) -> Dict:
        """
        获取指定Stage的进度信息
        
        Args:
            stage: Stage编号 (1-5)
        
        Returns:
            进度信息字典
        """
        total = len(self.all_movies)
        done = 0
        error = 0
        
        for movie in self.all_movies:
            video_path = Path(movie["video_path"])
            metadata = Metadata(video_path)
            
            if metadata.is_done(stage):
                done += 1
                if metadata.get_error(stage):
                    error += 1
        
        return {
            "total": total,
            "done": done,
            "error": error,
            "pending": total - done,
            "progress": done / total if total > 0 else 0
        }
