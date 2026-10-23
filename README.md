# yt-drive-dedup

找出 Google Drive 裡「已經上傳到 YouTube」的重複影片，確認重複後把 Drive 的備份刪掉，回收儲存空間。

Find the videos in your Google Drive that are already uploaded to YouTube, verify the match, and trash the redundant Drive copies.

用 Docker 一行指令就能跑，本機不需要安裝 Python 或任何套件。

---

## 這個工具解決什麼問題

手機、相機、行車記錄器會一直往 Drive 塞影片，很可能同一批檔案早就上傳到 YouTube 了，兩邊各佔一份空間。要人工比對幾百支影片並不現實，因為上傳時常會改名，光看檔名對不起來。

這支工具用三個獨立訊號交叉比對，只有證據夠強才會動手，預設還只把檔案移到垃圾桶（可復原）。

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

配分與門檻集中在 `MatchPolicy`（`ytdd/domain/matching.py`），調參不用改邏輯。

`--explain` 會把**每一支**影片最接近的 YouTube 候選連分數一起印出來（包含未達門檻的），方便你人工複核。

## 安全設計

- **預設不刪任何東西。** 不加 `--apply` 就只產出報表。
- **預設丟垃圾桶，不永久刪除。** 30 天內可從 Drive 網頁還原。要永久刪必須自己加 `--permanent`。
- **只刪你有權刪的檔案。** 別人分享給你的檔案即使內容重複，Google 也不允許你刪（`403 insufficientFilePermissions`），工具會列出擁有者後跳過。
- **不做模糊猜測。** 沒有達到 STRONG 的配對一律不動。

## 專案結構

分層架構，依賴方向一律由外向內：`presentation → application → domain`，`infrastructure` 只被最外層的 composition root 組裝。

```
ytdd/
├── domain/            純邏輯，不 import 任何 Google 套件、不讀檔、不印字
│   ├── models.py      DriveVideo / YouTubeVideo / Match / Tier
│   └── matching.py    評分與唯一性規則（MatchPolicy 集中所有參數）
├── application/       使用案例，只依賴 port，不知道 argparse 與 CSV
│   ├── ports.py       DriveVideoReader / YouTubeUploadReader / DriveTrashWriter
│   └── dedup_service.py  scan() 與 delete()
├── infrastructure/    Google 的實作，唯一會碰到網路與 token 的地方
│   ├── credentials.py token 解析與 scope 檢查
│   ├── drive_repository.py
│   ├── youtube_repository.py
│   └── oauth.py       兩步驟授權流程
├── presentation/      輸出與 CLI
│   ├── cli.py         argparse + composition root
│   ├── console.py     表格與摘要
│   ├── csv_report.py  CSV 輸出
│   └── formatting.py  大小與時長的格式化
├── __main__.py        python -m ytdd
└── auth.py            python -m ytdd.auth
tests/                 22 個單元測試，全部走 fake port，不碰網路
docker/
├── Dockerfile
└── docker-compose.yml
data/                  token.json 與報表（掛載進容器的 /data）
secrets/               OAuth client JSON（唯讀掛載）
```

幾個刻意的決定：

- **domain 沒有任何 I/O。** 評分規則可以在毫秒內被單元測試，不需要 token、不需要網路。
- **application 只認得 port。** `DedupService` 要的是 `DriveVideoReader` 這種 Protocol，不是 Google SDK。測試時換成假物件即可。
- **infrastructure 可以被替換。** 要支援第二個影片平台，新增一個 adapter，domain 與 application 不必改。
- **compose 與 Dockerfile 放在 `docker/`。** 部署產物與程式碼分開，repo 根目錄只留專案本身的檔案；build context 指回 `..`，所以 `.dockerignore` 仍在根目錄（Docker 只讀 context 根的那一份）。

## 快速開始（Docker，推薦）

只需要 Docker 與 Docker Compose，本機不用安裝 Python 或任何套件。

```bash
git clone https://github.com/DoubleChuang/yt-drive-dedup.git
cd yt-drive-dedup/docker
docker compose build
```

第一次要先做 Google 授權。把 OAuth client JSON 放進 `secrets/`（該目錄已被 git 忽略）：

```bash
cp ~/Downloads/client_secret_xxx.apps.googleusercontent.com.json ../secrets/client_secret.json
```

授權流程見下方「Google 授權」章節，容器版是：

```bash
docker compose run --rm auth --client-secret /secrets/client_secret.json
docker compose run --rm auth --client-secret /secrets/client_secret.json --code 'http://localhost:1/?code=4/0A...'
```

拿到 token 之後：

```bash
docker compose run --rm dedup --owned-only     # 先看報表，什麼都不刪
docker compose run --rm dedup --explain        # 逐支檢視最接近的候選
docker compose run --rm dedup --apply          # 確認後刪除 STRONG 那批
```

