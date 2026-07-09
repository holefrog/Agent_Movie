# Agent Movie

Agent Movie 是一个智能、全自动的本地媒体库字幕管理系统。专为追求高质量观影体验的用户打造，通过引入 AI（LLM 与 Whisper）解决传统媒体库（如 Emby/Plex）在处理中文字幕时的痛点。系统强调"极简"、"不瞎猜"、"出错即提示"的严格运行逻辑。

---

## 🌟 核心特性

### ✨ 5 Stage 自动化流程

```
Stage 1: 媒体库扫描 → Stage 2: 字幕评估 → Stage 3: 字幕清洗
    ↓
Stage 4: 音轨识别 → Stage 5: 字幕补全
```

#### **Stage 1: 媒体库扫描**

递归扫描配置的媒体路径，建立影片元数据库。

- 自动找到所有 `.nfo` 文件（影片元数据）
- 解析影片信息：标题、年份、IMDB ID
- 定位对应目录的最大视频文件
- 计算 **版本 hash**（区分不同分辨率的同一电影）
- **完成标志**：生成 `sound_track.json` 的 `movie` 和 `video_file` 字段

**何时触发**：Web 应用启动时自动执行  
**耗时**：< 1 秒（仅遍历目录）

---

#### **Stage 2: 字幕现状评估**

检查现有字幕的质量和完整性。

- 扫描视频目录下的所有 `.srt` / `.ass` / `.ssa` 文件
- 检测每个字幕文件的语言（中文/繁体/英文）
- 检测编码（UTF-8/GB18030/Big5）
- 识别脏字幕（<1KB 的垃圾文件）
- 检查命名规范性
- **完成标志**：`subtitles_assessment.done = true`

**何时触发**：Stage 1 完成后自动执行  
**耗时**：< 1 秒/影片  
**阻塞条件**：如发现脏字幕（`has_garbage = true`），Web UI 显示警告

---

#### **Stage 3: 字幕清洗与规范化**

修复和规范化现有字幕。

- **删除垃圾文件**：<1KB 的无用字幕
- **编码转换**：非 UTF-8 的字幕统一转为 UTF-8
- **强制重命名**：命名不规范的字幕，按内容语言重命名为 `[视频名].zh-CN.srt` 或 `.en.srt`
- **完成标志**：`subtitles_cleanup.done = true`

**何时触发**：仅当 Stage 2 检测到脏字幕时（用户点击按钮）  
**耗时**：< 5 秒/影片  
**失败处理**：如文件冲突，记录错误并中止

---

#### **Stage 4: 音轨识别（关键阶段）**

用 Whisper（Groq API）识别视频的原音语言。

- 从视频的 7 个时间点各提取 15 秒音频（采样点：25%, 50%, 75%, 15%, 85%, 35%, 65%）
- 调用 Groq Whisper API 识别每个片段的语言
- **一旦识别到 3 个有效结果就停止**（无需扫完全部）
- **宁缺毋滥**：3 个采样都失败 → 立即报错，不进行盲目 Fallback
- **完成标志**：`audio_tracks.done = true` + `primary_language` 确定

**何时触发**：用户点击按钮 或 配置为自动运行  
**耗时**：30-120 秒/影片（网络+API 响应时间）  
**后台执行**：异步线程，支持实时进度显示和中途中止  
**错误处理**：
  - API 限流 → 指数退避重试
  - 网络超时 → 最多重试 10 次
  - 超出重试限制 → 记录详细错误信息

---

#### **Stage 5: 字幕补全**

根据音轨识别结果，智能补全中文字幕。

**处理逻辑**：
```
判断 is_chinese_audio：
  ├─ true（中文原音）
  │  └─ method = "skipped"（已是中文，无需字幕）
  │
  ├─ false（外语原音）
  │  ├─ 检查：已有中文字幕？
  │  │  └─ yes → method = "existed"（跳过）
  │  │  └─ no → 尝试从 OpenSubtitles 下载中文
  │  │
  │  ├─ 下载成功？
  │  │  └─ yes → method = "downloaded"
  │  │  └─ no → 检查：有英文字幕？
  │  │
  │  └─ 有英文字幕？
  │     └─ yes → 用 LLM 翻译 → method = "translated", translator = "gemini/groq/mistral"
  │     └─ no → method = "not_found", error = "无可用字幕"
```

- **记录翻译服务**：如果进行了 LLM 翻译，记录使用的服务（Gemini/Groq/Mistral 等）
- **完成标志**：`subtitle_completion.done = true`

