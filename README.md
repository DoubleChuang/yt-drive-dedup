# yt-drive-dedup

找出 Google Drive 裡「已經上傳到 YouTube」的影片，確認重複後把 Drive 的備份刪掉，回收儲存空間。

Find the videos in your Google Drive that are already uploaded to YouTube, verify the match, and trash the redundant Drive copies.

---

## 這個工具解決什麼問題

手機、相機、行車記錄器會一直往 Drive 塞影片，很可能同一批檔案早就上傳到 YouTube 了，兩邊各佔一份空間。要人工比對幾百支影片並不現實，因為上傳時常會改名，光看檔名對不起來。

這支腳本用三個獨立訊號交叉比對，只有證據夠強才會動手，預設還只把檔案移到垃圾桶（可復原）。

## 比對邏輯

Drive 的每個影片檔會跟頻道上**所有**已上傳影片逐一評分：

| 訊號 | 配分 | 說明 |
|---|---|---|
| 檔案大小完全相同 | +55 | 位元組完全相同，同一份檔案的鐵證 |
| `fileDetails.fileName` 完全相同 | +40 | YouTube 對自己頻道的影片會回傳原始上傳檔名 |
| `fileDetails.fileName` 正規化後相同 | +30 | 忽略副檔名、空格、`copy`、`(1)`、`副本` 等 |
| YouTube 標題 = 檔名 | +25 | |
| YouTube 標題包含在檔名中（或反之） | +15 | 標題正規化後須 ≥5 字元，避免「1」「2」這種短標題亂配 |
| 時長差 ≤ 2 秒 | +40 | |
| 時長差 ≤ 10 秒 | +18 | |

分級：

- **STRONG**（≥85 分）— 確定重複，`--apply` 會刪這批
- **REVIEW**（55–84 分）— 疑似，只列出不刪，你自己判斷
- **AMBIG** — 多個 Drive 檔案搶同一支 YouTube 影片且沒有鐵證時，全部排除不刪
- 其餘不配對

`--explain` 會把**每一支**影片最接近的 YouTube 候選連分數一起印出來（包含未達門檻的），方便你人工複核。

## 安全設計

- **預設不刪任何東西。** 不加 `--apply` 就只產出報表。
- **預設丟垃圾桶，不永久刪除。** 30 天內可從 Drive 網頁還原。要永久刪必須自己加 `--permanent`。
- **只刪你有權刪的檔案。** 別人分享給你的檔案即使內容重複，Google 也不允許你刪（`403 insufficientFilePermissions`），腳本會列出擁有者後跳過。
- **不做模糊猜測。** 沒有達到 STRONG 的配對一律不動。

## 快速開始（Docker，推薦）

只需要 Docker 與 Docker Compose，本機不用安裝 Python 或任何套件。

```bash
git clone https://github.com/DoubleChuang/yt-drive-dedup.git
cd yt-drive-dedup
docker compose build
```

第一次要先做 Google 授權。把 OAuth client JSON 放進 `secrets/`（該目錄已被 git 忽略）：

```bash
cp ~/Downloads/client_secret_xxx.apps.googleusercontent.com.json secrets/client_secret.json
```

授權流程見下方「Google 授權」章節，容器版是：

```bash
docker compose run --rm auth --client-secret /secrets/client_secret.json
docker compose run --rm auth --client-secret /secrets/client_secret.json  --code 'http://localhost:1/?code=4/0A...'
```

拿到 token 之後：

```bash
docker compose run --rm dedup --owned-only     # 先看報表，什麼都不刪
docker compose run --rm dedup --explain        # 逐支檢視最接近的候選
docker compose run --rm dedup --apply          # 確認後刪除 STRONG 那批
```

`docker compose run` 後面的參數會直接傳給程式，所以 `--owned-only`、`--apply`、`--ids 3,7,9`、`--show-all` 等用法跟本機執行完全一樣。

### 檔案放哪裡

```
data/                                   ← 掛載成容器的 /data
├── token.json                          Google 授權（已 gitignore）
├── yt_drive_dedup_report.csv           配對結果
└── yt_drive_dedup_unmatched.csv        未配對清單
secrets/                                ← 唯讀掛載成 /secrets
└── client_secret.json                  OAuth client（已 gitignore）
```

