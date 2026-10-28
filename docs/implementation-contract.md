# 實作契約（v2.0 單螢幕介面＋v1.0 既有介面）

更新日期：2026-10-28

狀態：第 0 節是已實作的 [系統架構 v2.0](system-architecture.md) 單螢幕／手寫筆介面；第 1 節以後保留 v1.0 的介面與模組約定。兩者衝突時以第 0 節為準。平板頁、三個展示角色、WebSocket 與三螢幕計時已不在 v2.0 流程中使用，程式仍保留供對照，舊網址轉址到 `/exhibition`。

原始依據：[v1.0 架構封存](archive/system-architecture-v1.0.md)。架構文件描述「做什麼」，本文件規定「怎麼對接」。

## 0. v2.0 單螢幕介面（已實作）

### 0.1 頁面與模組

| 項目 | 位置 | 說明 |
| --- | --- | --- |
| 展覽頁 | `/exhibition`（`frontend/src/exhibition/`） | 單一全螢幕頁：輪播、繪圖（內嵌 `src/draw/App`）、本人首播；每 500 毫秒輪詢主機狀態 |
| 後臺 | `/admin` | 沿用 v1.0，新增三段秒數與 K，監看改為單一展覽頁 |
| 舊頁面 | `/draw`、`/display/{role}` | 轉址到 `/exhibition` |
| 狀態機 | `backend/exhibition.py` | 唯一的模式、首播鎖與分段計時來源；由 Web 程序內的 `ExhibitionController` 每 200 毫秒推進 |
| API | `backend/api/exhibition.py` | 見 0.2 |
| 分段紀錄 | `backend/schema/30_exhibition.sql` | `exhibition_segments`：每段一筆，`kind` 為 `personal`／`premiere`／`history`，`result` 為 `completed`／`interrupted` |

狀態存在 `runtime_state.exhibition_v2`：`mode`（`gallery`／`drawing`／`personal`）、`stage`（`visitor`／`ai`／`artist`）、`kind`、`item_id`、`owner_submission_id`、`session_id`、`resume_item_id`、`started_at`、`durations`（本件開始時綁定的三段秒數）、`revision`、`client_id`、`last_seen`。

### 0.2 HTTP API

| 方法與路徑 | 說明 |
| --- | --- |
| `GET /api/exhibition` | 推進一次狀態並回傳：`revision, mode, stage, kind, can_start, locked, connected, session_id, submission_id, playback_item_id, receipt_order, image_url, failure_text, artwork, started_at, seconds, waiting_for_ai, next_session_available_at, progress` |
| `POST /api/exhibition/presented` | `{revision, client_id, visible}`：圖片已載入並實際顯示；也當作每 2 秒的心跳。版本不符時忽略；另一個視窗仍在線時回 409 `display_in_use` |
| `POST /api/sessions` | 一般輪播中的筆尖接觸才呼叫；本人首播未完成時回 409 `exhibition_locked` |
| `POST /api/admin/exhibition/stop?source=admin\|escape` | 暫停接待並要求啟動器結束；`escape` 為展覽頁五秒內五次 Esc。這次暫停記在 `runtime_state.intake_paused_for_stop`，下次 Web 啟動時自動恢復接待；結束前工作人員已手動暫停的則維持暫停 |

### 0.3 規則摘要

- 一般輪播：先播尚未完成首播的舊作品（`premiere`），沒有時循環最近 K 件已播作品（`history`），每件「觀眾 → AI → 原圖」。
- 筆尖接觸（`pointerType` 為 `pen`，開發時允許滑鼠左鍵）才開始；懸停與移動不觸發。喚醒的那一下不留筆跡。
- 送出非空白作品後立即進入 `personal`：觀眾段至少滿秒數且 AI 有結果才換段，三段都實際顯示滿秒數才解鎖。解鎖時筆尖仍按著不會開新場。
- 每段以 `presented` 回報開始計時；超過 8 秒沒有回報或回報不可見時，該段記為 `interrupted` 並從頭重計。
- 空白、放棄與磁碟捨棄場次回到一般輪播，保留 v1.0 的例外換場秒數（`turnaround_seconds`）。
- 第一次以 v2.0 開啟已有作品的舊資料庫時，先備份到 `data/backups/before-v2-*.sqlite3`。

### 0.4 設定

`settings` 新增 `visitor_seconds`、`ai_seconds`、`artist_seconds`（預設各 10，1–600）與 `gallery_limit`（K，預設 10，1–1000）。`display_min_seconds` 只供舊流程使用。

### 0.5 五秒內連按五次 Esc 結束展覽

