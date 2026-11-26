// 从 illus/*.svg 生成效果稿用的 illus.js（只供设计稿）。用法：node docs/design/v36/build-illus.mjs
import fs from 'fs'; import path from 'path'; import {fileURLToPath} from 'url';
const dir=path.dirname(fileURLToPath(import.meta.url));
const out={};
for(const f of fs.readdirSync(path.join(dir,'illus')).filter(f=>f.endsWith('.svg'))){
  out[f.replace('.svg','')]=fs.readFileSync(path.join(dir,'illus',f),'utf8').replace(/<style>[\s\S]*?<\/style>/,'').replace(/<!--[\s\S]*?-->/g,'').replace(/\s*\n\s*/g,' ').trim();
}
fs.writeFileSync(path.join(dir,'illus.js'),'/* 由 build-illus.mjs 从 illus/*.svg 生成，不要手改 */\nwindow.ILLUS='+JSON.stringify(out)+';\n');
console.log(Object.keys(out).join(' '));