`data/` 與 `secrets/` 都以 `.gitkeep` 保留在 repo 裡，所以 clone 下來目錄就存在、擁有者是你本人。若改成自己手動建目錄，Docker 會用 root 建立，非特權容器就寫不進去。

### 檔案權限

容器以你的 UID/GID 執行，產出的檔案不會變成 root 所有。預設 `1000:1000`，帳號不是 1000 的話：

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) docker compose run --rm dedup --owned-only
```

或寫進 `.env`：

```
LOCAL_UID=1001
LOCAL_GID=1001
```

### 容器細節

- 基底映像 `python:3.12-slim`，只安裝 `requirements.txt` 的三個套件
- 以非特權使用者 `app`（uid 1000）執行，映像內不跑 root
- `./data` 可寫、`./secrets` 唯讀，不對外開 port
- 不需要 Dockerfile 以外的建置步驟，`docker compose build` 一次到底

## 本機執行（不想用 Docker 的話）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

需要 Python 3.10 以上。後面的授權與用法把 `docker compose run --rm auth ...` 換成 `python auth.py ...`、
`docker compose run --rm dedup ...` 換成 `python yt_drive_dedup.py ...` 即可。

## 第一次使用：Google 授權

### 1. 建立 OAuth 憑證（只需做一次）

1. 到 <https://console.cloud.google.com/projectselector2/home/dashboard> 建立或選擇一個 project
2. 到 <https://console.cloud.google.com/apis/library> 啟用兩個 API：
   - **Google Drive API**
   - **YouTube Data API v3**
3. 到 <https://console.cloud.google.com/apis/credentials>
   `Create Credentials` → `OAuth 2.0 Client ID` → Application type 選 **Desktop app** → Create
4. 若 app 還在 Testing 狀態，把自己的帳號加進 test users：
   <https://console.cloud.google.com/auth/audience> → `Test users` → `Add users`
5. 下載 JSON，檔名類似 `client_secret_xxxx.apps.googleusercontent.com.json`

> 在 CLI 裡貼路徑時不要單獨一行只放 `/` 開頭的路徑，會被當成 slash command。

### 2. 產生 token

```bash
# 本機
python auth.py --client-secret ~/Downloads/client_secret_xxx.json

# Docker（client secret 已放進 secrets/）
docker compose run --rm auth --client-secret /secrets/client_secret.json
```

開啟印出來的網址，登入並勾選兩個權限：

- 查看及管理 Google 雲端硬碟中的檔案
- 查看你的 YouTube 帳戶

若出現「Google 尚未驗證這個應用程式」，點 `進階` → `前往 <你的project>（不安全）`。那是你自己的 project，正常。

授權完成後瀏覽器會跳到 `http://localhost:1/?code=...` 並顯示無法連線，**這是預期的**。把網址列整條複製下來：

```bash
# 本機
python auth.py --client-secret ~/Downloads/client_secret_xxx.json \
  --code 'http://localhost:1/?code=4/0A...&scope=...'

# Docker
docker compose run --rm auth --client-secret /secrets/client_secret.json \
  --code 'http://localhost:1/?code=4/0A...&scope=...'
```

完成後會產生 `token.json`（權限 600）：本機版在執行目錄，Docker 版在 `./data/token.json`。
兩者都已被 `.gitignore` 排除，不會被 commit。

token 的尋找順序：
`--token` 參數 > 環境變數 `GOOGLE_TOKEN_FILE` > `~/.hermes/google_token.json` > `./token.json`

## 使用方法

Docker 版把下面的 `python yt_drive_dedup.py` 換成 `docker compose run --rm dedup` 即可，參數完全相同。

```bash
# 1. 先看報表，什麼都不刪（預設行為）
python yt_drive_dedup.py

# 2. 只看自己擁有的影片，略過別人分享給你的
python yt_drive_dedup.py --owned-only

# 3. 逐支檢視最接近的 YouTube 候選（含未達門檻的），人工複核用
python yt_drive_dedup.py --owned-only --explain

# 4. 確認報表無誤後，把 STRONG 那批移到垃圾桶
python yt_drive_dedup.py --apply

# 5. 只想刪報表上第 3、7、9 筆
python yt_drive_dedup.py --apply --ids 3,7,9

# 6. 順便列出未配對的 Drive 影片與只在 YouTube 上的影片
python yt_drive_dedup.py --show-all

# 7. 永久刪除（跳過垃圾桶，無法復原）
python yt_drive_dedup.py --apply --permanent
```