**何时触发**：Stage 4 完成后自动执行 或 用户点击按钮  
**耗时**：10-60 秒/影片（取决于是否需要下载/翻译）  
**翻译策略**：
  - 批处理 50 条字幕一批
  - 批次间延迟 2 秒（避免限流）
  - 任何失败即中止（不继续重试后续批次）

---

## 📊 处理状态管理

### 统一数据源：`sound_track.json`

每个视频文件所在的目录都有一个 `sound_track.json` 文件，完整记录该影片在 5 个 Stage 中的处理状态。

**文件位置**：
```
/media/Movie/
├── Taxi (1998)/
│   ├── Taxi.1998.720p.mkv
│   ├── Taxi.1998.nfo
│   └── sound_track.json          ← 这个文件
```

### JSON 结构

```json
{
  "version": 2,
  "last_updated": "2026-07-07T16:35:00Z",
  
  // ========== Stage 1 ==========
  "movie": {
    "title": "Taxi",
    "year": 1998,
    "imdb_id": "tt0118694",
    "version_hash": "d1e5f8a2b3c4",    // 版本标识（文件大小+时长的 hash）
    "version_name": "720p"               // 可选的可读版本名
  },
  
  "video_file": {
    "path": "/media/Movie/Taxi (1998)/Taxi.1998.720p.mkv",
    "duration_seconds": 5400,
    "size_bytes": 1073741824
  },
  
  // ========== Stage 2 ==========
  "subtitles_assessment": {
    "done": true,                        // 是否已完成
    "has_chinese": true,                 // 是否有中文字幕
    "has_english": true,                 // 是否有英文字幕
    "has_garbage": false,                // 是否有脏字幕（<1KB）
    "error": null                        // 错误信息（无错时为 null）
  },
  
  // ========== Stage 3 ==========
  "subtitles_cleanup": {
    "done": true,                        // 是否已完成
    "files_deleted": 1,                  // 删除的文件数
    "files_renamed": 0,                  // 重命名的文件数
    "error": null
  },
  
  // ========== Stage 4 ==========
  "audio_tracks": {
    "done": true,                        // 是否已完成
    "primary_language": "en",            // 原音语言标识
    "is_chinese_audio": false,           // 是否为中文原音
    "error": null
  },
  
  // ========== Stage 5 ==========
  "subtitle_completion": {
    "done": true,                        // 是否已完成
    "method": "downloaded",              // existed / downloaded / translated / skipped / not_found
    "translator": "gemini",              // 如果翻译，记录用的 LLM 服务
    "chinese_subtitle": "Taxi.1998.zh-CN.srt",  // 最终的中文字幕文件名
    "error": null
  }
}
```

### 关键字段说明

| 字段 | 含义 | 示例值 |
|------|------|--------|
| `version_hash` | 版本唯一标识（区分 1080p vs 720p） | `"d1e5f8a2b3c4"` |
| `done` | 该 Stage 是否已完成 | `true` / `false` |
| `error` | 错误信息（无错为 null） | `"无法识别音轨：重试 10 次后仍失败"` |
| `method` | Stage 5 的处理方式 | `"downloaded"`, `"translated"`, `"skipped"` |
| `translator` | 使用的翻译服务 | `"gemini"`, `"groq"`, `"mistral"` |

### 处理逻辑

**自动跳过已完成的 Stage**：
```python
# 伪代码
if sound_track.json 存在:
    if audio_tracks.done == true:
        跳过 Stage 4（音轨识别）
    
    if subtitles_cleanup.done == true:
        跳过 Stage 3（字幕清洗）
```

**错误恢复**：
```
遇到错误时：
  └─ 查看 sound_track.json 的相应字段的 error 信息
  └─ 手动删除 sound_track.json 文件
  └─ 重新访问 Web UI，系统从 Stage 1 重新处理
```

**手动修正**：
```json
// 例如：某影片的音轨识别错了
// 编辑 sound_track.json，改正 primary_language
"audio_tracks": {
  "done": true,
  "primary_language": "zh",    // 手动改为正确值
  "is_chinese_audio": true
}
// 保存后，Web UI 立即认可该影片已完成
```

---

## 🌐 Web 用户界面

### 实时状态监控

打开 http://localhost:8899 后，Web UI 每 2 秒自动轮询一次后端状态，实时显示：

