const fs=require('fs');
const path=require('path');
const input=path.join(__dirname,'..','02_網站原型','submit_500.html');
const output=path.join(__dirname,'..','05_檢查輸出','count.txt');
let s=fs.readFileSync(input,'utf8');
s=s.replace(/<style>[\s\S]*?<\/style>/,'').replace(/<[^>]+>/g,'').replace(/\s/g,'');
fs.writeFileSync(output,String([...s].length));
