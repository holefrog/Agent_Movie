"""
scanner.py - 扫描媒体库目录，解析 NFO，检测并重命名字幕文件。
"""
import xml.etree.ElementTree as ET
import json
from pathlib import Path
import logging
import re
import os
import toml

logger = logging.getLogger(__name__)

def _load_settings() -> dict:
    settings_path = Path(__file__).parent / "settings.toml"
    if not settings_path.exists():
        settings_path = Path(os.path.expanduser("~/Programs/Agent_Movie/settings.toml"))
    if not settings_path.exists():
        return {}
    return toml.load(settings_path)

_settings = _load_settings()
if "scanner" not in _settings:
    raise ValueError("settings.toml 中缺少 [scanner] 配置块")
_scanner_config = _settings["scanner"]

if "video_exts" not in _scanner_config:
    raise ValueError("settings.toml 中 scanner 块缺少 video_exts 配置")
if "sub_exts" not in _scanner_config:
    raise ValueError("settings.toml 中 scanner 块缺少 sub_exts 配置")

# 视频和字幕扩展名，由 settings.toml 动态配置
_VIDEO_EXTS = set(_scanner_config["video_exts"])
_SUB_EXTS = set(_scanner_config["sub_exts"])

# --- 简繁体检测用的特征字 ---
# 只出现在简体中的常用字
_SIMPLIFIED_CHARS = set("个这对与从点问时间来说学会东车经长马鱼鸟关门开书画见现种认让远进还连请说贝达钟钱铁银队阳页飞饭"
                        "体别动办发变号叶团坏声处备头夺实将尔带帮干广应张当录总态战报挂择担拥择据损换搜摇撑无旧电节范药观计议记许设评词试"
                        "话语误调谁谢质贡购赶赵转轮达选铁错际随难须领题风验鸡齐龙龟")
# 只出现在繁体中的常用字
_TRADITIONAL_CHARS = set("個這對與從點問時間來說學會東車經長馬魚鳥關門開書畫見現種認讓遠進還連請說貝達鐘錢鐵銀隊陽頁飛飯"
                         "體別動辦發變號葉團壞聲處備頭奪實將爾帶幫幹廣應張當錄總態戰報掛擇擔擁擇據損換搜搖撐無舊電節範藥觀計議記許設評詞試"
                         "話語誤調誰謝質貢購趕趙轉輪達選鐵錯際隨難須領題風驗雞齊龍龜")

# NFO 中标识中文语音的关键词
_CHINESE_LANG_KEYWORDS = {
    "chinese", "mandarin", "cantonese",
    "普通话", "國語", "国语",
    "广州话", "廣州話", "粤语", "粵語",
    "中文", "闽南语", "閩南語",
}

# 中文字幕文件名中常见的语言标识
_CHINESE_SUB_TAGS = {".zh.", ".zh-cn.", ".zh-tw.", ".chi.", ".chs.", ".cht.", ".chinese."}



def detect_subtitle_language(filepath: Path) -> str:
    """
    读取字幕文件前 2KB，判断语言。
    返回: "zh-CN" / "zh-TW" / "en" / "unknown"
    """
    try:
        # 尝试多种编码读取整个文件（字幕文件通常不大）
        raw_bytes = filepath.read_bytes()
        text = ""
        for encoding in ("utf-8-sig", "utf-16", "utf-16le", "utf-8", "gb18030", "big5"):
            try:
                text = raw_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        
        if not text:
            # 如果全失败，用 utf-8 ignore 强行读取
            text = raw_bytes.decode("utf-8", errors="ignore")
            
        text = text[:2048]

        # 提取纯文本（去掉 SRT 序号、时间戳、ASS 标签等）
        clean = re.sub(r"\d+\n\d{2}:\d{2}:\d{2}.*?-->.*?\n", "", text)
        clean = re.sub(r"\{[^}]*\}", "", clean)  # ASS 样式标签
        clean = re.sub(r"<[^>]*>", "", clean)     # HTML 标签

        # 统计 CJK 字符数量
        cjk_count = sum(1 for ch in clean if "\u4e00" <= ch <= "\u9fff")
        total_alpha = sum(1 for ch in clean if ch.isalpha())

        if total_alpha == 0 and cjk_count == 0:
            return "unknown"

        # 如果前 2KB 中包含一定数量的中文字符（如大于 10 个），即判定为中文字幕
        # （防止 .ass 文件头部大量英文字符导致比例被稀释）
        if cjk_count > 10:
            # 是中文，判断简繁
            simp_count = sum(1 for ch in clean if ch in _SIMPLIFIED_CHARS)
            trad_count = sum(1 for ch in clean if ch in _TRADITIONAL_CHARS)

            if simp_count > trad_count:
                return "zh-CN"
            elif trad_count > simp_count:
                return "zh-TW"
            else:
                # 无法区分，默认简体
                return "zh-CN"
        else:
            # CJK 字符很少或没有，认为是英文或纯拼音
            return "en"

    except Exception as e:
        logger.warning(f"检测字幕语言失败 {filepath}: {e}")
        return "unknown"