```
┌─────────────────────────────────────────────┐
│ 🎬 中文字幕获取                             │
├─────────────────────────────────────────────┤
│                                             │
│ ✅ Stage 1: 媒体库扫描                     │
│    已扫描 150 部影片                       │
│                                             │
│ ✅ Stage 2: 字幕现状评估                   │
│    字幕规范: 100 部 | 需清洗: 30 部        │
│                                             │
│ ⚠️  Stage 3: 字幕清洗（需要执行）         │
│    [🧹 一键清洗字幕]  按钮                │
│                                             │
│ ⏳ Stage 4: 音轨识别（正在进行中）        │
│    [████████░░░░░░░░░░] 45%               │
│    已识别 45/150 | 中文: 25 | 外语: 15    │
│    当前: Taxi (1998)                       │
│    [⏹ 中止]  按钮                         │
│                                             │
│ Stage 5: 等待中                            │
│    等待前置阶段完成...                     │
│                                             │
└─────────────────────────────────────────────┘
```

### API 接口列表

```
GET  /api/page_status              获取当前页面状态
POST /api/stage3_cleanup           触发 Stage 3 清洗
POST /api/stage4_stt_start         开始 Stage 4 音轨识别
POST /api/stage4_stt_stop          中止 Stage 4 音轨识别
GET  /api/stage4_progress          查询 Stage 4 进度
POST /api/stage5_completion        触发 Stage 5 补全
```

---

## ⚙️ 部署与配置

### 快速开始（仅需 3 步）

#### **Step 1: 创建敏感信息配置**

```bash
cd deploy

cat > group_vars/secrets.yml << 'EOF'
---
# OpenSubtitles 凭证
opensubtitles_api_key: "your_opensubtitles_api_key"
opensubtitles_username: "your_opensubtitles_username"
opensubtitles_password: "your_opensubtitles_password"

# LLM API Keys（按需选择）
gemini_api_key: "your_gemini_api_key"
groq_api_key: "your_groq_api_key"
mistral_api_key: "your_mistral_api_key"
openai_api_key: "your_openai_api_key"
nvidia_api_key: "your_nvidia_api_key"
EOF
```

#### **Step 2: 运行 Ansible 部署**

```bash
bash deploy.sh
```

这个脚本会自动：
- 检查 Ansible 是否安装
- 验证 secrets.yml 是否存在
- 创建 Python 虚拟环境
- 安装依赖包
- 生成 settings.toml 配置文件

#### **Step 3: 启动服务**

```bash
cd ~/Programs/Agent_Movie
bash run.sh
```

Web 应用会自动打开，地址为 http://localhost:8899

---

### 配置文件详解

#### **`group_vars/all.yml`**（通用配置，可安全提交到 Git）

```yaml
---
# 部署目标
deploy_user: david
deploy_dir: /home/david/Programs/Agent_Movie

# 扫描路径（支持多个）
media_paths:
  - /home/david/NAS_NFS/Media/Video/Movie/
  # - /mnt/other_media/

# 视频文件扩展名
video_exts:
  - ".mp4"
  - ".mkv"
  - ".avi"
  - ".wmv"
  - ".flv"
  - ".mov"
  - ".ts"
  - ".rmvb"

# 字幕文件扩展名
sub_exts:
  - ".srt"
  - ".ass"
  - ".ssa"

# 选择翻译服务（gemini / openai / mistral / groq / nvidia）
translate_provider: mistral

# 翻译通用参数
translate_timeout: 60          # API 超时（秒）
translate_batch_size: 50       # 每批翻译多少条字幕
translate_temperature: 0.3     # LLM 温度参数

# 模型选择
gemini_translate_model: gemini-2.5-flash
openai_translate_model: gpt-4o-mini
mistral_translate_model: mistral-large-latest
groq_translate_model: llama-3.3-70b-versatile
nvidia_translate_model: meta/llama-3.3-70b-instruct

# Web 服务
web_host: "0.0.0.0"
web_port: 8899
```

#### **`group_vars/secrets.yml`**（敏感信息，.gitignore）

```yaml
---
# OpenSubtitles API
opensubtitles_api_key: "xxx"
opensubtitles_username: "xxx"
opensubtitles_password: "xxx"

# Groq（用于 Whisper STT 和翻译）
groq_api_key: "xxx"

# Gemini（可选翻译）
gemini_api_key: "xxx"

# Mistral（可选翻译）
mistral_api_key: "xxx"

# OpenAI（可选翻译）
openai_api_key: "xxx"

# Nvidia（可选翻译）
nvidia_api_key: "xxx"
```

---

## 🔧 开发指南

### 项目结构