- 展覽頁：以 window 層級攔截 `keydown`，長按的重複事件不計；五秒內湊滿五次時先保存草稿、顯示「對話暫歇。」，再呼叫 `POST /api/admin/exhibition/stop?source=escape`。
- 展覽頁不進入 HTML5 全螢幕（全螢幕由啟動器的 kiosk 視窗負責），否則瀏覽器會攔下 Esc。
- 啟動器備援（`launcher/escape_watch.py`）：以 `GetAsyncKeyState` 偵測，只在前景視窗屬於展覽瀏覽器時計數；湊滿五次後等 1.5 秒（讓頁面保存草稿）再結束。Web 服務沒有回應時也能關閉。`--no-displays` 時不啟用。
- 日誌事件：`admin.exhibition_stop`（`source=escape`）、`system.launcher_escape`。

### 0.6 標準關閉與再次開展

| 方式 | 行為 |
| --- | --- |
| 展覽視窗按 Alt+F4（或以其他方式正常關閉） | 啟動器持有瀏覽器主程序的控制代碼；主程序以代碼 0 結束即視為有人關閉視窗，依正常流程結束整個展覽。非 0（當掉、被強制結束）或主程序消失時，照舊在 8 秒啟動緩衝後補開。Edge 啟動程序會把視窗交給另一個主程序後以 0 結束，監看時排除它（`browser.pick_main_process`）。 |
| `stop-exhibition.bat`（`python -m launcher --stop`） | 在 `data/tmp/launcher-stop.request` 留下結束要求，啟動器每 0.5 秒檢查一次（等待 Web 就緒期間也會檢查），之後依正常流程結束；BAT 等到單一執行鎖釋放才回報完成。`--stop-timeout`（預設 30 秒）內沒有結束時，強制結束啟動器與遺留程序並回傳 1。沒有啟動器在執行時，只清除遺留程序與展覽視窗。 |
| 五秒內五次 Esc、後臺「結束展覽」 | 見 0.5 與 0.2。 |

為了讓強制結束（工作管理員、當掉、關閉主控台）之後仍能再次開展：

