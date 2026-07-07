"""
app.py - Flask Web 入口。
提供影片列表页面，用户勾选后逐个获取中文字幕。
"""
import sys
import tomllib
import logging
from pathlib import Path
from flask import Flask, render_template, request, jsonify

from scanner import get_all_movies
from subtitle import get_chinese_subtitle

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="web", static_folder="web", static_url_path="/static")

# 配置文件路径：与 app.py 同级的 settings.toml
_CONFIG_PATH = Path(__file__).parent / "settings.toml"


def load_config() -> dict:
    """加载 TOML 配置文件并严格校验"""
    if not _CONFIG_PATH.exists():
        logger.error(f"配置文件不存在: {_CONFIG_PATH}")
        logger.error("请先运行 Ansible playbook 生成配置，或手动创建 settings.toml")
        sys.exit(1)
        
    try:
        with open(_CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
    except Exception as e:
        logger.error(f"解析配置文件失败: {e}")
        sys.exit(1)
        
    # 严格校验必填配置，不 fallback，缺失直接退出
    try:
        _ = config["scanner"]["media_paths"]
        provider = config["provider"]["translate"]
        _ = config["translate"]["common"]["system_prompt"]
        _ = config["translate"]["common"]["timeout"]
        _ = config["translate"]["common"]["batch_size"]
        _ = config["translate"]["common"]["temperature"]
        _ = config["translate"][provider]["api_key"]
        _ = config["translate"][provider]["model"]
        _ = config["opensubtitles"]["api_key"]
        _ = config["opensubtitles"]["username"]
        _ = config["opensubtitles"]["password"]
        _ = config["web"]["host"]
        _ = config["web"]["port"]
    except KeyError as e:
        logger.error(f"配置文件缺少必填项或结构错误: {e}")
        sys.exit(1)
        
    return config

# 启动时立刻执行校验
_ = load_config()


@app.route("/")
def index():
    """渲染一个带有 Loading 的中间页，随后在浏览器里通过 ajax 抓取内容，避免网页白屏卡顿"""
    return render_template("loading.html")

@app.route("/_index_content")
def _index_content():
    """执行耗时的全库扫描，并返回真正的页面 HTML"""
    import scanner
    try:
        config = load_config()
        media_paths = config["scanner"]["media_paths"]
        all_movies = get_all_movies(media_paths)
        
        missing_nfo_count = sum(1 for m in all_movies if m["is_chinese_audio"] is None)
        # 统计含有脏字幕的【电影数】，而不是总文件数
        dirty_subs_movie_count = sum(1 for m in all_movies if m.get("dirty_subs_count", 0) > 0)

        # 只保留确实需要且可以翻译中文字幕的电影（排除中文语音）
        # 强制要求：如果 is_chinese_audio 是 None（未生成 nfo），它不应该出现在翻译列表里
        movies = [
            m for m in all_movies 
            if not m["has_external_chinese_sub"] 
            and not m["has_internal_chinese_sub"] 
            and m["is_chinese_audio"] is False
        ]
        
        return render_template("index.html", movies=movies, missing_nfo_count=missing_nfo_count, dirty_subs_count=dirty_subs_movie_count)
    except Exception as e:
        logger.error(f"加载首页失败: {e}", exc_info=True)
        return render_template("index.html", error=str(e), movies=[], missing_nfo_count=0, dirty_subs_count=0)


@app.route("/submit", methods=["POST"])
def submit():
    """接收用户勾选的影片，逐个获取中文字幕"""
    config = load_config()
    os_config = config["opensubtitles"]

    # 构造翻译配置
    provider_name = config["provider"]["translate"]
    common_config = config["translate"]["common"]
    provider_config = config["translate"][provider_name]

    translate_config = {
        "provider": provider_name,
        **common_config,
        **provider_config,
    }

    selected = request.json.get("selected", [])
    if not selected:
        return jsonify({"error": "未选择任何影片"}), 400

    media_paths = config["scanner"]["media_paths"]
    all_movies = get_all_movies(media_paths)

    selected_set = set(selected)
    to_process = [m for m in all_movies if m["directory"] in selected_set]

    results = []
    for i, movie in enumerate(to_process):
        logger.info(f"处理 {i + 1}/{len(to_process)}: {movie['title']}")
        result = get_chinese_subtitle(movie, os_config, translate_config)
        results.append({
            "title": movie["title"],
            "year": movie["year"],
            **result,
        })

    return jsonify({"results": results})


@app.route("/stt_scan")
def stt_scan():
    """渲染全库音轨体检 Web 界面"""
    return render_template("stt_scan.html")


@app.route("/api/stt_status")
def api_stt_status():
    """获取当前音轨体检进度"""
    import scan_sound_track
    import time
    scan_sound_track.stt_status["last_ping_time"] = time.time()
    return jsonify(scan_sound_track.stt_status)


@app.route("/api/stt_history")
def api_stt_history():
    """获取所有已完成体检的历史影片"""
    import scan_sound_track
    config = load_config()
    media_paths = config["scanner"]["media_paths"]
    data = scan_sound_track.get_all_processed_movies(media_paths)
    return jsonify(data)


@app.route("/api/rename_subs", methods=["POST"])
def api_rename_subs():
    """执行 Stage 2：清理并规范化字幕命名"""
    import scanner
    config = load_config()
    media_paths = config["scanner"]["media_paths"]
    
    total_processed = 0
    total_deleted = 0
    total_renamed = 0
    all_errors = []
    
    for path in media_paths:
        res = scanner.normalize_subtitles(path)
        if "errors" in res and res["errors"]:
            all_errors.extend(res["errors"])
            
        total_processed += res.get("processed_dirs", 0)
        total_deleted += res.get("deleted_count", 0)
        total_renamed += res.get("renamed_count", 0)
            
    if all_errors:
        return jsonify({
            "success": False,
            "error": "重命名过程发现冲突（目标文件已存在），请手动介入处理：<br>" + "<br>".join(all_errors)
        })
        
    return jsonify({
        "success": True, 
        "message": f"处理完成！删除了 {total_deleted} 个垃圾字幕，重命名了 {total_renamed} 个字幕文件。"
    })


@app.route("/api/stt_start", methods=["POST"])
def api_stt_start():
    """启动全库音轨体检跑批"""
    import scan_sound_track
    import threading
    
    if scan_sound_track.stt_status["is_running"]:
        return jsonify({"error": "跑批任务已经在运行中"}), 400
        
    config = load_config()
    api_key = config["translate"]["groq"]["api_key"]
    media_paths = config["scanner"]["media_paths"]
    
    thread = threading.Thread(target=scan_sound_track.scan_all_movies, args=(api_key, media_paths))
    thread.daemon = True
    thread.start()
    
    return jsonify({"success": True})


@app.route("/api/stt_stop", methods=["POST"])
def api_stt_stop():
    """中止全库音轨体检跑批"""
    import scan_sound_track
    scan_sound_track.stt_status["should_stop"] = True
    return jsonify({"success": True})

if __name__ == "__main__":
    import os
    import threading
    import webbrowser

    config = load_config()
    host = config["web"]["host"]
    port = config["web"]["port"]

    url_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{url_host}:{port}"

    # 因为 app.run 中启用了 debug，Flask 会启动两个进程（主进程+重载进程）
    # 必须在判断之前明确设置 app.debug，否则主进程会误以为 debug=False 从而多开一个 tab
    app.debug = True
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    logger.info(f"启动 Web 服务: {url}")
    app.run(host=host, port=port)
