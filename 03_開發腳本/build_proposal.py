from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.style import WD_STYLE_TYPE
from pathlib import Path
import re

OUT=Path(__file__).resolve().parents[1] / '01_提案文件'
OUT.mkdir(parents=True, exist_ok=True)

NAVY='17324D'; TEAL='0B7285'; MINT='E6F4F1'; LIGHT='F3F6F8'; GRAY='5B6570'; BORDER='D9E1E6'; BLACK='000000'
FONT='Microsoft JhengHei'

def set_cell_shading(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr(); shd = tcPr.find(qn('w:shd'))
    if shd is None: shd=OxmlElement('w:shd'); tcPr.append(shd)
    shd.set(qn('w:fill'), fill)

def set_cell_border(cell, color=BORDER, sz='6'):
    tc = cell._tc; tcPr = tc.get_or_add_tcPr(); tcBorders = tcPr.first_child_found_in('w:tcBorders')
    if tcBorders is None: tcBorders=OxmlElement('w:tcBorders'); tcPr.append(tcBorders)
    for edge in ('top','left','bottom','right','insideH','insideV'):
        tag='w:'+edge; el=tcBorders.find(qn(tag))
        if el is None: el=OxmlElement(tag); tcBorders.append(el)
        el.set(qn('w:val'),'single'); el.set(qn('w:sz'),sz); el.set(qn('w:color'),color)

def set_cell_margins(cell, top=100, start=120, bottom=100, end=120):
    tc=cell._tc; tcPr=tc.get_or_add_tcPr(); tcMar=tcPr.first_child_found_in('w:tcMar')
    if tcMar is None: tcMar=OxmlElement('w:tcMar'); tcPr.append(tcMar)
    for m,v in [('top',top),('start',start),('bottom',bottom),('end',end)]:
        node=tcMar.find(qn('w:'+m))
        if node is None: node=OxmlElement('w:'+m); tcMar.append(node)
        node.set(qn('w:w'),str(v)); node.set(qn('w:type'),'dxa')

def set_repeat_table_header(row):
    trPr=row._tr.get_or_add_trPr(); tblHeader=OxmlElement('w:tblHeader'); tblHeader.set(qn('w:val'),'true'); trPr.append(tblHeader)

def set_col_widths(table, widths):
    for row in table.rows:
        for i,w in enumerate(widths): row.cells[i].width=Inches(w)

def style_doc(doc):
    sec=doc.sections[0]; sec.top_margin=Inches(0.6); sec.bottom_margin=Inches(0.55); sec.left_margin=Inches(0.7); sec.right_margin=Inches(0.7)
    styles=doc.styles
    normal=styles['Normal']; normal.font.name=FONT; normal._element.rPr.rFonts.set(qn('w:eastAsia'),FONT); normal.font.size=Pt(10.5); normal.font.color.rgb=RGBColor.from_string('222222')
    normal.paragraph_format.space_after=Pt(5); normal.paragraph_format.line_spacing=1.12
    for name,size,color in [('Title',24,NAVY),('Heading 1',16,NAVY),('Heading 2',12.5,TEAL),('Heading 3',11,GRAY)]:
        s=styles[name]; s.font.name=FONT; s._element.rPr.rFonts.set(qn('w:eastAsia'),FONT); s.font.size=Pt(size); s.font.bold=True; s.font.color.rgb=RGBColor.from_string(color); s.paragraph_format.space_before=Pt(10 if name!='Title' else 0); s.paragraph_format.space_after=Pt(4)
    # custom small
    if 'Small Note' not in styles:
        s=styles.add_style('Small Note', WD_STYLE_TYPE.PARAGRAPH); s.base_style=normal
    s=styles['Small Note']; s.font.name=FONT; s._element.rPr.rFonts.set(qn('w:eastAsia'),FONT); s.font.size=Pt(8.5); s.font.color.rgb=RGBColor.from_string(GRAY); s.paragraph_format.space_after=Pt(3)

def add_title(doc,title,subtitle=None):
    p=doc.add_paragraph(style='Title'); p.alignment=WD_ALIGN_PARAGRAPH.LEFT; p.add_run(title)
    if subtitle:
        p=doc.add_paragraph(); p.paragraph_format.space_after=Pt(8); r=p.add_run(subtitle); r.font.name=FONT; r._element.rPr.rFonts.set(qn('w:eastAsia'),FONT); r.font.size=Pt(12); r.font.color.rgb=RGBColor.from_string(TEAL); r.bold=True

def add_meta(doc, rows):
    t=doc.add_table(rows=len(rows), cols=2); t.alignment=WD_TABLE_ALIGNMENT.LEFT; t.autofit=False; set_col_widths(t,[1.25,5.65])
    for i,(a,b) in enumerate(rows):
        for c in t.rows[i].cells: set_cell_border(c); set_cell_margins(c)
        set_cell_shading(t.rows[i].cells[0],LIGHT); t.rows[i].cells[0].vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER; t.rows[i].cells[1].vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
        t.cell(i,0).text=a; t.cell(i,1).text=b
        for p in t.rows[i].cells[0].paragraphs: p.runs[0].bold=True; p.runs[0].font.color.rgb=RGBColor.from_string(NAVY)
    doc.add_paragraph()

def add_bullets(doc, items):
    for x in items:
        p=doc.add_paragraph(style='List Bullet'); p.paragraph_format.left_indent=Inches(0.2); p.add_run(x)

def add_table(doc, headers, rows, widths=None, header_fill=NAVY, font_size=9.2):
    t=doc.add_table(rows=1, cols=len(headers)); t.alignment=WD_TABLE_ALIGNMENT.LEFT; t.autofit=False
    if widths: set_col_widths(t,widths)
    hdr=t.rows[0]; set_repeat_table_header(hdr)
    for i,h in enumerate(headers):
        c=hdr.cells[i]; set_cell_shading(c,header_fill); set_cell_border(c); set_cell_margins(c,130,120,130,120); c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER; c.text=h
        for p in c.paragraphs:
            p.alignment=WD_ALIGN_PARAGRAPH.CENTER
            for r in p.runs: r.font.name=FONT; r._element.rPr.rFonts.set(qn('w:eastAsia'),FONT); r.font.size=Pt(font_size); r.bold=True; r.font.color.rgb=RGBColor(255,255,255)
    for ri,row in enumerate(rows):
        cells=t.add_row().cells
        for i,val in enumerate(row):
            c=cells[i]; set_cell_border(c); set_cell_margins(c); c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER; c.text=str(val)
            if ri%2==1: set_cell_shading(c,'F7FAFB')
            for p in c.paragraphs:
                p.paragraph_format.space_after=Pt(1)
                for r in p.runs: r.font.name=FONT; r._element.rPr.rFonts.set(qn('w:eastAsia'),FONT); r.font.size=Pt(font_size)
    doc.add_paragraph().paragraph_format.space_after=Pt(1)
    return t

def add_footer(doc, text='CGU AI Hackathon 2026｜校園資訊整合平台'):
    for sec in doc.sections:
        p=sec.footer.paragraphs[0]; p.alignment=WD_ALIGN_PARAGRAPH.CENTER; r=p.add_run(text); r.font.name=FONT; r._element.rPr.rFonts.set(qn('w:eastAsia'),FONT); r.font.size=Pt(8); r.font.color.rgb=RGBColor.from_string(GRAY)

# Full proposal
full=Document(); style_doc(full); add_title(full,'校園資訊整合平台企劃書','透明維修進度 × AI 校務導航｜CGU AI Hackathon 2026 初賽提案')
add_meta(full,[('隊伍名稱','【待填】'),('作品名稱','校園透明站 Campus OpenHub'),('指導教授','【姓名／系所待填】'),('文件用途','回應初賽四題，並規劃決賽 6 小時實作與 Codex Token 使用方式')])
full.add_heading('一、提案摘要',1)
full.add_paragraph('我們要把分散在總務、學務、教務與各系所網站的校務資訊，整理成一個可查、可追、可理解的校園入口。第一個核心場景是「維修進度透明化」：學生能看到案件位置、問題類型、目前狀態、預計完成時間與最後更新時間。第二個核心場景是「AI 校務導航」：把公告、辦法與表單建立可追溯的知識庫，學生用自然語言提問時，AI 以來源文件與更新日期回答，找不到依據時明確說明並引導至承辦單位。')
full.add_heading('二、初賽四題回應',1)
full.add_heading('1. 我們希望解決的校園問題',2)
full.add_paragraph('校園資訊目前分散於不同單位網站、公告欄、社群貼文與 PDF，入口不一致、搜尋成本高，學生常不知道應看哪一個版本；維修案件則多半只能「反映問題」，看不到處理進度，造成重複詢問與對行政單位的信任落差。這是一個每天都會發生、可由跨單位資料整合直接改善的校園問題。')
full.add_heading('2. 解決構想與差異',2)
full.add_paragraph('作品「校園透明站」由兩個模組組成：A. 維修追蹤：以案件編號、地點、分類、狀態、負責單位、預計完成日與更新時間呈現進度，支援依地點與狀態篩選；B. AI 校務導航：對公告與辦法做結構化索引，回答時顯示引用文件、段落與日期。差異在於把「進度資料」與「規範資料」放在同一個可信入口，並保留來源、版本與人工回報管道，避免只給一個無法查證的答案。')
add_table(full,['MVP 功能','使用者看得到的結果','決賽驗證方式'],[
('維修案件看板','案件列表、狀態時間軸、最後更新、預計完成日','以 10 筆示例案件完成 3 個查詢任務'),
('AI 校務問答','回答附來源文件與更新日期；不確定時轉人工','以 20 份示例公告測試 10 題，記錄正確率與引用率'),
('資料新鮮度與回報','顯示資料更新時間，可回報過期或錯誤','模擬一筆過期資料，確認提示與回報流程')], [1.3,3.1,2.5], font_size=8.8)
full.add_heading('3. Codex 與跨系專長分工',2)
full.add_paragraph('Codex 會協助把需求轉成資料模型、前後端介面、檢索問答流程與測試案例；團隊成員負責提供校務流程脈絡、判斷欄位與驗收標準。建議跨系分工如下，實際姓名與系所於報名時補入：')
add_table(full,['角色／專長','主要任務'],[
('AI／資訊','設計文件切分、檢索與引用格式；建立問答評測題；用 Codex 產生 API 與測試程式'),
('管理／公共事務','盤點公告與維修流程；定義使用者情境、權責邊界與資料更新規則'),
('工程／場域使用者','把維修欄位對應到實際場景；檢查地點、狀態與完成條件是否易懂'),
('全員','決定 MVP 範圍、審查 AI 回答、整合展示腳本與現場驗證')], [2.2,4.7], font_size=8.8)
full.add_heading('4. 決賽完成品與成效驗證',2)
full.add_paragraph('決賽結束時完成可操作的 Web 原型：首頁入口、維修案件看板、案件詳情時間軸、AI 校務問答視窗、引用來源區與錯誤回報按鈕。展示資料以去識別化示例資料建立，不使用個人敏感資料。驗證採任務導向：請測試者完成「找出某棟大樓尚未完成的維修」「詢問申請某項服務需要的文件」「判斷回答來源是否最新」三項任務；目標為任務完成率 ≥80%、AI 回答具來源比例 ≥90%、維修案件可在 30 秒內找到正確狀態。')
full.add_heading('三、決賽 6 小時與 Codex Token 規劃',1)
add_table(full,['時間','當天產出','Codex 協作重點'],[
('09:00–09:30','確認 MVP、資料欄位、展示任務','需求拆解、資料 schema、任務清單'),
('09:30–10:30','完成頁面骨架與假資料載入','產生元件、路由、樣式與資料介面'),
('10:30–12:00','完成維修看板與案件時間軸','CRUD、篩選、狀態元件、邊界案例'),
('12:00–13:00','完成公告索引與 AI 問答最小流程','prompt、檢索、引用與拒答規則'),
('13:00–14:00','整合兩模組並跑驗證任務','產生測試案例、修正錯誤、檢查引用'),
('14:00–15:00','視覺整理、彩排、展示與備援','修 UI、整理 README、生成 demo 腳本')], [1.25,2.7,2.95], font_size=8.3)
full.add_paragraph('Token 配置採比例控管，預估總量 80,000–120,000 tokens：需求與架構 15%、介面與 API 35%、AI 檢索與提示 25%、測試與除錯 15%、展示素材與文件 10%。事前只準備需求訪談題綱、去識別化示例資料、線框稿、測試腳本與空白專案骨架；核心功能、串接、測試與可展示成品均於 12/05 09:00–15:00 完成。')
full.add_heading('四、執行邊界與風險控制',1)
add_bullets(full,['資料只使用公開或去識別化內容；AI 回答保留來源文件、版本與日期。','若找不到依據或資料過期，顯示不確定提示並提供承辦單位／回報入口，不臆測。','先做兩個高價值場景的可驗證 MVP，再視時間加入通知、權限與統計等延伸功能。'])
full.add_heading('五、依活動規範準備清單',1)
add_table(full,['項目','規範／日期','團隊待辦'],[
('參賽資格','限長庚在學學生；2–8 人；至少 2 個系／所；1 位指導教授','補入成員、系所與教授資料；每人完成校內 Email 驗證'),
('初賽文件','A4 單頁創意海報＋500 字內創意說明 PDF；四題、時間與 Token 規劃不可缺','使用本企劃書內容製作兩份 PDF'),
('重要日期','報名截止 2026/11/23；入選公告 12/01；決賽 12/05 09:00–15:00','於截止日前完成上傳並確認「送出」成功'),
('決賽限制','作品須於當天完成；可事前準備但不得提前完成全部作品','保留建置、整合、測試與展示紀錄')], [1.3,3.2,2.4], font_size=8.5)
add_footer(full)
full.save(OUT/'校園資訊整合平台_企劃書.docx')

# 500-char explanation
concise=Document(); style_doc(concise); sec=concise.sections[0]; sec.top_margin=Inches(0.52); sec.bottom_margin=Inches(0.45); sec.left_margin=Inches(0.62); sec.right_margin=Inches(0.62)
add_title(concise,'校園透明站 Campus OpenHub','500 字內創意說明｜CGU AI Hackathon 2026')
concise.add_paragraph('隊伍名稱：【待填】　作品名稱：校園透明站　指導教授：【待填】',style='Small Note')
text='''1.問題：校園公告、辦法與表單分散在各單位網站、社群與 PDF；學生難以判斷哪個版本有效。維修案件也常只能反映，無法追蹤進度，造成重複詢問與行政負擔。\n2.構想：建立校園透明站，整合「維修追蹤」與「AI 校務導航」。看板顯示案件地點、狀態、預計完成日、最後更新與時間軸；AI 以公告與辦法建立可追溯索引，回答附文件、段落與日期，無依據時拒答並導向承辦單位。差異是把進度透明與規範問答放在同一可信入口。\n3.跨域與 Codex：AI／資訊成員負責資料索引、問答與程式；管理／公共事務成員盤點流程、定義權責；工程或場域使用者檢查維修欄位與情境。Codex 協助產生資料模型、介面、檢索流程與測試案例。\n4.成果與驗證：完成可操作 Web 原型、10 筆維修示例、20 份公告、引用來源與錯誤回報。以三項任務測試，目標任務完成率≥80%、回答有來源≥90%、30 秒內找到案件狀態。決賽 09:00–15:00：架構30分、介面60分、看板90分、AI60分、整合測試60分、彩排60分；Token 15%需求、35%程式、25%AI、15%測試、10%展示。事前只準備去識別化資料、線框與測試腳本，成品於當天完成。'''
for para in text.split('\n'):
    p=concise.add_paragraph(para); p.paragraph_format.space_after=Pt(5); p.paragraph_format.line_spacing=1.08
concise.add_heading('規範對應',1)
concise.add_paragraph('本提案對應初賽四題，並明列 6 小時與 Codex Token 規劃。報名須符合：長庚在學學生 2–8 人、至少 2 個系／所、1 位指導教授；每位成員完成校內 Email 驗證；2026/11/23 前完成送出。',style='Small Note')
add_footer(concise)
concise.save(OUT/'校園資訊整合平台_500字說明.docx')

# Poster
a4=Document(); style_doc(a4); sec=a4.sections[0]; sec.page_height=Inches(11.69); sec.page_width=Inches(8.27); sec.top_margin=Inches(0.45); sec.bottom_margin=Inches(0.45); sec.left_margin=Inches(0.5); sec.right_margin=Inches(0.5)
add_title(a4,'校園透明站 Campus OpenHub','把校務資訊變得可查、可追、可理解')
p=a4.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.LEFT; r=p.add_run('一個入口，串起維修進度與校務規範'); r.bold=True; r.font.size=Pt(15); r.font.color.rgb=RGBColor.from_string(TEAL)
# three feature table
add_table(a4,['校園痛點','我們的做法','決賽可展示'],[
('公告與辦法分散、版本難辨','AI 校務導航：回答附來源、段落、日期；無依據就說明','自然語言提問 → 可追溯答案'),
('維修反映後看不到進度','維修追蹤：案件、位置、狀態、預計完成日、時間軸','篩選案件 → 查看進度 → 回報過期'),
('行政重複回答、資訊易過期','顯示最後更新與承辦單位，保留錯誤回報入口','用任務測試完成率與引用率')], [2.0,3.2,2.1], header_fill=NAVY, font_size=9)
a4.add_heading('使用流程',1)
a4.add_paragraph('學生提出問題／查詢地點  →  透明站辨識需求  →  顯示維修狀態或 AI 引用答案  →  不確定時提供承辦單位與回報入口',style='Normal')
a4.add_heading('決賽六小時成果',1)
add_table(a4,['09:00–12:00','12:00–14:00','14:00–15:00'],[
('頁面骨架、資料模型、維修看板','公告索引、AI 問答、兩模組整合','任務測試、修正、彩排與展示')],[2.25,2.25,2.25],header_fill=TEAL,font_size=9)
a4.add_heading('驗證指標',1)
add_table(a4,['任務完成率','回答具來源','找狀態時間'],[('≥ 80%','≥ 90%','≤ 30 秒')],[2.25,2.25,2.25],header_fill=NAVY,font_size=11)
a4.add_paragraph('跨域分工：AI／資訊 × 管理／公共事務 × 工程／場域使用者　｜　事前準備資料與線框，核心作品於 2026/12/05 現場完成',style='Small Note')
add_footer(a4,'CGU AI Hackathon 2026｜校園資訊整合平台')
a4.save(OUT/'校園資訊整合平台_A4海報.docx')

# write char count
print('concise_chars',len(re.sub(r'\s','',text)))
print('files',*[str(p) for p in OUT.glob('校園資訊整合平台_*.docx')],sep='\n')