- 啟動器建立 Windows 工作物件（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`），Web、Worker 與正式展覽視窗的瀏覽器主程序加入其中；啟動器一結束，系統就一併結束它們。狀態頁視窗不加入，啟動失敗時仍留在螢幕上說明原因。
- 取得單一執行鎖後、環境檢查前，先清除使用同一份資料目錄的 `python -m backend`／`python -m backend.worker`（依程序的 `AP_DRAWART_ROOT`／`AP_DRAWART_DATA_DIR` 與工作目錄判斷），避免舊程序占用連線埠或重複處理 AI 工作。
- 日誌事件：`system.display_window_closed`、`system.display_window_reopen`（含 `exit_code`）、`system.launcher_request`（`request=stop_file`）、`system.leftover_cleanup`、`system.launcher_force_stop`、`system.child_job`。

## 1. 模組所有權

每個工作包只修改自己擁有的檔案。需要改動共用檔案或別人的檔案時，不要直接修改，在最終回報中列出需求，由整合者處理。可以在自己的目錄內新增檔案。

| 工作包 | 擁有的檔案 |
| --- | --- |
| 整合者（共用） | `backend/__init__.py`、`backend/__main__.py`、`backend/app.py`、`backend/config.py`、`backend/db.py`、`backend/timeutil.py`、`backend/logclient.py`、`backend/schema/10_core.sql`、`backend/api/errors.py`、`backend/api/pages.py`、`backend/services/state.py`、`backend/services/settings.py`、`backend/ai_providers/base.py`、`tests/conftest.py`、`requirements.txt`、`frontend/package.json`、`frontend/vite.config.ts`、`frontend/tsconfig.json`、`frontend/src/shared/*`、`docs/*`、`.gitignore` |
| backend-core | `backend/api/public.py`、`backend/api/admin.py`、`backend/api/netguard.py`、`backend/services/`（上列共用檔除外，例如 `artworks.py`、`sessions.py`、`submissions.py`、`disk.py`、`health.py`）、`tests/test_core_*.py` |
| playback | `backend/playback/*`、`backend/api/display.py`、`backend/api/ws.py`、`backend/schema/20_playback.sql`、`tests/test_playback_*.py` |
| worker-ai | `backend/worker/*`、`backend/ai_providers/*`（`base.py` 除外）、`tests/test_worker_*.py` |
| launcher-logs | `launcher/*`、`backend/logsink/*`、`backend/platform/*`、`start-exhibition.bat`、`tests/test_launcher_*.py` |
| frontend-draw | `frontend/draw/index.html`、`frontend/src/draw/*` |
| frontend-display | `frontend/display/index.html`、`frontend/src/display/*` |
| frontend-admin | `frontend/admin/index.html`、`frontend/src/admin/*` |

目前已有對應模組實作；上表保存當時的工作包分工。v2.0 遷移以實際程式及新版契約為準，既有內容須先核對後再修改。

## 2. 共同規則

- 不安裝任何套件（不執行 `pip install`、`npm install`）。已安裝：FastAPI、Uvicorn、python-multipart、Pillow、openai（3.39.0）、psutil、httpx、pytest；React 19、Vite 8、TypeScript 7。需要其他套件時回報整合者。
- 不執行 git commit、不切換分支、不修改 git 設定。
- 不寫入專案根目錄的 `data/`、`logs/`、`config/`。手動試跑時用環境變數把路徑導向自己的暫存目錄（見第 3 節），並使用分配的連線埠。
- Python：3.14，型別註記，`from __future__ import annotations`；識別字用英文，註解用繁體中文、精簡；日誌訊息用繁體中文。
- 前端：TypeScript strict、React 函式元件；只用 `src/shared/` 的 API、時鐘、WebSocket 工具；不載入外部 CDN 或網路字型；樣式用一般 CSS 並使用 `src/shared/theme.css` 的 token。
- 測試檔名以工作包為前綴，平放在 `tests/`（例如 `tests/test_core_sessions.py`），使用 `tests/conftest.py` 的夾具；只跑自己的測試。
- 只有主機確認的狀態才算數：作品、佇列與播放狀態以 SQLite 與檔案為準，日誌不能作為狀態來源。

## 3. 開發指令、環境變數與連線埠

```bash
# Python（專案根目錄執行）
PY="/c/Users/Chrono/miniconda3/envs/agent-use-latest/python.exe"
"$PY" -m pytest tests/test_core_*.py -q
"$PY" -m backend                 # Web 程序（含播放控制器）
"$PY" -m backend.worker          # AI Worker
"$PY" -m backend.logsink         # 集中日誌寫入器
"$PY" -m launcher                # 啟動器

# 前端（frontend/ 目錄執行）
npx tsc --noEmit -p tsconfig.json                       # 只處理自己檔案的錯誤
VITE_PAGES=draw VITE_OUT_DIR="<你的暫存目錄>" npx vite build
```

| 環境變數 | 用途 | 預設 |
| --- | --- | --- |
| `AP_DRAWART_ROOT` | 專案根目錄 | 程式所在專案 |
| `AP_DRAWART_DATA_DIR` / `AP_DRAWART_LOGS_DIR` / `AP_DRAWART_CONFIG_DIR` | 資料／日誌／設定目錄 | `<root>/data`、`logs`、`config` |
| `AP_DRAWART_BASE_IMAGE` | 藝術家原圖來源 | `<root>/base.webp` |
| `AP_DRAWART_STATIC_DIR` | 前端建置輸出 | `<root>/frontend/dist`，交付包為 `app/static` |
| `AP_DRAWART_HOST` / `AP_DRAWART_PORT` | Web 監聽 | `0.0.0.0` / `8080` |
| `AP_DRAWART_LOG_SINK` | 集中日誌寫入器 `host:port`；未設定時直接寫檔 | 未設定 |
| `AP_DRAWART_AI_PROVIDER` | `openai` 或 `fake` | `openai` |
| `AP_DRAWART_FAKE_DELAY_SECONDS` / `AP_DRAWART_FAKE_FAIL_RATE` | 假供應商的延遲與失敗率 | `5` / `0` |
| `AP_DRAWART_ADMIN_ALLOW_ALL` | `1` 時後臺不檢查來源 IP（僅測試） | 未設定 |
| `OPENAI_API_KEY` | OpenAI 金鑰；未設定時讀 `config/openai.local.json` | 未設定 |

手動試跑專用連線埠：backend-core 8101、playback 8102、worker-ai 8103、launcher-logs 8104（日誌寫入器 8114）、frontend-draw 8105、frontend-display 8106、frontend-admin 8107。正式預設 Web 8080、日誌寫入器 8765。

## 4. 識別碼、時間、路徑與媒體網址

- 時間：UTC ISO 8601、毫秒、`Z` 結尾（`backend.timeutil.now_iso()`）。所有給平板與展示頁的回應都帶 `server_now`。
- 識別碼：場次、作品、生成嘗試為 32 字元 uuid4 hex；`ai_jobs`、`playback_items`、`playback_runs`、`artworks`、`settings_versions` 為整數。
- 收件順序 `receipt_order`：全域遞增整數，從 1 開始，在收件交易內取 `MAX + 1`。
- 資料庫路徑欄位：相對於 `data_dir`、使用正斜線。
- 媒體網址：`/media/<相對路徑>?v=<版本>`，只開放 `artworks/`、`submissions/`、`ai-tests/`（`backend/api/pages.py`）。

| 檔案 | 路徑 |
| --- | --- |
| 原圖 | `artworks/<artwork_id>/original.webp` |
| 透明筆跡 | `submissions/<submission_id>/strokes.png` |
| 白底作品 | `submissions/<submission_id>/visitor.png` |
| 作品資料 | `submissions/<submission_id>/metadata.json` |
| AI 輸入 | `submissions/<submission_id>/ai/<attempt_id>/ai-input.png` |
| AI 原始輸出 | `submissions/<submission_id>/ai/<attempt_id>/ai-output.<副檔名>`（供應商非 PNG 時才另存） |
| AI 展示圖 | `submissions/<submission_id>/ai/<attempt_id>/ai-result.png` |
| 嘗試資料 | `submissions/<submission_id>/ai/<attempt_id>/metadata.json` |
| 試生成 | `ai-tests/<test_id>/input.png`、`result.png`、`metadata.json` |

## 5. 資料表與狀態

結構見 `backend/schema/10_core.sql`（共用）與 `20_playback.sql`（playback 擁有）。`runtime_state` 的鍵見 `backend/services/state.py`。設定版本見 `backend/services/settings.py`。

| 對象 | 狀態 |
| --- | --- |
| `sessions.status` | `drawing` → `submitted` / `empty` / `discarded` / `abandoned` |
| `sessions.end_reason` | `manual`、`timeout`、`draft_lost`、`staff` |
| `ai_jobs.status` | `queued` → `running` → `succeeded` / `failed`（不重試） |
| `playback_items.status` | `waiting_for_ai` → `ready` → `preparing` → `playing` → `played` |
| `playback_items.result_kind` | `generated`（`ai_attempt_id` 指向成功嘗試）或 `failure_card`（`failure_text` 為「此刻，機器沉默。」） |

收件交易（backend-core）在同一個 `BEGIN IMMEDIATE` 內：結束場次、建立 `submissions`、`ai_jobs`（`queued`）、`playback_items`（`waiting_for_ai`）。AI 結束時（worker-ai）在同一交易內：更新 `ai_attempts`、`ai_jobs`，並把 `playback_items` 改為 `ready`、填入 `result_kind`、`content_version += 1`。

## 6. HTTP API

錯誤一律為 `{"error": {"code", "message", "details"}}`（`backend.api.errors.ApiError`）。

### 6.1 共用物件

```jsonc
// ArtworkInfo
{"id": 1, "url": "/media/artworks/1/original.webp?v=1", "width": 1600, "height": 1132}
// PublicSettings（settings.public_subset）
{"drawing_duration_seconds": 90, "turnaround_seconds": 10, "reference_opacity": 0.3}
// SessionView
{
  "id": "hex32", "status": "drawing",
  "artwork": ArtworkInfo, "settings": PublicSettings,     // 皆為場次綁定的版本
  "started_at": "...", "deadline_at": "...", "ended_at": null,
  "next_session_available_at": null,                      // 場次結束後才有值
  "submission": {"id": "hex32", "queue_ahead": 3} | null  // queue_ahead＝收件順序在前且尚未 played 的件數
}
```

### 6.2 平板（backend-core，`backend/api/public.py`）

| 方法與路徑 | 請求 | 成功回應 |
| --- | --- | --- |
| `GET /api/config` | — | `{server_now, artwork, settings, canvas: {width: 1920, height: 1080}, intake_enabled}`；同時偵測原圖是否更換 |
| `GET /api/sessions/current` | — | `{server_now, intake_enabled, next_session_available_at, active_session: SessionView \| null}`；開放時間已過則為 `null` |
| `POST /api/sessions` | `{"tablet_id": "選填"}` | 201 `{server_now, session}`；建立前同步原圖 |
| `GET /api/sessions/{id}` | — | `{server_now, session}` |
| `POST /api/sessions/{id}/submit` | multipart：`submission_key`、`reason`（`manual`/`timeout`）、`strokes`（PNG） | `{server_now, result: "submitted"\|"empty"\|"discarded", session}` |
| `POST /api/sessions/{id}/abandon` | `{"reason": "draft_lost" \| "staff"}` | `{server_now, session}` |
| `GET /api/submissions/{id}` | — | `{server_now, submission: {id, session_id, receipt_order, received_at, visitor_url, ai_status, playback_status, queue_ahead}}` |

建立場次的錯誤：409 `intake_paused`；409 `session_active`（`details.session_id`）；409 `turnaround`（`details.next_session_available_at`）；503 `artwork_unavailable`。

提交規則：

- `strokes` 為透明背景 PNG，正常為 1920 × 1080；主機只要求可解碼，不設大小上限。
- 空白判定：alpha 通道最大值為 0。空白時不存檔、不建工作，`result: "empty"`。
- 資料磁碟可用空間低於 `DISK_DISCARD_RATIO`（2%）時不存檔、不建工作，場次標為 `discarded`，寫 WARNING 日誌，`result: "discarded"`。
- 一般收件：先寫入 `data/tmp/<uuid>/`，產生 `visitor.png`（白底合成）與 `metadata.json`，搬到 `submissions/<id>/`，再執行收件交易。
- 所有結果都結束場次：`ended_at = now`，`next_session_available_at = now + 綁定的 turnaround_seconds`。
- 冪等：同場次、同 `submission_key`、同內容雜湊的重送，回覆原結果（200）。同 key 不同內容回 409 `submission_conflict`；場次已由其他 key 或放棄結束，回 409 `session_closed`（`details.session` 為 SessionView）。
- 截止時間過後仍接受補送，不設期限。
- 非容量門檻的寫入失敗回 500 `storage_error`，平板應保留作品並重送。

放棄規則：只有 `drawing` 可放棄；已放棄再呼叫回原結果；其他已結束狀態回 409 `session_closed`。放棄也設定下場開放時間。

### 6.3 展示（playback，`backend/api/display.py`）

`GET /api/display/current` → `{server_now, current: DisplaySnapshot | null, pending: DisplaySnapshot | null, progress: Progress}`

```jsonc
// DisplaySnapshot
{
  "revision": 12,                       // 0 代表尚無任何作品的待機畫面
  "kind": "item" | "standby",
  "playback_item_id": 5, "receipt_order": 5,          // standby 時為 null
  "visitor": {"url": "/media/submissions/<id>/visitor.png?v=1"} | null,
  "ai": {"type": "image", "url": "..."} | {"type": "failure", "text": "此刻，機器沉默。"} | {"type": "standby"},
  "artist": ArtworkInfo                 // 該場次綁定的原圖；standby 時為目前啟用原圖
}
// Progress（中螢幕進度提示）
{"active": true, "started_at": "...", "estimate_seconds": 60, "queued_after": 2}
```

`estimate_seconds`：最近 10 件成功嘗試耗時的中位數（秒，取整），無紀錄時 60。`queued_after`：`ai_jobs.status = 'queued'` 的件數。沒有執行中的工作時 `active: false`、`started_at: null`。

### 6.4 後臺（backend-core，`backend/api/admin.py`）

`/admin` 與 `/api/admin/*` 由 `AdminNetGuardMiddleware` 限制：允許本機迴路、與主機任一 IPv4 介面同網段的來源；`AP_DRAWART_ADMIN_ALLOW_ALL=1` 時全部允許；其他回 403 `forbidden_network`。

| 方法與路徑 | 說明 |
| --- | --- |
| `GET /api/admin/overview` | 儀表板輪詢用：`{server_now, intake_enabled, health, active_session, next_session_available_at, counts, playback, progress, settings_version_id}`；`counts` 含 `ai_queued`、`ai_running`、`playback_unplayed`、`played_total`、`submissions_total`、`empty_total`、`discarded_total`、`abandoned_total`；`playback` 為 `get_playback_status()` 結果 |
| `GET /api/admin/queues?limit=50` | `{server_now, ai_jobs[], playback_items[], playback_runs[], sessions[]}`，各為最近的資料，欄位見下方 |
| `GET /api/admin/settings` | `{server_now, version_id, settings, defaults, allowed_models, allowed_qualities}` |
| `PATCH /api/admin/settings` | 部分設定；成功回同上；驗證失敗 422 `settings_invalid`（`details.field`） |
| `GET` / `PATCH /api/admin/intake` | `{intake_enabled}`；PATCH 立即生效並記錄日誌 |
| `GET /api/admin/logs?offset=&max_bytes=65536` | `{server_now, offset, next_offset, size, text}`；未給 offset 時回最後一段；從完整行開始；offset 超過檔案大小時改回最後一段 |
| `GET /api/admin/logs/download` | `text/plain; charset=utf-8` 下載 `runtime.txt` |
| `GET /api/admin/displays` | `{server_now, config, monitors, clients}`；`config` 為 `config/displays.json`，`monitors` 來自 `list_monitors()`，`clients` 來自 `get_playback_status()["clients"]` |
| `PATCH /api/admin/displays` | 寫入 `config/displays.json`，回同上；於下次重新定位視窗時生效 |
| `POST /api/admin/displays/relaunch` | 設定 `launcher_requests.relaunch_displays = true` |
| `POST /api/admin/exhibition/stop` | 關閉接待並設定 `launcher_requests.shutdown = true` |
| `POST /api/admin/launcher-requests/clear` | `{"name": "shutdown" \| "relaunch_displays"}`，啟動器處理後呼叫 |
| `GET` / `PUT /api/admin/openai-key` | GET `{configured, source}`；PUT `{"api_key"}` 寫入 `config/openai.local.json`；任何回應與日誌都不含金鑰 |
| `POST /api/admin/ai-test` | multipart：選填 `image`，選填 `ai_model`、`ai_quality`、`ai_size`、`ai_prompt`；未附圖時用最新作品的 `visitor.png`，皆無時 409 `no_test_image`；呼叫 `run_ai_test()` 並回傳其結果 |

`queues` 欄位：

- `ai_jobs`：`id, submission_id, receipt_order, status, started_at, finished_at, duration_ms, model, error_code, error_message`
- `playback_items`：`id, submission_id, receipt_order, status, result_kind, played_at, visitor_url, ai_url`
- `playback_runs`：`id, playback_item_id, receipt_order, timing_started_at, ended_at, result, min_display_seconds`
- `sessions`：`id, status, started_at, ended_at, end_reason`

放棄場次沿用 `POST /api/sessions/{id}/abandon`（`reason: "staff"`）。

### 6.5 健康檢查（backend-core）

`GET /api/health`（不受網段限制，啟動器與展示頁使用）：

```jsonc
{
  "server_now": "...",
  "status": "ok" | "degraded" | "setup_required",
  "admin_urls": ["http://192.168.1.10:8080/admin"],        // 主機各區網 IPv4 的後臺網址
  "launcher_requests": {"shutdown": false, "relaunch_displays": false},
  "checks": {
    "database": {"ok": true},
    "frontend": {"ok": true, "missing": []},                // draw/display/admin 的 index.html
    "artwork": {"ok": true, "artwork_id": 1},
    "worker": {"ok": true, "pid": 123, "last_heartbeat_at": "..."},   // 心跳 15 秒內
    "displays": {"ok": true, "roles": {"visitor": {...}, "ai": {...}, "artist": {...}}},
    "disk": {"ok": true, "free_bytes": 0, "total_bytes": 0, "free_ratio": 0.42, "discarding": false},
    "log": {"ok": true, "mode": "sink", "last_error": null},
    "openai_key": {"ok": true, "source": "env"}             // fake 供應商時一律 ok
  }
}
```

`openai_key` 不通過時為 `setup_required`；其他任一項不通過為 `degraded`。

## 7. WebSocket（playback，`backend/api/ws.py`）

`WS /ws/events?role=visitor|ai|artist&client_id=<每次載入頁面產生的亂數>`

- 每個角色同時只保留一條連線；新連線取代舊連線（舊連線以 4000 關閉）。
- 平板與後臺不使用 WebSocket，改用 HTTP 輪詢。

主機 → 展示頁（型別見 `frontend/src/shared/types.ts` 的 `ServerMessage`）：

| 訊息 | 時機 |
| --- | --- |
| `hello {server_now, role, current, pending, progress}` | 連線後立即送出 |
| `prepare {server_now, snapshot}` | 下一件已就緒且準備預載；`snapshot.revision` 為新版本 |
| `commit {server_now, revision, switch_at}` | 三個角色都回報 ready，且目前作品已達最低時間；`switch_at` 約為現在 + 400 毫秒 |
| `progress {server_now, progress}` | 生成開始、結束或排隊數改變 |

展示頁 → 主機（`ClientMessage`）：

| 訊息 | 時機 |
| --- | --- |
| `ready {revision}` | 已載入並解碼 prepare 的內容 |
| `presented {revision}` | 已實際顯示該版本（hello 的 current、或 commit 切換後） |
| `heartbeat {shown_revision}` | 每 5 秒 |
| `client_error {message}` | 前端錯誤，主機寫入日誌 |

展示頁收到不認得版本的 commit（例如漏收 prepare）時，改呼叫 `GET /api/display/current`，載入後立即顯示並回報 presented。

## 8. 後端內部介面

| 介面 | 擁有者 | 說明 |
| --- | --- | --- |
| `backend.services.artworks.sync_active_artwork(conn) -> dict \| None` | backend-core | 比對 `base.webp` 的 mtime 與大小，有變動才算雜湊；新內容可解碼時複製並登錄新版本、更新 `active_artwork_id`；回傳 ArtworkInfo 所需欄位；無法解碼時沿用前一版並寫 WARNING |
| `backend.playback.controller.start()` / `stop()` | playback | 由 app lifespan 呼叫的 async 函式 |
| `backend.playback.status.get_playback_status(conn) -> dict` | playback | `{phase, display_revision, current, pending, clients}`；`current` 含 `playback_item_id, receipt_order, submission_id, result_kind, timing_started_at, min_display_seconds, played`；`pending` 含 `playback_item_id, receipt_order, ready_roles`；`clients[role]` 含 `connected, shown_revision, last_heartbeat_at` |
| `backend.playback.status.get_progress(conn) -> dict` | playback | Progress 物件 |
| `backend.ai_providers.get_provider() -> ImageProvider` | worker-ai | 依 `AP_DRAWART_AI_PROVIDER` 回傳；介面見 `ai_providers/base.py` |
| `backend.worker.ai_test.run_ai_test(image_path, overrides) -> dict` | worker-ai | 同步執行一次付費試生成，不進佇列；回傳 `{id, status, model, quality, size, duration_ms, result_url, error_code, error_message}` |
| `backend.platform.monitors.list_monitors() -> list[dict]` | launcher-logs | `[{index, name, x, y, width, height, primary}]`，由左到右 |
| `backend.logclient.get_logger(source)` | 整合者 | `info/warning/error/exception(event, message, **ids)` |
| `backend.services.settings` / `state` | 整合者 | 設定版本與 runtime_state |
| `backend.config` | 整合者 | 路徑、常數、金鑰與螢幕設定檔讀寫 |

Worker 心跳：每 5 秒寫入 `runtime_state.worker_heartbeat = {"pid", "at", "job_id"}`。

## 9. 播放控制器規則（playback）

- 在 Web 程序內以單一 asyncio 任務執行，約每 200 毫秒一輪；資料庫操作以 `asyncio.to_thread` 執行。
- 下一件＝`receipt_order` 最小、尚未 `played`、且不是目前播放項的項目。它仍是 `waiting_for_ai` 時就等待，後面的作品不超車。
- 準備：狀態改 `preparing`、`display_revision + 1`，送出 `prepare`。三個角色都回報該版本 `ready`，且目前作品已 `played`（或沒有目前作品），才送出 `commit`。
- 計時：三個角色都回報新版本 `presented` 後，才開始計算最低展示時間；`min_display_seconds` 在送出 commit 時讀取目前設定並寫入項目。達到秒數後標記 `played`，`playback_runs.result = completed`，畫面繼續停留到下一件切換。
- 任一角色斷線、超過 15 秒無心跳或回報其他版本時，暫停計時；三個角色恢復同一版本後，從頭重新計算完整秒數。中斷的計時記為 `interrupted`。
- 主機重啟：`preparing`、`playing` 且未 `played` 的項目改回 `ready` 重新播放；目前畫面恢復為最後一件 `played` 的項目（沒有則為待機）。
- 沒有下一件時維持目前畫面，不切回待機。
- 寫入日誌事件：`playback.prepare`、`playback.commit`、`playback.timing_start`、`playback.played`、`playback.interrupted`、`display.connected`、`display.disconnected`、`display.client_error`。

## 10. AI Worker 規則（worker-ai）

- `python -m backend.worker` 啟動單一 Worker，一次處理一件，依 `receipt_order` 領取 `queued` 工作。
- 每件建立一個嘗試：複製 `visitor.png` 為 `ai-input.png`，以工作綁定的設定版本呼叫供應商；`timeout_seconds = ai_timeout_seconds`。
- OpenAI 用戶端設定 `max_retries=0`；任何錯誤、拒絕或逾時都直接 `failed`，不重試。實作前先檢查已安裝 openai 3.39.0 的 `images.edit` 簽名。
- 成功：把原始輸出轉成 PNG 存為 `ai-result.png`（保持模型輸出尺寸），驗證可解碼後在一個交易內更新嘗試、工作與待播項。
- 失敗：在一個交易內標記嘗試與工作 `failed`，待播項改 `ready`、`result_kind = failure_card`、`failure_text = FAILURE_TEXT`。
- 逾時後才抵達的結果一律捨棄（以嘗試識別碼與工作目前嘗試比對）。
- 啟動時：`running` 的工作若嘗試目錄已有可解碼的 `ai-result.png` 就完成歸檔，否則直接 `failed`，不重送雲端。
- 假供應商 `fake`：延遲 `AP_DRAWART_FAKE_DELAY_SECONDS` 秒，依 `AP_DRAWART_FAKE_FAIL_RATE` 隨機失敗；成功時以 Pillow 由輸入圖產生一張明顯不同、符合要求尺寸的圖。
- 寫入日誌事件：`ai.start`、`ai.succeeded`、`ai.failed`、`ai.recovered`、`worker.start`、`worker.stop`。

## 11. 啟動器與集中日誌寫入器（launcher-logs）

- `python -m backend.logsink [--port 8765]`：TCP 伺服器，接收 `logclient` 送來的 JSON 行，以 `logclient.format_line()` 格式附加寫入 `runtime.txt`，每行寫完即 flush；它是唯一寫入者。
- `start-exhibition.bat`：以自身所在目錄為基準，尋找 Python（`runtime\python\pythonw.exe`，否則開發用 `C:\Users\Chrono\miniconda3\envs\agent-use-latest\pythonw.exe`），在背景啟動 `-m launcher` 後結束自己的視窗。
- 啟動器依架構文件 2.4 的步驟：單一執行個體鎖、在程序內啟動日誌寫入器並設定 `AP_DRAWART_LOG_SINK`、檢查環境、啟動 Web 與 Worker 子程序、輪詢 `/api/health` 等待就緒、依 `config/displays.json` 與 `list_monitors()` 開啟三個全螢幕展示視窗，之後持續監看子程序與 `launcher_requests`。
- 展示視窗：瀏覽器依設定 `browser`（`auto` 時依序找交付包內瀏覽器、Edge、Chrome），每個角色使用獨立 `--user-data-dir` 與 `--kiosk`、`--window-position`，網址為 `http://127.0.0.1:<port>/display/<role>`。
- 第二次執行：偵測到既有執行個體時，只要求補開缺少的展示視窗後結束。
- `stop-exhibition.bat`：同樣以自身所在目錄為基準，改用有主控台的 `python.exe` 執行 `-m launcher --stop`，顯示結果後關閉；詳見第 0.6 節。

## 12. 前端約定

| 頁面 | 網址 | 說明 |
| --- | --- | --- |
| 平板 | `/draw` | HTTP 輪詢，不用 WebSocket |
| 展示 | `/display/visitor`、`/display/ai`、`/display/artist` | 角色取自網址最後一段；Vite 開發伺服器用 `/display/?role=ai` |
| 後臺 | `/admin` | 每 2 秒輪詢 `/api/admin/overview` |

- 共用：`src/shared/api.ts`（`apiGet/apiPost/apiPatch/apiPut/apiPostForm`、`ApiRequestError`、`NetworkError`，回應中的 `server_now` 會自動校正時鐘）、`clock.ts`（`serverNow()`、`msUntil()`）、`ws.ts`（`ReconnectingSocket`）、`types.ts`、`theme.css`。
- 後臺專用的型別定義在 `src/admin/` 內。
- 視覺仍為暫時樣式：依架構文件的畫面需求做出清楚、可操作的版面，使用 theme token，視覺定稿後再替換。

## 13. 日誌事件

| 前綴 | 事件 |
| --- | --- |
| `system.*` | `web_start`、`web_stop`、`launcher_start`、`launcher_ready`、`launcher_stop`、`preflight`、`child_start`、`child_stop`、`child_exit`、`child_restart`、`child_output`、`display_window_*`、`status_page`、`launcher_request*`、`launcher_second_instance` |
| `logsink.*` | 日誌寫入器自身事件，例如 `malformed` |
| `session.*` | `start`、`submitted`、`empty`、`discarded`、`abandoned`、`resubmit` |
| `submission.*` | `saved`、`save_failed`、`conflict` |
| `artwork.*` | `registered`、`invalid` |
| `settings.*` | `updated`、`intake_changed`、`openai_key_set`、`displays_changed` |
| `ai.*`、`worker.*` | 見第 10 節，另有 `ai.late_result`、`ai.metadata_failed`、`worker.recovered`、`worker.heartbeat_failed`、`worker.loop_error` |
| `playback.*`、`display.*` | 見第 9 節，另有 `playback.start`、`playback.stop`、`playback.error` |
| `admin.*` | `ai_test`、`relaunch_displays`、`exhibition_stop`、`forbidden` |

AI 失敗的 `error_code` 除第 10 節外，另有 `input_missing`、`storage_error`、`bad_response`、`internal`、`interrupted`、`not_configured`、`quota`、`refused`、`fake_failure`，試生成另有 `invalid_image`、`settings_invalid`。

每筆附上可取得的 `session_id`、`submission_id`、`ai_job_id`、`attempt_id`、`playback_item_id` 等識別碼。