### 參數一覽

| 參數 | 說明 |
|---|---|
| `--apply` | 真的執行刪除，只處理 STRONG；不加則只出報表 |
| `--permanent` | 永久刪除，不進垃圾桶（不可復原） |
| `--ids 3,7,9` | 指定報表列號刪除，覆蓋自動挑選 |
| `--owned-only` | 只考慮 `ownedByMe` 的檔案，略過別人分享的 |
| `--explain` | 列出每支影片最接近的 YouTube 候選與分數 |
| `--show-all` | 額外印出未配對清單（各前 40 筆） |
| `--token PATH` | 指定 token JSON 路徑 |
| `--report-dir DIR` | CSV 報表輸出目錄（預設：目前目錄） |

## 輸出

| 檔案 | 內容 |
|---|---|
| `yt_drive_dedup_report.csv` | 配對結果：Drive 檔名、大小、時長、擁有者、YouTube 標題與連結、信心分數、證據 |
| `yt_drive_dedup_unmatched.csv` | 三類未配對項目：`drive_no_match`（Drive 有但 YouTube 找不到）、`youtube_only`（YouTube 有但 Drive 沒來源）、`ambiguous`（多檔搶同一支影片） |
| `yt_drive_dedup_report_owned.csv` | 使用 `--owned-only` 時的報表檔名 |

`evidence` 欄位說明配對是靠哪些訊號成立的：`size=`（大小相同）、`filename=`（原檔名相同）、`filename~`（檔名近似）、`title=`/`title~`（標題相同／相近）、`dur=`/`dur~`（時長相同／相近）。

## 還原

刪除只是移到垃圾桶：

Drive 網頁 → 垃圾桶 → 勾選檔案 → 還原

垃圾桶內的檔案**仍佔用配額**，要「清空垃圾桶」才會真正釋放空間。這個工具不會幫你清空。

## 已知限制

- 只比對**目前授權帳號自己的頻道**。其他 Google 帳號的頻道要另外授權。
- 較舊的 YouTube 影片可能沒有 `fileDetails`（原檔名與大小），那些只能用標題與時長比對，準確度較低。腳本執行時會印出有無原檔資訊的數量。
- 時長由 YouTube 以整秒回報，短片（10 秒內）的時長容忍區間相對寬鬆，因此短片的時長訊號較弱——這也是 STRONG 門檻要求必須有大小或原檔名鐵證的原因。
- 若原始片段被合併進 `*_stitched` 之類的合輯影片，那屬於「部分使用」而非完整備份，不會被判定為重複。
- Drive 查詢涵蓋個人雲端硬碟（含分享給你的檔案），不含共用雲端硬碟（Shared Drives）。

## 疑難排解

| 症狀 | 原因與處理 |
|---|---|
| `no Google token at ...` | 還沒做授權，執行 `python auth.py` |
| `token is missing scope ...` | token 權限不足，刪掉 `token.json` 重新授權 |
| `403 insufficientFilePermissions` | 檔案不是你擁有的，只有擁有者能刪。用 `--owned-only` 可先過濾掉 |
| `403 Access Not Configured` | Cloud Console 沒啟用 Drive API 或 YouTube Data API v3 |
| `403 access_denied` | 帳號沒被加進 test users，到 <https://console.cloud.google.com/auth/audience> 加入 |
| `quotaExceeded` | YouTube Data API 每日額度用盡，隔天再跑 |
| 容器寫入 `./data` 權限錯誤 | `./data` 被建立成 root 所有。`sudo chown -R $(id -u):$(id -g) data`，並確認 `LOCAL_UID`/`LOCAL_GID` 與你一致 |
| `docker compose` 說 build context 太大 | 確認 `.dockerignore` 存在；它會排除 `.git`、產出的 CSV 與 `data/`、`secrets/` |

## 授權範圍

只請求兩個 scope：

- `https://www.googleapis.com/auth/drive`
- `https://www.googleapis.com/auth/youtube.readonly`

不讀 Gmail、不讀日曆、不取得通訊錄。token 存在本機，不會上傳到任何地方。

## License

MIT
