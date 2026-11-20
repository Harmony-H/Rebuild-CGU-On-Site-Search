# 長庚大學規章與公告爬蟲

`cgu_crawler.py` 會從長庚大學校首頁與集中式規章總覽開始，沿著同校網的「最新消息／公告／規章／辦法／表單」連結探索各處室與系所網站。它會擷取：

- 規章名稱、編號、制定單位、公告日期、審議會議
- 各處室公告列表與公告詳情頁文字
- PDF、Word、Excel 等附件的名稱與網址（可選擇下載）

每筆資料均提供統一欄位：`unit`（單位）、`subject`（主題）、`document_type`（文件類型）、`applicable_audience`（適用對象）、`keywords`（關鍵字）、`published_date`（發布日期）、`last_updated_date`（最後更新日期）、`original_url`（原始連結）與 `is_valid`（是否仍有效）。日期輸出為 ISO 格式 `YYYY-MM-DD`；網站沒有明示更新日期時留白，無法由頁面確認有效性的資料以 `null` 表示。

## 執行

需要 Python 3.10 以上版本。

```powershell
py -m pip install -r requirements.txt
py 03_開發腳本/cgu_crawler.py --output 04_爬蟲資料/cgu_data --max-pages 2000 --verbose
```

需要保存附件時：

```powershell
py 03_開發腳本/cgu_crawler.py --output 04_爬蟲資料/cgu_data --download-files
```

## Scrapy 與 PDF 內文

使用 Scrapy 版本時，會下載頁面中的 PDF，透過 `pypdf` 抽取文字層，並寫入完整資料的 `pdf_text`、`pdf_pages` 欄位；抽出的文字也會用來補強關鍵字與適用對象。PDF 若是掃描影像、沒有文字層，需另外接 OCR。

```powershell
py -m pip install -r requirements.txt
py 03_開發腳本/cgu_scrapy_spider.py --output 04_爬蟲資料/cgu_data --max-pages 2000 --download-files
```

長庚部分 `ServerFile` 附件會被校方 `robots.txt` 排除；確認使用政策允許後，才加上 `--ignore-robots` 以讀取這些 PDF：

```powershell
py 03_開發腳本/cgu_scrapy_spider.py --output 04_爬蟲資料/cgu_data --max-pages 2000 --download-files --ignore-robots
```

只抓單一處室（例如人事室）：

```powershell
py 03_開發腳本/cgu_crawler.py --seed https://www.cgu.edu.tw/personnel/ --output 04_爬蟲資料/personnel_data
```

輸出包含 `items.json`、`items.jsonl`、`items.csv`、`standard_items.json`、`standard_items.csv`、`cgu.db`、`errors.json` 與 `run.json`。其中 `standard_items.*` 使用需求中的 9 個中文欄位；`items.*` 則保留完整原始欄位、附件資訊與 PDF 內文。`items` 的 `kind` 為 `regulation`、`announcement` 或 `announcement_detail`。程式設有重試、逾時、請求間隔、同網域限制與去重；再次執行會覆寫相同 URL 的最新資料，適合排程定期更新。

## SQLite + FTS5 快速搜尋

`cgu.db` 會建立 `items_fts` 全文索引，使用 SQLite FTS5 的 `trigram` tokenizer，適合中文與 PDF 內文搜尋。既有資料可重新建立索引：

```powershell
py 03_開發腳本/build_fts5_index.py --db 04_爬蟲資料/cgu_data/cgu.db
```

搜尋規章、公告與 PDF 內文：

```powershell
py 03_開發腳本/search_cgu.py "英文免修" --limit 20
py 03_開發腳本/search_cgu.py "免修門檻" --json
```

爬蟲每次寫入 `cgu.db` 後會自動重建 FTS5 索引；中文兩字詞由搜尋工具補上 `LIKE` 查詢，以支援「免修」、「流程」等短詞。

請遵守網站 robots.txt、服務條款與合理請求頻率；下載附件前也應確認公開授權與儲存政策。