不想 `cd` 的話，從 repo 根目錄用 `docker compose -f docker/docker-compose.yml run --rm dedup --owned-only`，或設一次 `COMPOSE_FILE=docker/docker-compose.yml`。

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

或寫進 `docker/.env`：

```
LOCAL_UID=1001
LOCAL_GID=1001
```

### 容器細節

- 基底映像 `python:3.12-slim`，相依套件在 build 時裝好，執行時不需要網路安裝
- 以非特權使用者 `app`（uid 1000）執行，映像內不跑 root
- `../data` 可寫、`../secrets` 唯讀，不對外開 port
- 程式以 `pip install --no-deps .` 安裝進 site-packages，所以 `python -m ytdd` 在任何工作目錄都能跑

## 本機執行（不想用 Docker 的話）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

需要 Python 3.10 以上。之後的指令把 `docker compose run --rm dedup ...` 換成 `python -m ytdd ...`、
`docker compose run --rm auth ...` 換成 `python -m ytdd.auth ...` 即可。

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
python -m ytdd.auth --client-secret ~/Downloads/client_secret_xxx.json

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
python -m ytdd.auth --client-secret ~/Downloads/client_secret_xxx.json \
  --code 'http://localhost:1/?code=4/0A...&scope=...'

# Docker
docker compose run --rm auth --client-secret /secrets/client_secret.json \
  --code 'http://localhost:1/?code=4/0A...&scope=...'
```

完成後會產生 `token.json`（權限 600）：本機版在執行目錄，Docker 版在 `data/token.json`。
兩者都已被 `.gitignore` 排除，不會被 commit。

token 的尋找順序：
`--token` 參數 > 環境變數 `GOOGLE_TOKEN_FILE`（容器用這個）> `~/.hermes/google_token.json` > `./token.json`

## 使用方法

Docker 版把下面的 `python -m ytdd` 換成 `docker compose run --rm dedup` 即可，參數完全相同。

```bash
# 1. 先看報表，什麼都不刪（預設行為）
python -m ytdd

# 2. 只看自己擁有的影片，略過別人分享給你的
python -m ytdd --owned-only

# 3. 逐支檢視最接近的 YouTube 候選（含未達門檻的），人工複核用
python -m ytdd --owned-only --explain

# 4. 確認報表無誤後，把 STRONG 那批移到垃圾桶
python -m ytdd --apply

# 5. 只想刪報表上第 3、7、9 筆
python -m ytdd --apply --ids 3,7,9

# 6. 順便列出未配對的 Drive 影片與只在 YouTube 上的影片
python -m ytdd --show-all

# 7. 永久刪除（跳過垃圾桶，無法復原）
python -m ytdd --apply --permanent
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
| `--report-dir DIR` | CSV 報表輸出目錄（預設：目前目錄，容器內為 `/data`） |

## 備份到 YouTube（`backup` 子命令）

反過來把 Drive 的影片上傳到 YouTube，成功後再釋放 Drive 空間。這是為了省 Drive 容量，跟上面的「刪除重複」是兩件事。

```bash
# 先看計畫：會列出可上傳幾支、多少 GB、跳過哪些，不動任何東西
python -m ytdd backup --dry-run

# 實際執行（預設上傳為 private，成功且轉檔完成後把 Drive 原檔移到垃圾桶）
python -m ytdd backup --limit 6

# 保留 Drive 原檔，只做 YouTube 多一份備份
python -m ytdd backup --keep-drive
```

### 配額是硬限制

`videos.insert` 每支影片花 **1600 配額單位**，新 Project 每天 **10,000 單位** → **一天 6 支**。超過就是 `quotaExceeded`，工具會停下來並告訴你還有幾支沒跑，隔天再執行即可（已上傳的不會重做）。

要一次跑完就得提高配額：Cloud Console → `APIs & Services` → `YouTube Data API v3` → `Quotas` → `Queries per day` → 申請調整。以 39 支計，大約需要 62,400 單位。

### 上傳流程與安全設計

1. 先比對（跟 scan 同一套規則），已確定在 YouTube 上的直接跳過，不浪費配額
2. 從 Drive 下載到暫存目錄（分塊串流，不會把整個檔案讀進記憶體）
3. 用可續傳上傳（resumable）送進頻道，預設隱私 `private`
4. 加入指定的播放清單（預設 `Google Drive 備份上傳`，不存在會自動建立），
   這樣備份上傳不會跟原本的影片混在一起
5. **等 YouTube 回報 `processed` 才刪 Drive 原檔**；逾時或失敗就保留原檔並回報
6. 刪除只是移到垃圾桶，30 天內可還原

預設不會上傳的三種情況：不是你的檔案、STRONG 已確定在 YouTube、REVIEW 疑似在 YouTube（要含進去請加 `--include-review`）。

### 參數

