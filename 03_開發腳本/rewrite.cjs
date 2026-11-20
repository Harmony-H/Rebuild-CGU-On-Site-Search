const fs=require('fs');
const path=require('path');
const web=path.join(__dirname,'..','02_網站原型');
const input=path.join(web,'submit_500.html');
const base=fs.readFileSync(input,'utf8');
const s=`<p><b>1.問題：</b>公告、辦法與表單分散在各單位網站、社群與 PDF，學生難辨有效版本；維修反映後也看不到進度，造成重複詢問。</p><p><b>2.構想：</b>建立校園透明站，整合維修追蹤與 AI 校務導航。看板呈現地點、狀態、預計完成日、更新時間與時間軸；AI 回答附文件、段落與日期，無依據時拒答並導向承辦單位。進度與規範在同一可信入口。</p><p><b>3.跨域與 Codex：</b>AI／資訊負責索引、問答與程式；管理／公共事務盤點流程與權責；工程或場域使用者檢查維修欄位。Codex 協助資料模型、介面、檢索與測試。</p><p><b>4.成果與驗證：</b>完成 Web 原型、10 筆維修示例、20 份公告、引用來源與錯誤回報。測試三項任務，目標完成率≥80%、回答有來源≥90%、30 秒內找到狀態。六小時：架構30分、介面60分、看板90分、AI60分、整合測試60分、彩排60分；Token 15%需求、35%程式、25%AI、15%測試、10%展示。事前只準備去識別化資料、線框與測試腳本，成品當天完成。</p>`;
const out=base.replace(/<p><b>1\.問題：[\s\S]*?<h2>規範對應<\/h2>[\s\S]*?<footer>/,s+'<footer>');
fs.writeFileSync(input,out);
console.log('rewritten');
