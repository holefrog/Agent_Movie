#!/usr/bin/env python3
import os
import sys
import json
import time
import subprocess
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

import toml
from retry import with_retry
from metadata_manager import Metadata

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scan_sound_track")

# 全局状态，用于 Web UI 监控
stt_status = {
    "is_running": False,
    "should_stop": False,
    "last_ping_time": 0,
    "total_movies": 0,
    "processed_count": 0,
    "current_movie": "",
    "processed_movies": []  # item format: {"title": "Movie Name", "tracks": [{"index": 1, "lang": "en"}]}
}

def get_all_processed_movies(media_paths: list[str]) -> dict:
    """扫描目录，返回所有已经存在状态文件的影片记录及全库电影总数"""
    history = []
    total_library_count = 0
    for base_path in media_paths:
        base_dir = Path(base_path)
        if not base_dir.exists() or not base_dir.is_dir():
            continue
            
        processed_dirs = set()
        for meta_nfo in base_dir.rglob("*.nfo"):
            if meta_nfo.name.lower().endswith(".json") or meta_nfo.name.lower() == "language.nfo":
                continue
                
            movie_dir = meta_nfo.parent
            if movie_dir in processed_dirs:
                continue
                
            video_files = [f for f in movie_dir.iterdir() if f.is_file() and f.suffix.lower() in {".mp4", ".mkv", ".avi", ".wmv", ".flv", ".mov", ".ts", ".rmvb"}]
            if not video_files:
                continue
                
            processed_dirs.add(movie_dir)
            total_library_count += 1
            
            main_video = max(video_files, key=lambda f: f.stat().st_size)
            metadata = Metadata(main_video)
            if metadata.exists():
                try:
                    audio_info = metadata.get_audio_tracks()
                    if audio_info.get("done"):
                        history.append({
                            "title": movie_dir.name,
                            "tracks": audio_info.get("tracks", [])
                        })
                except Exception:
                    pass
                    
    # 按电影名称拼音/字母序排序，方便在界面上查找
    history.sort(key=lambda x: x["title"].lower())
    return {
        "history": history,
        "total_library_count": total_library_count
    }