| 參數 | 說明 |
|---|---|
| `--dry-run` | 只列計畫，不上傳不刪除 |
| `--limit N` | 這次最多上傳幾支（配合每日配額） |
| `--privacy {private,unlisted,public}` | 上傳後的隱私狀態，預設 `private` |
| `--playlist NAME` | 上傳後加入的播放清單，預設 `Google Drive 備份上傳` |
| `--no-playlist` | 不加入任何播放清單 |
| `--keep-drive` | 上傳後保留 Drive 原檔 |
| `--include-review` | 連 REVIEW 的檔案也上傳（可能產生重複） |
| `--process-timeout SEC` | 等轉檔的秒數，預設 900 |
| `--work-dir DIR` | 下載暫存目錄，預設系統暫存目錄 |
| `--token PATH` | 指定 token 路徑 |

**注意這是「有備份」不是「無損備份」**：YouTube 會重新編碼，畫質與原檔不同，也拿不回原始檔案。要保留原檔就加 `--keep-drive`，重要素材建議先另外備份。

## 輸出

| 檔案 | 內容 |
|---|---|
| `yt_drive_dedup_report.csv` | 配對結果：Drive 檔名、大小、時長、擁有者、YouTube 標題與連結、信心分數、證據 |
| `yt_drive_dedup_unmatched.csv` | 三類未配對項目：`drive_no_match`（Drive 有但 YouTube 找不到）、`youtube_only`（YouTube 有但 Drive 沒來源）、`ambiguous`（多檔搶同一支影片） |
| `yt_drive_dedup_report_owned.csv` | 使用 `--owned-only` 時的報表檔名 |

`evidence` 欄位說明配對是靠哪些訊號成立的：`size=`（大小相同）、`filename=`（原檔名相同）、`filename~`（檔名近似）、`title=`/`title~`（標題相同／相近）、`dur=`/`dur~`（時長相同／相近）。

## 測試

不需要 token、不需要網路、不需要安裝 pytest：

```bash
python -m unittest discover -s tests -t .
```

測試只驅動 domain 與 application 兩層，用假的 port 實作取代 Google API。

## 還原

刪除只是移到垃圾桶：

Drive 網頁 → 垃圾桶 → 勾選檔案 → 還原

垃圾桶內的檔案**仍佔用配額**，要「清空垃圾桶」才會真正釋放空間。這個工具不會幫你清空。

## 已知限制

- 只比對**目前授權帳號自己的頻道**。其他 Google 帳號的頻道要另外授權。
- 較舊的 YouTube 影片可能沒有 `fileDetails`（原檔名與大小），那些只能用標題與時長比對，準確度較低。
- 時長由 YouTube 以整秒回報，短片（10 秒內）的時長容忍區間相對寬鬆，因此短片的時長訊號較弱——這也是 STRONG 門檻要求必須有大小或原檔名鐵證的原因。
- 若原始片段被合併進 `*_stitched` 之類的合輯影片，那屬於「部分使用」而非完整備份，不會被判定為重複。
- Drive 查詢涵蓋個人雲端硬碟（含分享給你的檔案），不含共用雲端硬碟（Shared Drives）。

## 疑難排解

| 症狀 | 原因與處理 |
|---|---|
| `no Google token at ...` | 還沒做授權，執行 `python -m ytdd.auth`（容器內 `docker compose run --rm auth`） |
| `token is missing scope ...` | token 權限不足，刪掉 `token.json` 重新授權 |
| `403 insufficientFilePermissions` | 檔案不是你擁有的，只有擁有者能刪。用 `--owned-only` 可先過濾掉 |
| `403 Access Not Configured` | Cloud Console 沒啟用 Drive API 或 YouTube Data API v3 |
| `403 access_denied` | 帳號沒被加進 test users，到 <https://console.cloud.google.com/auth/audience> 加入 |
| `quotaExceeded` | YouTube Data API 每日額度用盡，隔天再跑 |
| 容器寫入 `data` 權限錯誤 | 目錄被建立成 root 所有。`sudo chown -R $(id -u):$(id -g) data`，並確認 `LOCAL_UID`/`LOCAL_GID` 與你一致 |
| build 時 `"/README.md": not found` | `.dockerignore` 不可以排除 `README.md`，`pyproject.toml` 把它宣告成 package readme |
| `docker compose` 說 build context 太大 | 確認根目錄有 `.dockerignore`；它會排除 `.git`、產生的 CSV 與 `data/`、`secrets/` |

## 授權範圍

只請求這三個 scope：

- `https://www.googleapis.com/auth/drive`
- `https://www.googleapis.com/auth/youtube.readonly`
- `https://www.googleapis.com/auth/youtube.upload`（`backup` 需要）
- `https://www.googleapis.com/auth/youtube.force-ssl`（`backup` 把影片加入播放清單需要）

只跑 `scan` 時不會用到上傳權限；若現有 token 沒有 `youtube.upload`，`backup --dry-run` 仍可執行，真正上傳前會提示你重新授權。

不讀 Gmail、不讀日曆、不取得通訊錄。token 存在本機，不會上傳到任何地方。

## License

MIT
