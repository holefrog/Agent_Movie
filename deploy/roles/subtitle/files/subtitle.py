"""
subtitle.py - 中文字幕获取：OpenSubtitles 下载 + LLM 翻译（多 provider）。
"""
import re
import time
import logging
import sys
from pathlib import Path

from retry import with_retry

logger = logging.getLogger(__name__)


# ============================================================
# 第一条路：从 OpenSubtitles 下载
# ============================================================

def download_chinese_sub(movie: dict, os_config: dict) -> str | None:
    """
    从 OpenSubtitles 下载中文字幕。
    成功返回保存的文件路径，失败返回 None。
    """
    api_key = os_config["api_key"]
    username = os_config["username"]
    password = os_config["password"]

    if not api_key:
        logger.info("OpenSubtitles 未配置 API Key (值为空)，跳过下载")
        return None

    imdb_id = movie.get("imdb_id", "")
    if not imdb_id:
        logger.warning(f"影片 {movie['title']} 无 IMDB ID，无法搜索字幕")
        return None

    try:
        from opensubtitlescom import OpenSubtitles
    except ImportError:
        logger.error("opensubtitlescom 未安装，请运行: pip install opensubtitlescom")
        sys.exit(1)

    try:
        ost = OpenSubtitles("AgentMovie/1.0", api_key)

        if username and password:
            ost.login(username, password)

        imdb_num = imdb_id.replace("tt", "")
        results = ost.search(imdb_id=imdb_num, languages="zh-cn,zh-tw")

        if not results or not results.data:
            logger.info(f"OpenSubtitles 未找到中文字幕: {movie['title']}")
            return None

        best = results.data[0]
        sub_content = ost.download_and_parse(best)

        video_path = Path(movie["video_path"])
        save_name = video_path.stem + ".chi.srt"
        save_path = video_path.parent / save_name

        save_path.write_text(sub_content, encoding="utf-8")
        logger.info(f"下载成功: {save_path}")
        return str(save_path)

    except Exception as e:
        logger.error(f"OpenSubtitles 下载失败 ({movie['title']}): {e}")
        return None


# ============================================================
# 第二条路：LLM 翻译（多 provider 支持）
# ============================================================

def _parse_srt(text: str) -> list[dict]:
    """解析 SRT 文件为结构化列表"""
    blocks = re.split(r"\n\s*\n", text.strip())
    entries = []

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue

        try:
            index = int(lines[0].strip())
        except ValueError:
            continue

        timestamp = lines[1].strip()
        if "-->" not in timestamp:
            continue

        content = "\n".join(lines[2:]).strip()
        if not content:
            continue

        entries.append({
            "index": index,
            "timestamp": timestamp,
            "content": content,
        })

    return entries


def _build_srt(entries: list[dict]) -> str:
    """将结构化列表重新组装为 SRT 文本"""
    parts = []
    for e in entries:
        parts.append(f"{e['index']}\n{e['timestamp']}\n{e['content']}")
    return "\n\n".join(parts) + "\n"


def _build_prompt(entries: list[dict], movie_title: str, system_prompt: str) -> tuple[str, str]:
    """构造翻译 prompt，返回 (system, user)"""
    text_lines = [f"[{i}] {entry['content']}" for i, entry in enumerate(entries)]
    text_block = "\n".join(text_lines)

    user_prompt = f"电影：{movie_title}\n\n{text_block}"
    return system_prompt, user_prompt


def _parse_translation(result_text: str, entries: list[dict]) -> list[str]:
    """解析 LLM 返回的翻译结果"""
    translated = {}
    for line in result_text.split("\n"):
        match = re.match(r"\[(\d+)\]\s*(.*)", line.strip())
        if match:
            idx = int(match.group(1))
            translated[idx] = match.group(2).strip()

    return [translated.get(i, entry["content"]) for i, entry in enumerate(entries)]


# --- 各 provider 的调用实现 ---

def _call_gemini(system_prompt: str, user_prompt: str, config: dict) -> str:
    """调用 Gemini API"""
    try:
        from google import genai
    except ImportError:
        logger.error("google-genai 未安装")
        sys.exit(1)

    client = genai.Client(api_key=config["api_key"])
    prompt = f"{system_prompt}\n\n{user_prompt}"
    response = client.models.generate_content(
        model=config["model"],
        contents=prompt,
    )
    return response.text.strip()


