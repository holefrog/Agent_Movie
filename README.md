# Agent Movie

Agent Movie 是一个智能、全自动的本地媒体库字幕管理系统。专为追求高质量观影体验的用户打造，通过引入 AI（LLM 与 Whisper）解决传统媒体库（如 Emby/Plex）在处理中文字幕时的痛点。系统强调“极简”、“不瞎猜”、“出错即提示”的严格运行逻辑。

## 🌟 核心功能

### Stage 1: 媒体库扫描
系统首先扫描媒体库目录，解析影片元数据。
- **NFO 解析**：递归扫描媒体库，解析 *.nfo 文件提取影片信息（标题、年份、IMDB ID）
- **视频文件识别**：自动识别目录中的主视频文件（按大小选择）
- **版本信息提取**：支持电影版本字段（如导演剪辑版、剧场版）
- **状态持久化**：分析结果保存在影片目录的 `[视频名].json` 中

### Stage 2: 字幕评估
在处理字幕前，系统先评估现有字幕的状态。
- **语言检测**：读取字幕文件内容，智能判断语言（简体/繁体/英文）
- **编码检测**：检测字幕文件编码格式
- **垃圾识别**：识别体积过小的广告字幕、冗余配置文件等
- **脏数据标记**：标记需要清洗的字幕文件

### Stage 3: 字幕清洗
处理完评估后，自动清理和规范化字幕文件。
- **垃圾清理**：一键清除体积过小的广告字幕文件、老旧的冗余配置文件等
- **按内容重命名**：无视文件原有的后缀瞎标，程序直接读取字幕文件内容。只要内容里含有简体中文，强制重命名为 `[电影原名].zh-CN.[格式]`；含有繁体则重命名为 `.zh-TW`
- **编码统一**：强制转换所有字幕文件为 UTF-8 编码

### Stage 4: AI 音轨识别 (STT Scan)
在处理字幕前，系统必须先准确摸清库里所有电影的真实发音（防止给纯中文电影强行下外挂中文字幕）。
- **智能采样与 Whisper 识别**：调用 FFmpeg 从视频中提取多个 15 秒片段（备选采样点 `[25%, 50%, 75%, 15%, 85%, 35%, 65%]`），并通过 Groq 提供的 Whisper 模型识别发音。
- **宁缺毋滥的严格模式**：一旦识别到 3 个有效语音片段即见好就收。如果所有片段均为纯音乐或无声导致无法识别，**系统将抛出异常并中止任务**，强制要求人工介入，坚决不进行盲目 Fallback。
- **监控大屏**：提供独立的 Web 监控中心，可实时查看大盘进度（“总进度: 已处理 / 全库数量”）和处理日志。
- **持久化档案**：分析结果保存在影片目录的 `[视频名].json` 中，包含每条音轨的语言标记。

### Stage 5: 字幕补全（含同步检测与错版甄别）
只针对确认需要中文字幕的外语片执行操作：
- **OpenSubtitles 精准下载**：根据目录 `*.nfo` 元数据中的 IMDB ID 匹配，确保版本对应
- **AI 同步检测**：使用 Whisper 提取视频音频并识别首尾台词的时间轴，与下载的外部字幕文件进行比对。判断外部字幕时间轴是否超前或滞后，并自动计算偏移量（Offset）来校准时间轴
- **AI 错版甄别**：下载字幕后，AI 会抽样对比“视频原音转写内容”和“外部字幕文本内容”的语义匹配度。杜绝“挂羊头卖狗肉”现象（如下载的是导演剪辑版字幕，但视频是剧场版导致后续完全错位，或者纯粹下载到了另一部电影的同名字幕）。如发现严重错版，自动废弃该字幕并重新拉取其他版本
- **大模型长文本翻译**：对于只下到英文字幕的影片，调用配置的 LLM (Gemini / Groq 等) 对外挂字幕进行高精度上下文连贯翻译

### ⚙️ 部署与架构
- **Ansible 一键部署**：使用 `deploy.sh` 配合 Ansible playbook 自动化环境搭建（Python venv 构建、依赖安装、文件分发）
- **后台常驻服务**：Flask Web 提供交互入口，后台静默执行任务并通过心跳机制防止僵尸进程。所有配置均在 `settings.toml` 中统一维护
- **状态机驱动**：使用 `[视频名].json` 作为唯一状态源，每个 Stage 有明确的 done/error 字段，支持渐进式处理和错误追踪
- **模块化设计**：职责解耦，每个模块单一职责，易于维护和扩展

---

## 📅 未来规划

- [ ] **电视剧集支持**：
  - 增加对电视剧集 (TV Shows / Episodes) 的批量智能支持
  
- [ ] **更多翻译器支持**：
  - 支持更多 LLM 提供商和翻译模型

---

## 💡 开发避坑指南 (Pitfalls)

在对接 **OpenSubtitles API (opensubtitlescom)** 期间，踩过以下严重深坑，特此记录以防后人再次踩雷：

1. **极其严苛的 429 速率限制 (Too Many Requests)**
   - **坑点**：每次调用 `OpenSubtitles.login()` 算一次请求。如果在循环遍历多部电影时，每次都实例化客户端去登录，会瞬间触发 `1 req/sec per IP` 的限流封禁。
   - **解法**：必须使用**全局单例 (Singleton)** 缓存已登录的客户端对象；并且在 `login`、`search`、`download` 动作之前强制 `time.sleep(1.5)`，外层包裹指数退避的 `@with_retry` 机制。