```
Agent_Movie/
├── app.py                      # Flask 应用，路由处理
├── scanner.py                  # Stage 1-3：扫描、评估、清洗
├── scan_sound_track.py         # Stage 4：音轨识别（后台线程）
├── subtitle.py                 # Stage 5：字幕补全
├── metadata_manager.py         # sound_track.json 管理
├── state_machine.py            # 页面状态计算
├── retry.py                    # 指数退避重试工具
├── settings.toml               # 配置文件（Ansible 生成）
├── requirements.txt            # Python 依赖
├── run.sh                      # 启动脚本
├── venv/                       # Python 虚拟环境
└── web/
    ├── index.html             # 主界面
    ├── loading.html           # 加载页面
    ├── stt_scan.html          # Stage 4 监控页面（可选）
    └── style.css              # 样式表
```

### 核心模块职责

| 模块 | 职责 | 关键函数 |
|------|------|---------|
| `scanner.py` | Stage 1-3：扫描、评估、清洗字幕 | `scan_directory()`, `assess_subtitles()`, `normalize_subtitles()` |
| `scan_sound_track.py` | Stage 4：音轨识别（后台异步） | `scan_all_movies()`, `build_language_nfo_for_video()` |
| `subtitle.py` | Stage 5：OpenSubtitles 下载 + LLM 翻译 | `get_missing_subtitle()`, `translate_subtitle()` |
| `metadata_manager.py` | 读写 `sound_track.json` | `Metadata` 类的 `set_*()` 方法 |
| `state_machine.py` | 计算页面状态 | `compute_page_state()` |
| `retry.py` | 指数退避重试工具 | `with_retry()` |
| `app.py` | Flask Web 应用 | `@app.route()` 各路由 |

### 编码规范

遵循 [.clinerules](.clinerules)：
- 所有思考和代码注释必须用中文
- 拒绝上帝类：模块只干一件事
- 错误处理：打印人能看懂的中文错误提示
- 嵌套层级：if-else 最多 3 层，超过用卫语句提前返回

---

## 💡 使用注意事项

### 性能参考

| Stage | 耗时 | 备注 |
|-------|------|------|
| Stage 1 | < 1 分钟 | 150 部影片 |
| Stage 2 | < 1 分钟 | 150 部影片 |
| Stage 3 | < 5 分钟 | 30 部需清洗 |
| Stage 4 | 15-30 分钟 | 150 部影片，依赖 Groq API |
| Stage 5 | 5-15 分钟 | 150 部影片，部分需翻译 |

### API 限流处理

系统内置了自动的限流应对机制：

- **OpenSubtitles**：1 req/sec（代码自动延迟 1.5 秒）
- **Groq/Gemini**：采用指数退避，最多重试 10 次
- **LLM 翻译**：批处理 50 条/批，批次间延迟 2 秒

### 多版本电影处理

同一部电影如有多个版本（1080p、720p 等），系统会自动区分：

```
/media/Movie/
├── Taxi (1998) 1080p/
│   ├── Taxi.1998.1080p.mkv
│   └── sound_track.json (version_hash: "a1b2c3d4e5f6")
│
└── Taxi (1998) 720p/
    ├── Taxi.1998.720p.mkv
    └── sound_track.json (version_hash: "d1e5f8a2b3c4")
```

- `version_hash` 基于文件大小和时长计算
- 不同版本独立处理，互不影响

### 手动修正影片信息

如果某个影片的音轨识别错了：

```bash
# 编辑对应目录的 sound_track.json
# 修改 audio_tracks.primary_language 字段

nano /media/Movie/Taxi\ \(1998\)/sound_track.json

# 修改后保存，Web UI 立即认可该影片已完成
```

---

## 🚀 快速故障排查

### 常见问题

#### **Q1: Web UI 打开时报错**

```
Error: 找不到 settings.toml
```

**原因**：Ansible 部署失败  
**解决方案**：
```bash
cd deploy
# 检查 group_vars/secrets.yml 是否存在
ls -la group_vars/secrets.yml

# 重新运行部署
bash deploy.sh
```

#### **Q2: Stage 4 一直卡在某部影片不动**

**原因**：Groq API 限流 或 网络问题  
**解决方案**：
1. 查看后台日志，找出错误信息
2. 删除该影片的 `sound_track.json`
3. 点击"⏹ 中止"按钮停止识别
4. 重新点击"🎵 开始识别"重试

#### **Q3: 字幕清洗后还是乱码**

**原因**：字幕原本编码损坏  
**解决方案**：
1. 手动删除该字幕文件
2. 删除 `sound_track.json`
3. 从 Stage 1 重新开始

