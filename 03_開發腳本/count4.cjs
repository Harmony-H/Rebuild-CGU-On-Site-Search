const fs=require('fs');
const path=require('path');
const input=path.join(__dirname,'..','02_網站原型','submit_500.html');
const output=path.join(__dirname,'..','05_檢查輸出','count.txt');
let s=fs.readFileSync(input,'utf8');
const a=s.indexOf('<body>'),b=s.indexOf('</body>');
s=s.slice(a+6,b).replace(/<[^>]+>/g,'').replace(/\s/g,'');
fs.writeFileSync(output,String([...s].length));