2. **自相矛盾的语种传参 (`Invalid language code`)**
   - **坑点**：OpenSubtitles 的 REST API 底层其实支持 `zh-cn,zh-tw` 的逗号拼接查询，但其官方 Python 封装库 `opensubtitlescom` 里却写死了断言，直接把包含逗号的字符串判定为非法并抛出异常。
   - **解法**：调用 `search()` 前，手动通过正则 `re.split(r'[,|;]+', lang_code)` 将多语言拆散成独立的列表，然后在代码里写 for 循环挨个发起纯粹单一语种的搜索请求。

3. **迷惑的返回值类型 (`Subtitle object is not subscriptable`)**
   - **坑点**：当调用 `ost.download_and_parse()` 时，库并不返回字典或纯文本，而是返回自己定义的 `opensubtitlescom.srt.Subtitle` 对象的列表。如果后续尝试用 `['index']` 下标读取或直接 `write_text` 写入，会导致整个程序崩溃。
   - **解法**：坚决弃用 `download_and_parse()`，改为调用最底层的 `ost.download()`。它会原封不动地返回原始 `.srt` 文件的字节流 (`bytes`)，直接 `write_bytes()` 落盘即可，完美避开乱码和格式解析问题。

---

## 🏛️ 架构设计理念 (Architecture)

### 状态机驱动的渐进式处理

Agent_Movie 采用基于状态机的5阶段架构，每个阶段有明确的输入、处理和输出：

1. **状态持久化**：每个影片目录维护一个 `[视频名].json` 文件，记录该影片在各个阶段的状态
2. **渐进式处理**：按业务流程顺序执行，前序Stage阻塞后续Stage
3. **错误明确**：所有错误记录在 error 字段，前端直接显示给用户
4. **幂等性**：各Stage可重复执行，结果一致，已完成的Stage会自动跳过
5. **本地化**：无数据库依赖，基于文件系统，简单可靠

### 绝对无状态的实时响应 (Stateless & Real-time)

与 Emby / Plex 等需要庞大 SQLite 数据库维护状态的传统媒体中心不同，Agent_Movie 的核心设计理念是**“纯 I/O 实时驱动”**。

`app.py` 扮演着一个经典的死循环阻塞型 Web Server（Blocking Server）角色：
1. **服务常驻**：执行 `./run.sh` 时，Python 线程被 `app.run()` 永久挂起，持续监听前端页面的指令
2. **每次刷新即“全盘重扫”**：
   - 因为没有数据库“记仇”，你在浏览器按下 `F5` 刷新的瞬间，系统会直接拉起后台的扫描脚本（`scanner.py`）
   - 脚本顺着配置的媒体库路径，实时且暴力地去读取物理硬盘上每一个 `.nfo`，嗅探每一个 `.srt` 的存在
3. **所见即所得**：无论你是通过本程序下载了字幕，还是手动往文件夹里拖入了一个新字幕，无需点击“同步媒体库”，只要刷新网页，页面就会瞬间根据硬盘的真实物理变动来剔除或更新列表。永不同步错误！

## 📊 状态文件格式

每个影片目录下的状态文件采用与视频文件同名的命名方式，便于识别和管理：

- **视频文件**: `1917 (2019) 1080p AAC.mp4`
- **状态文件**: `1917 (2019) 1080p AAC.json`

**命名规则**: 状态文件名 = 视频文件名（不含扩展名） + `.json`

### 状态文件内容示例

```json
{
  "version": 2,
  "last_updated": "2026-07-09T14:00:00Z",
  "movie": {
    "title": "电影名称",
    "year": "1998",
    "imdb_id": "tt0120915",
    "version": "Director's Cut"
  },
  "video_file": {
    "path": "/path/to/video.mkv",
    "duration_seconds": 7200
  },
  "subtitles_assessment": {
    "done": true,
    "has_chinese": false,
    "has_english": true,
    "has_garbage": true,
    "has_internal_chinese_sub": false,
    "error": null
  },
  "subtitles_cleanup": {
    "done": true,
    "files_deleted": 2,
    "files_renamed": 1,
    "error": null
  },
  "audio_tracks": {
    "done": true,
    "primary_language": "en",
    "is_chinese_audio": false,
    "tracks": [
      {"index": 0, "lang": "en"},
      {"index": 1, "lang": "en"}
    ],
    "error": null
  },
  "subtitle_completion": {
    "done": true,
    "method": "translated",
    "translator": "groq",
    "chinese_subtitle": "Taxi (1998) Director's Cut.zh-CN.srt",
    "sync_offset": 0.5,
    "mismatch_detected": false,
    "error": null
  }
}
```

### 字段说明
- **done**: 该Stage是否已完成
- **error**: 该Stage的错误信息（null表示无错误）
- **movie.version**: 电影版本字段，区分同一电影的不同版本
- **subtitles_assessment.has_internal_chinese_sub**: 是否有内置中文字幕（通过内容检测，null表示未检测，false表示无，true表示有）
- **subtitle_completion.sync_offset**: 同步检测计算的时间偏移量
- **subtitle_completion.mismatch_detected**: 错版甄别结果