def _has_lang_tag(filename_lower: str) -> bool:
    """检查文件名是否已带语言标识"""
    for tag in (".zh.", ".zh-cn.", ".zh-tw.", ".chi.", ".chs.", ".cht.",
                ".en.", ".eng.", ".sdh.", ".fr.", ".da.", ".de.",
                ".es.", ".it.", ".ja.", ".ko.", ".pt.", ".ru.",
                ".en.hi."):
        if tag in filename_lower:
            return True
    return False


def _is_chinese_sub_by_name(filename_lower: str) -> bool:
    """通过文件名判断是否是中文字幕"""
    return any(tag in filename_lower for tag in _CHINESE_SUB_TAGS)


def normalize_subtitle_encoding_to_utf8(filepath: Path) -> None:
    """
    检查字幕文件编码，如果不纯是 UTF-8（或带 BOM），则强制转换并覆盖保存为纯 UTF-8。
    """
    try:
        raw_bytes = filepath.read_bytes()
        if not raw_bytes:
            return

        for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16le", "gb18030", "big5"):
            try:
                text = raw_bytes.decode(encoding)
                utf8_bytes = text.encode("utf-8")
                # 如果解码后再编为纯 UTF-8 与原字节不同，说明原来不是纯 UTF-8，覆写之
                if raw_bytes != utf8_bytes:
                    filepath.write_bytes(utf8_bytes)
                    logger.info(f"统一字幕编码为 UTF-8: {filepath.name} (原编码疑似: {encoding})")
                return
            except UnicodeDecodeError:
                continue
                
        # 所有常规编码都失败，则强行忽略错误转换为 UTF-8
        text = raw_bytes.decode("utf-8", errors="ignore")
        filepath.write_bytes(text.encode("utf-8"))
        logger.info(f"强制转换字幕编码为 UTF-8: {filepath.name}")
    except Exception as e:
        logger.warning(f"字幕编码统一转换失败 {filepath}: {e}")


def rename_subtitle(filepath: Path, detected_lang: str) -> Path:
    """
    根据检测到的语言强制重命名文件。
    会先剥离已有的其他语言标签，再附加正确的标签。
    例如: movie.en.srt → movie.zh-CN.srt
    """
    if detected_lang not in ("zh-CN", "zh-TW", "en"):
        return filepath

    target_tag = f".{detected_lang}"
    name_lower = filepath.name.lower()
    suffix_lower = filepath.suffix.lower()

    # 如果已经完全符合标准命名，直接返回
    if name_lower.endswith(target_tag.lower() + suffix_lower):
        return filepath

    clean_stem = filepath.stem
    # 尽可能剥离尾部存在的旧语言标签
    tags_to_strip = [".zh-cn", ".zh-tw", ".zh", ".chi", ".chs", ".cht", ".chinese", 
                     ".en.hi", ".en", ".eng", ".sdh", ".fr", ".da", ".de", ".es", ".it", 
                     ".ja", ".ko", ".pt", ".ru"]
    
    for tag in tags_to_strip:
        if clean_stem.lower().endswith(tag):
            clean_stem = clean_stem[:-len(tag)]
            break

    new_name = clean_stem + target_tag + filepath.suffix
    new_path = filepath.parent / new_name

    if new_path.exists():
        if filepath.resolve() != new_path.resolve(): # Windows 等大小写不敏感的文件系统处理
            raise FileExistsError(f"冲突: 试图将 '{filepath.name}' 重命名为 '{new_name}' 时失败，该目标文件已存在，请手动排查或删除多余字幕。")
        return filepath

    filepath.rename(new_path)
    logger.info(f"字幕按内容强制重命名: {filepath.name} → {new_name}")
    return new_path


