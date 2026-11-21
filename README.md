# 黑客松專案資料夾

## 目錄

- `00_執行環境/`：Python 虛擬環境、套件快取、瀏覽器設定與 Python 快取
- `01_提案文件/`：企劃書、500 字說明與 A4 海報（DOCX / PDF）
- `02_網站原型/`：長庚校務導航 MVP、500 字說明頁與海報 HTML
- `03_開發腳本/`：提案產生器、HTML 產生器、PDF 檢查與爬蟲
- `04_爬蟲資料/`：爬蟲輸出的 JSON、CSV、SQLite 與下載附件
- `05_檢查輸出/`：字數與 PDF 檢查結果
- `99_封存/`：重複檔案的保留副本
- `README_cgu_crawler.md`：爬蟲使用說明
- `requirements.txt`：Python 依賴

## 常用指令

```powershell
node 03_開發腳本/build_html.cjs
node 03_開發腳本/build_proposal.cjs
py 03_開發腳本/cgu_crawler.py --output 04_爬蟲資料/cgu_data --max-pages 2000 --verbose
```

## SQLite + FTS5 網站原型

網站現在以 `04_爬蟲資料/cgu_data/cgu.db` 的 `items` 與 `items_fts` 作為搜尋來源。啟動本機 API 與網站：

```powershell
py server.py
```

再開啟 `http://127.0.0.1:8765/`。`/api/search` 使用 SQLite FTS5 查詢，短詞或非連續中文查詢會使用欄位備援比對；`/api/meta` 提供索引筆數、單位與文件類型。

網站入口：`02_網站原型/index.html`。瀏覽器設定與執行環境已集中放在 `00_執行環境/`，不影響專案檔案瀏覽。

完整的技術組成、資料表、API 與搜尋流程請見 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

搜尋設定在 [`search_config.json`](search_config.json)，包含精選建議、同義詞、錯字修正與人工排序提升。這些設定讓「英文免修」、「宿舍退宿」等口語查詢能找到校務文件，也保留後續檢視搜尋紀錄的入口。

## 本地 Git repo

目前環境的根目錄 `.git` 為唯讀，因此本專案使用 `.git-work` 保存本地 Git metadata。可以用：

```powershell
.\git-local.ps1 status
.\git-local.ps1 log --oneline -1
```