def _call_openai_compatible(system_prompt: str, user_prompt: str, config: dict,
                            base_url: str | None = None) -> str:
    """
    调用 OpenAI 兼容接口。
    OpenAI / Mistral / Groq / Nvidia 都使用此函数，只是 base_url 不同。
    """
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai 未安装")
        sys.exit(1)

    client_kwargs = {"api_key": config["api_key"]}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)
    response = client.chat.completions.create(
        model=config["model"],
        temperature=config["temperature"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content.strip()


# Provider → (调用函数, base_url) 映射
_PROVIDER_MAP = {
    "gemini": (_call_gemini, None),
    "openai": (_call_openai_compatible, None),
    "mistral": (_call_openai_compatible, "https://api.mistral.ai/v1"),
    "groq": (_call_openai_compatible, "https://api.groq.com/openai/v1"),
    "nvidia": (_call_openai_compatible, "https://integrate.api.nvidia.com/v1"),
}


def _translate_batch(entries: list[dict], movie_title: str, config: dict) -> list[str]:
    """用配置中指定的 provider 翻译一批字幕"""
    provider = config["provider"]
    system_prompt = config["system_prompt"]
    sys_prompt, user_prompt = _build_prompt(entries, movie_title, system_prompt)

    call_fn, base_url = _PROVIDER_MAP[provider]

    def _do_call():
        if call_fn == _call_gemini:
            return call_fn(sys_prompt, user_prompt, config)
        else:
            return call_fn(sys_prompt, user_prompt, config, base_url)

    retry_config = {
        "max_retries": 10,
        "base_delay": 2.0,
        "backoff_factor": 1.5,
        "max_delay": 60.0
    }

    result_text = with_retry(_do_call, retry_config, label=f"Translate {provider}")
    return _parse_translation(result_text, entries)


def translate_subtitle(movie: dict, translate_config: dict) -> str | None:
    """
    用 LLM 将英文字幕翻译为中文。
    成功返回保存的文件路径，失败返回 None。
    """
    sub_path = movie.get("english_sub_path", "")
    if not sub_path:
        logger.info(f"影片 {movie['title']} 无英文字幕，无法翻译")
        return None

    api_key = translate_config["api_key"]
    if not api_key:
        logger.error("翻译 API Key 值为空")
        return None

    sub_file = Path(sub_path)

    # 读取英文字幕（尝试多种编码）
    text = ""
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            text = sub_file.read_text(encoding=encoding, errors="ignore")
            break
        except (UnicodeDecodeError, ValueError):
            continue

    if not text:
        logger.error(f"无法读取字幕文件: {sub_path}")
        return None

    entries = _parse_srt(text)
    if not entries:
        logger.error(f"字幕文件解析为空: {sub_path}")
        return None

    provider = translate_config["provider"]
    logger.info(f"开始翻译 {movie['title']}（{provider}），共 {len(entries)} 条字幕")

    # 分批翻译
    batch_size = translate_config["batch_size"]
    for start in range(0, len(entries), batch_size):
        end = min(start + batch_size, len(entries))
        batch = entries[start:end]
        batch_num = start // batch_size + 1
        total_batches = (len(entries) + batch_size - 1) // batch_size

        logger.info(f"  翻译第 {batch_num}/{total_batches} 批...")

        try:
            translations = _translate_batch(batch, movie["title"], translate_config)
            for i, trans in enumerate(translations):
                entries[start + i]["content"] = trans
        except Exception as e:
            logger.error(f"  第 {batch_num} 批翻译失败: {e}")
            return None

        # 批次间等待，避免限速
        if end < len(entries):
            time.sleep(2)

    # 保存中文字幕
    video_path = Path(movie["video_path"])
    save_name = video_path.stem + ".zh-CN.srt"
    save_path = video_path.parent / save_name

    srt_text = _build_srt(entries)
    save_path.write_text(srt_text, encoding="utf-8")
    logger.info(f"翻译完成: {save_path}")
    return str(save_path)


def get_chinese_subtitle(movie: dict, os_config: dict, translate_config: dict) -> dict:
    """
    获取中文字幕的统一入口。先下载，失败则翻译。
    返回: {"success": bool, "method": str, "path": str, "error": str}
    """
    # 第一步：尝试从 OpenSubtitles 下载
    result = download_chinese_sub(movie, os_config)
    if result:
        return {"success": True, "method": "download", "path": result, "error": ""}

    # 第二步：尝试 LLM 翻译
    if movie.get("has_english_sub"):
        result = translate_subtitle(movie, translate_config)
        if result:
            return {"success": True, "method": "translate", "path": result, "error": ""}
        return {"success": False, "method": "translate", "path": "", "error": "翻译失败"}

    return {"success": False, "method": "none", "path": "", "error": "无英文字幕可翻译，下载也失败"}