def parse_nfo(nfo_path: Path) -> dict | None:
    """
    解析 NFO 文件，提取影片关键信息。
    返回 None 表示解析失败。
    """
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()
    except ET.ParseError as e:
        logger.warning(f"NFO 解析失败 {nfo_path}: {e}")
        return None

    title = root.findtext("title", "").strip()
    year = root.findtext("year", "").strip()
    languages = root.findtext("languages", "").strip()

    # 提取 IMDB ID
    imdb_id = ""
    for uid in root.findall("uniqueid"):
        if uid.get("type") == "imdb":
            imdb_id = (uid.text or "").strip()
            break

    if not title:
        return None

    return {
        "title": title,
        "year": year,
        "imdb_id": imdb_id,
        "languages": languages,
    }


    return None


def scan_directory(media_path: str) -> list[dict]:
    """
    递归扫描媒体目录，返回影片信息列表。
    每个视频文件对应一条记录。
    """
    root = Path(media_path)
    if not root.is_dir():
        logger.error(f"媒体路径不存在: {media_path}")
        return []

    results = []
    # 用 set 记录已处理的目录，避免重复
    processed_dirs = set()

    for nfo_path in root.rglob("*.nfo"):
        if nfo_path.name.lower() == "sound_track.json" or nfo_path.name.lower() == "language.nfo":
            continue
            
        directory = nfo_path.parent

        if directory in processed_dirs:
            continue
        processed_dirs.add(directory)

        nfo_info = parse_nfo(nfo_path)
        if not nfo_info:
            continue

        # 找到该目录下的视频文件
        video_files = [f for f in directory.iterdir()
                       if f.is_file() and f.suffix.lower() in _VIDEO_EXTS]

        if not video_files:
            continue

        # 提取主视频（选最大的视频文件作为主片）
        main_video = max(video_files, key=lambda f: f.stat().st_size)
        
        is_chinese_audio = None
        has_internal_chinese_sub = False
        
        sound_track_file = directory / "sound_track.json"
        if sound_track_file.exists():
            try:
                with open(sound_track_file, "rb") as f:
                    data = json.load(f)
                    
                # 兼容老数据：如果没有 subtitle_tracks 字段，强制视为未扫描完，重新触发体检
                if "subtitle_tracks" not in data:
                    is_chinese_audio = None
                else:
                    # 判断是否有中文语音
                    for track in data.get("audio_tracks", []):
                        if track.get("lang") == "zh":
                            is_chinese_audio = True
                            break
                    if is_chinese_audio is None:
                        is_chinese_audio = False
                        
                    # 判断是否有内置中文字幕
                    for sub_track in data.get("subtitle_tracks", []):
                        if sub_track.get("lang") == "zh":
                            has_internal_chinese_sub = True
                            break
            except Exception as e:
                logger.warning(f"读取 sound_track.json 失败 {sound_track_file}: {e}")

        if is_chinese_audio is None:
            # 未建库，跳过耗时的字幕读取、重命名等，直接存入拦截队列
            results.append({
                "title": nfo_info["title"],
                "year": nfo_info["year"],
                "imdb_id": nfo_info["imdb_id"],
                "languages": nfo_info["languages"],
                "is_chinese_audio": None,
                "has_external_chinese_sub": False,
                "has_internal_chinese_sub": False,
                "has_english_sub": False,
                "english_sub_path": "",
                "video_path": str(main_video),
                "directory": str(directory),
            })
            continue

        # 找到该目录下所有字幕文件
        sub_files = [f for f in directory.iterdir()
                     if f.is_file() and f.suffix.lower() in _SUB_EXTS]

        chinese_subs = []
        english_subs = []
        dirty_subs = []

        for sub in sub_files:
            name_lower = sub.name.lower()
            
            # 判断是否是需要重命名/清理的脏数据（无明确语言后缀，或者可能是极小的垃圾文件）
            if sub.stat().st_size < 1024:
                dirty_subs.append(sub)
                continue
                
            # 如果文件名不包含合规标签，视为脏数据
            if not any(tag in name_lower for tag in [".zh-cn.", ".zh-tw.", ".zh.", ".en.", ".eng."]):
                dirty_subs.append(sub)
                
            # 按文件名简单归类，用于判断是否已有字幕（仅凭文件名，内容纠正交给 Stage 2）
            if ".zh-cn." in name_lower or ".zh-tw." in name_lower or ".zh." in name_lower or _is_chinese_sub_by_name(name_lower):
                chinese_subs.append(sub)
            elif ".en." in name_lower or ".eng." in name_lower:
                english_subs.append(sub)

        has_external_chinese_sub = len(chinese_subs) > 0
        has_english_sub = len(english_subs) > 0
        english_sub_path = str(english_subs[0]) if english_subs else ""

        results.append({
            "title": nfo_info["title"],
            "year": nfo_info["year"],
            "imdb_id": nfo_info["imdb_id"],
            "languages": nfo_info["languages"],
            "is_chinese_audio": is_chinese_audio,
            "has_external_chinese_sub": has_external_chinese_sub,
            "has_internal_chinese_sub": has_internal_chinese_sub,
            "has_english_sub": has_english_sub,
            "english_sub_path": english_sub_path,
            "video_path": str(main_video),
            "directory": str(directory),
            "dirty_subs_count": len(dirty_subs)
        })

    results.sort(key=lambda x: (-1 if x["is_chinese_audio"] is None else (1 if x["is_chinese_audio"] else 0), x["languages"], x["title"]))
    return results