#### **Q4: 某影片识别的音轨语言错了**

**原因**：Whisper 在短音频片段上误判  
**解决方案**：
```bash
# 编辑 sound_track.json，手动改正
nano /path/to/sound_track.json

# 修改这一行：
"primary_language": "en",    # 原来是 "en"，改为正确的语言

# 保存后，Web UI 立即认可该影片为正确值
```

#### **Q5: 翻译出来的字幕质量不好**

**原因**：LLM 模型选择或 temperature 参数不合适  
**解决方案**：
```yaml
# 修改 group_vars/all.yml
translate_temperature: 0.2    # 改为更低（更严谨）或更高（更创意）
translate_provider: groq      # 改为其他 LLM 服务试试
translate_batch_size: 30      # 改为更小的批次（可能提升质量）

# 重新部署
bash deploy.sh

# 删除该影片的 sound_track.json，重新处理
```

### 日志位置

```
~/Programs/Agent_Movie/
├── app.log           # Flask 应用日志
├── scan_sound_track.log  # Stage 4 日志
└── subtitle.log      # Stage 5 日志
```

查看日志：
```bash
tail -f ~/Programs/Agent_Movie/app.log
```

---

## 📅 未来规划（Roadmap）

- [ ] **Sync Check**：AI 提取视频首尾台词，与下载的字幕时间轴对比，自动计算偏移量
- [ ] **Mismatch Detection**：抽样对比视频原音转写内容和外部字幕的语义匹配度，杜绝"错版"现象（如导演剪辑版 vs 剧场版）
- [ ] **TV Shows 支持**：扩展到电视剧集的批量处理
- [ ] **Web 实时日志**：在 Web UI 显示每个操作的详细日志流
- [ ] **智能重试**：记住常失败的影片，优先进行特殊处理

---

## 🎯 设计哲学

### 极简主义（Minimalism）
- 只记录必要信息，拒绝过度设计
- 文件即数据源，无需复杂数据库

### 宁缺毋滥（Better Nothing Than Wrong）
- Stage 4 识别失败时立即报错，不进行错误的 Fallback
- 用户看到错误提示，而不是被错误的处理所迷惑

### 容错设计（Fault Tolerant）
- 错误信息明确记录
- 用户可以随时删除 `sound_track.json` 重新开始

### 实时反馈（Real-time Feedback）
- Web UI 每 2 秒更新一次状态
- 用户能实时看到进度

---

## 📝 许可证

MIT License

---

## 🤝 贡献指南

欢迎提交 Issue 或 Pull Request！

### 快速反馈

遇到问题？最快的解决方案：
1. 查看错误信息
2. 删除对应的 `sound_track.json`
3. 重新运行该 Stage

### 开发建议

修改代码时遵循 [.clinerules](.clinerules)：
- 中文注释，清楚表达意图
- 模块单一职责
- 卫语句降低嵌套

---

## 📧 问题排查

### 无法连接到 Groq API

```
错误：HTTPError: 429 Too Many Requests
```

**原因**：API 限流  
**解决**：系统已自动处理（指数退避重试），如仍失败，检查：
```bash
# 1. API Key 是否正确
cat ~/Programs/Agent_Movie/settings.toml | grep groq_api_key

# 2. 网络连接
ping api.groq.com

# 3. Groq 服务状态
# 访问 https://status.groq.com/
```

### 无法连接到 OpenSubtitles

```
错误：ConnectionError: Failed to connect
```

**原因**：网络问题 或 API 维护  
**解决**：
```bash
# 1. 检查 API Key
cat ~/Programs/Agent_Movie/settings.toml | grep opensubtitles

# 2. 测试网络
curl -I https://api.opensubtitles.com/api/v1/ping

# 3. 手动下载字幕
# 访问 https://www.opensubtitles.com/ 手动搜索和下载
```

---

## ✅ 验证安装

部署完成后，验证一切正常：

```bash
# 1. 检查配置文件
ls -la ~/Programs/Agent_Movie/settings.toml

# 2. 启动服务
cd ~/Programs/Agent_Movie
bash run.sh

# 3. 打开浏览器
# http://localhost:8899

# 4. 观察日志
tail -f ~/Programs/Agent_Movie/app.log
```

应该看到：
```
🚀 启动 Web 服务...
2026-07-07 16:35:00 [INFO] app.py: 启动 Web 服务: http://0.0.0.0:8899
```

---

**祝你使用愉快！有问题随时反馈。** 🎬