def get_video_duration(video_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", 
        "-show_entries", "format=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", 
        str(video_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            return float(res.stdout.strip())
    except Exception as e:
        logger.warning(f"无法获取视频时长 {video_path.name}: {e}")
    return 0.0

def get_audio_stream_indices(video_path: Path) -> list[int]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "json",
        str(video_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            return [stream.get("index") for stream in data.get("streams", []) if "index" in stream]
    except Exception as e:
        logger.warning(f"无法获取音频流索引 {video_path.name}: {e}")
    return []

def get_subtitle_streams(video_path: Path) -> list[dict]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "s",
        "-show_entries", "stream=index,codec_name:stream_tags=language,title",
        "-of", "json",
        str(video_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            streams = []
            for stream in data.get("streams", []):
                if "index" not in stream:
                    continue
                index = stream["index"]
                codec = stream.get("codec_name", "")
                tags = stream.get("tags", {})
                lang = tags.get("language", "")
                title = tags.get("title", "")
                
                streams.append({
                    "index": index,
                    "codec": codec,
                    "lang": lang,
                    "title": title
                })
            return streams
    except Exception as e:
        logger.warning(f"无法获取字幕流 {video_path.name}: {e}")
    return []

def extract_subtitle_text(video_path: Path, stream_idx: int, start_seconds: int = 0) -> str:
    """提取内置字幕的文本内容（从指定位置取2分钟，避免扫描整个大文件）"""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_seconds),   # 跳到指定位置
        "-t", "120",                  # 只读取 2 分钟
        "-i", str(video_path),
        "-map", f"0:s:{stream_idx}",
        "-f", "srt",
        "-"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=15)
        # ffmpeg 截断输出时可能返回非零，只要 stdout 有内容就尝试解析
        if res.stdout:
            return res.stdout.decode("utf-8", errors="ignore")
    except Exception as e:
        logger.warning(f"提取字幕文本失败 (stream {stream_idx} @ {start_seconds}s): {e}")
    return ""

def detect_chinese_in_subtitle(text: str) -> bool:
    """检测字幕文本是否包含中文"""
    if not text:
        return False
    
    # 提取纯文本（去掉 SRT 序号、时间戳等）
    import re
    clean = re.sub(r"\d+\n\d{2}:\d{2}:\d{2}.*?-->\s*.*?\n", "", text)
    clean = re.sub(r"\{[^}]*\}", "", clean)  # ASS 样式标签
    clean = re.sub(r"<[^>]*>", "", clean)     # HTML 标签
    
    # 统计 CJK 字符数量
    cjk_count = sum(1 for ch in clean if "\u4e00" <= ch <= "\u9fff")
    
    return cjk_count > 10

def check_internal_chinese_subtitle(video_path: Path, subtitle_streams: list[dict]) -> bool | None:
    """检查是否有内置中文字幕（通过内容检测，多点采样）"""
    if not subtitle_streams:
        return None
    
    # 多点采样：片头可能全是音乐/黑屏，需要往后采样
    # 0分钟、10分钟、20分钟、30分钟，任一段有中文即认定
    sample_offsets = [0, 600, 1200, 1800]
    
    for stream in subtitle_streams:
        stream_idx = stream["index"]
        for offset in sample_offsets:
            text = extract_subtitle_text(video_path, stream_idx, start_seconds=offset)
            if detect_chinese_in_subtitle(text):
                logger.info(f"检测到内置中文字幕 (stream {stream_idx} @ {offset}s): {video_path.name}")
                return True
    
    return False

def extract_audio_segment(video_path: Path, stream_idx: int, start_time: float, output_path: Path) -> bool:
    # 截取 15 秒音频
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-t", "15",
        "-i", str(video_path),
        "-map", f"0:{stream_idx}",
        "-vn",
        "-acodec", "libmp3lame",
        "-ac", "1",
        "-ar", "16000",
        "-q:a", "5",
        str(output_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=30)
        return res.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024
    except Exception as e:
        logger.warning(f"截取音频失败 (stream {stream_idx} @ {start_time}): {e}")
        return False

def detect_language_via_groq(audio_path: Path, api_key: str) -> str:
    import requests
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}
    
    def _do_request():
        # Sleep to respect Groq baseline rate limits
        time.sleep(2.1)
        with open(audio_path, "rb") as f:
            files = {"file": ("audio.mp3", f, "audio/mpeg")}
            data = {
                "model": "whisper-large-v3",
                "response_format": "verbose_json"
            }
            res = requests.post(url, headers=headers, files=files, data=data, timeout=30)
            
            if res.status_code != 200:
                raise Exception(f"{res.status_code} {res.text}")
            return res.json()

    retry_config = {
        "max_retries": 10,
        "base_delay": 2.0,
        "backoff_factor": 1.5,
        "max_delay": 60.0
    }

    try:
        resp_json = with_retry(_do_request, retry_config, label="Groq STT")
        text = resp_json.get("text", "").strip()
        
        # 过滤过短的文本和常见的 Whisper 幻觉 (纯音乐或静音时)
        if len(text) < 5:
            return "unknown"
            
        lower_text = text.lower()
        hallucinations = ["thank you", "thanks for watching", "oh, my god", "subscribe", "amara.org", "¶"]
        if any(h in lower_text for h in hallucinations):
            return "unknown"
            
        return resp_json.get("language", "unknown").lower()
    except Exception as e:
        logger.error(f"Groq API 请求彻底失败: {e}")
        return "unknown"

def analyze_track_language(video_path: Path, stream_idx: int, duration: float, api_key: str) -> str:
    if duration < 60:
        logger.warning(f"视频时长过短 ({duration}s)，跳过识别")
        return "unknown"

    # 提供多个采样点作为备选池，以防刚好抽到静音、纯音乐片段
    sample_points = [
        duration * 0.25, 
        duration * 0.50, 
        duration * 0.75, 
        duration * 0.15, 
        duration * 0.85, 
        duration * 0.35, 
        duration * 0.65
    ]
    detected_langs = []

    for point in sample_points:
        if stt_status.get("should_stop", False):
            logger.info("检测到中止信号，提前结束当前音轨分析")
            break
            
        with NamedTemporaryFile(suffix=".mp3", delete=True) as temp_mp3:
            temp_path = Path(temp_mp3.name)
            if extract_audio_segment(video_path, stream_idx, point, temp_path):
                lang = detect_language_via_groq(temp_path, api_key)
                if lang != "unknown":
                    detected_langs.append(lang)
            else:
                logger.warning(f"提取片段失败 (stream {stream_idx} @ {point})")
                
        # 如果已经成功获取了 3 个含有有效语音的片段，就足够判断了，停止继续尝试
        if len(detected_langs) >= 3:
            break

    if not detected_langs:
        return "unknown"

    # 统计出现最多次的语言
    # "zh" 包含多种方言输出可能，所以统一处理
    lang_counts = {}
    for lang in detected_langs:
        # 统一标准化
        if "zh" in lang or lang in ("chinese", "cantonese", "mandarin"):
            lang = "zh"
        elif "en" in lang or lang == "english":
            lang = "en"
        lang_counts[lang] = lang_counts.get(lang, 0) + 1

    # 如果中文被识别到至少一次，我们就偏向于它是中文发音（因为极少会在纯外文电影里幻觉出长句中文）
    # 相比之下，中文电影里静音部分极容易幻觉出英语 (如 Oh my god)
    zh_count = lang_counts.get("zh", 0)
    
    if zh_count >= 1:
        return "zh"
    
    # 否则取最高票
    return max(lang_counts, key=lang_counts.get)

def build_language_nfo_for_video(video_path: Path, api_key: str) -> dict:
    logger.info(f"开始分析视频音轨: {video_path.name}")
    duration = get_video_duration(video_path)
    stream_indices = get_audio_stream_indices(video_path)
    
    # 使用 metadata_manager
    metadata = Metadata(video_path)
    
    if not stream_indices:
        logger.warning(f"未找到音频流: {video_path.name}")
        metadata.set_audio_tracks("", False, [], "未找到音频流")
        return {"audio_tracks": [], "subtitle_tracks": []}
        
    result = {"audio_tracks": [], "subtitle_tracks": []}
    
    # 检查是否已经有存档，避免重新做耗时的音轨识别
    existing_audio_tracks = None
    if metadata.exists():
        try:
            audio_info = metadata.get_audio_tracks()
            if audio_info.get("done"):
                existing_audio_tracks = audio_info.get("tracks", [])
        except Exception as e:
            logger.warning(f"读取现有状态文件失败, 将重新分析: {e}")
            
    # 分析字幕流 (这是新加的极速逻辑，不耗时)
    subtitle_streams = get_subtitle_streams(video_path)
    result["subtitle_tracks"] = subtitle_streams
    
    # 检查内置中文字幕（通过内容检测）
    has_internal_chinese_sub = check_internal_chinese_subtitle(video_path, subtitle_streams)
    
    if existing_audio_tracks is not None:
        logger.info(f"直接复用已有的音频分析结果: {video_path.name}")
        result["audio_tracks"] = existing_audio_tracks
    else:
        for idx in stream_indices:
            if stt_status.get("should_stop", False):
                logger.info("检测到中止信号，放弃分析剩余音轨")
                break
                
            logger.info(f"正在分析音轨 {idx}...")
            lang = analyze_track_language(video_path, idx, duration, api_key)
            if lang == "unknown":
                raise Exception(f"无法识别音轨 {idx} 的语言 (可能为纯无声或不支持的格式)。根据配置，已报错并退出。")
                
            result["audio_tracks"].append({"index": idx, "lang": lang})
            logger.info(f"音轨 {idx} 分析完成 -> {lang}")
        
    # 保存结果到状态文件
    try:
        # 判断主要语言
        primary_language = ""
        is_chinese_audio = False
        if result["audio_tracks"]:
            primary_language = result["audio_tracks"][0]["lang"]
            is_chinese_audio = primary_language == "zh"
        
        metadata.set_video_info(duration)
        metadata.set_audio_tracks(primary_language, is_chinese_audio, result["audio_tracks"])
        metadata.set_subtitle_tracks(
            has_internal_chinese_sub=has_internal_chinese_sub,
            streams=result["subtitle_tracks"],
        )
        logger.info(f"已保存状态文件: {metadata.metadata_path.name}")
    except Exception as e:
        logger.error(f"保存状态文件失败: {e}")
        
    return result

def load_settings() -> dict:
    settings_path = Path(__file__).parent / "settings.toml"
    if not settings_path.exists():
        settings_path = Path(os.path.expanduser("~/Programs/Agent_Movie/settings.toml"))
    if not settings_path.exists():
        raise FileNotFoundError("找不到 settings.toml")
    return toml.load(settings_path)

def scan_all_movies(api_key: str, media_paths: list[str]):
    settings = load_settings()
    if "scanner" not in settings:
        raise ValueError("settings.toml 中缺少 [scanner] 配置块")
    
    scanner_config = settings["scanner"]
    if "video_exts" not in scanner_config:
        raise ValueError("settings.toml 中 scanner 块缺少 video_exts 配置")
        
    _VIDEO_EXTS = set(scanner_config["video_exts"])
    
    global stt_status
    stt_status["is_running"] = True
    stt_status["should_stop"] = False
    stt_status["processed_count"] = 0
    stt_status["already_processed_count"] = 0
    stt_status["total_library_count"] = 0
    stt_status["current_movie"] = ""
    stt_status["error"] = ""
    stt_status["processed_movies"] = []
    
    # 第一次遍历，统计需要跑批的电影总数
    movies_to_process = []
    for base_path in media_paths:
        base_dir = Path(base_path)
        if not base_dir.exists() or not base_dir.is_dir():
            continue
            
        processed_dirs = set()
        for meta_nfo in base_dir.rglob("*.nfo"):
            if meta_nfo.name.lower().endswith(".json") or meta_nfo.name.lower() == "language.nfo":
                continue
                
            movie_dir = meta_nfo.parent
            if movie_dir in processed_dirs:
                continue
                
            video_files = [f for f in movie_dir.iterdir() if f.is_file() and f.suffix.lower() in _VIDEO_EXTS]
            if not video_files:
                continue
                
            processed_dirs.add(movie_dir)
                
            main_video = max(video_files, key=lambda f: f.stat().st_size)
            metadata = Metadata(main_video)
            if metadata.exists():
                try:
                    audio_info = metadata.get_audio_tracks()
                    # tracks 非空才视为真正完成了音轨识别
                    if audio_info.get("done") and audio_info.get("tracks"):
                        sub_tracks = metadata.get_subtitle_tracks()
                        # has_internal_chinese_sub 必须是 True/False 才算完成，None 表示未检测过
                        if sub_tracks.get("has_internal_chinese_sub") is not None:
                            stt_status["already_processed_count"] += 1
                            continue
                except Exception:
                    pass
            
            movies_to_process.append((movie_dir, main_video))
            
    # 按电影名称字母序排序，保证体检扫描顺序稳定且可预测
    movies_to_process.sort(key=lambda x: x[0].name.lower())
            
    stt_status["total_movies"] = len(movies_to_process)
    stt_status["total_library_count"] = stt_status["already_processed_count"] + len(movies_to_process)
    stt_status["last_ping_time"] = time.time()  # 初始化
    
    # 启动看门狗线程，独立监控心跳
    def watchdog():
        while stt_status["is_running"]:
            if not stt_status["should_stop"] and time.time() - stt_status.get("last_ping_time", 0) > 10:
                logger.info("心跳看门狗：10秒未收到 Web 前端请求，判断页面已关闭，立即发送中止信号！")
                stt_status["should_stop"] = True
                break
            time.sleep(2)
            
    import threading
    threading.Thread(target=watchdog, daemon=True).start()
    
    for movie_dir, main_video in movies_to_process:
        if stt_status["should_stop"]:
            logger.info("收到中止信号，停止全库跑批。")
            break
            
        if time.time() - stt_status.get("last_ping_time", 0) > 10:
            logger.info("心跳超时（10秒未收到 Web 前端请求），判断页面已关闭，自动中止全库跑批。")
            break
            
        stt_status["current_movie"] = movie_dir.name
        
        try:
            res = build_language_nfo_for_video(main_video, api_key)
            stt_status["processed_movies"].insert(0, {
                "title": movie_dir.name,
                "tracks": res.get("audio_tracks", [])
            })
        except Exception as e:
            logger.error(f"分析失败 {movie_dir.name}: {e}")
            stt_status["error"] = str(e)
            stt_status["should_stop"] = True
            break
            
        stt_status["processed_count"] += 1
        
    stt_status["is_running"] = False
    stt_status["current_movie"] = ""

def main():
    try:
        settings = load_settings()
        api_key = settings["translate"]["groq"]["api_key"]
        if not api_key:
            logger.error("Groq API Key 为空")
            sys.exit(1)
            
        if len(sys.argv) > 1:
            target_path = Path(sys.argv[1])
            if target_path.is_file():
                build_language_nfo_for_video(target_path, api_key)
            elif target_path.is_dir():
                # 如果传入的是某个媒体库目录，扫描该目录
                scan_all_movies(api_key, [str(target_path)])
        else:
            # 扫描所有配置的路径
            media_paths = settings["scanner"]["media_paths"]
            scan_all_movies(api_key, media_paths)
    except KeyboardInterrupt:
        logger.info("用户中断")
    except Exception as e:
        logger.error(f"运行时错误: {e}", exc_info=True)

if __name__ == "__main__":
    main()