def normalize_subtitles(media_path: str) -> dict:
    """执行 Stage 2：规范化所有的脏字幕，包括删除极小垃圾文件、读取内容重命名"""
    root = Path(media_path)
    if not root.is_dir():
        return {"success": False, "error": f"媒体路径不存在: {media_path}"}

    processed_count = 0
    deleted_count = 0
    renamed_count = 0
    errors = []
    
    # 注意：这里原本是为了排除不要读到它，现在虽然改名叫 json，但还是保留防卫逻辑
    for nfo_path in root.rglob("*.nfo"):
        if nfo_path.name.lower() == "sound_track.json" or nfo_path.name.lower() == "language.nfo":
            continue
            
        directory = nfo_path.parent
        sub_files = [f for f in directory.iterdir()
                     if f.is_file() and f.suffix.lower() in _SUB_EXTS]
                     
        for sub in sub_files:
            if sub.stat().st_size < 1024:
                try:
                    sub.unlink()
                    deleted_count += 1
                except OSError:
                    pass
                continue
                
            name_lower = sub.name.lower()
            if any(tag in name_lower for tag in [".zh-cn.", ".zh-tw.", ".zh.", ".en.", ".eng."]):
                continue # 已经合规
                
            # 执行重命名
            detected = detect_subtitle_language(sub)
            if detected in ("zh-CN", "zh-TW"):
                normalize_subtitle_encoding_to_utf8(sub)
                
            try:
                new_path = rename_subtitle(sub, detected)
                if new_path.name != sub.name:
                    renamed_count += 1
            except FileExistsError as e:
                errors.append(f"[{directory.name}] {str(e)}")
                
        processed_count += 1
        
    return {
        "success": len(errors) == 0,
        "processed_dirs": processed_count,
        "deleted_count": deleted_count,
        "renamed_count": renamed_count,
        "errors": errors
    }


def get_all_movies(media_paths: list[str]) -> list[dict]:
    """
    扫描所有媒体路径，返回所有的影片列表。
    """
    all_movies = []
    for path in media_paths:
        all_movies.extend(scan_directory(path))
    return all_movies
