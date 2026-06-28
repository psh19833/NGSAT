const fs = require('fs');
const code = fs.readFileSync('src/App.jsx', 'utf8');
const lines = code.split('\n');
let brace = 0;
for (let i = 0; i < lines.length; i++) {
  const line = lines[i];
  for (const ch of line) {
    if (ch === '{') brace++;
    if (ch === '}') brace--;
  }
  if (brace === 0 && line.includes('const ') && line.includes('=>')) {
    // function definition line — check if braces are balanced
  }
}
console.log('Final brace:', brace);

// Find which top-level block is unbalanced
let blocks = code.split(/\n(?=const |export |function )/);
for (const block of blocks) {
  let b = 0;
  for (const ch of block) {
    if (ch === '{') b++;
    if (ch === '}') b--;
  }
  if (b !== 0) {
    console.log('UNBALANCED block:', block.slice(0, 100).replace(/\n/g, '\\n'));
  }
}
