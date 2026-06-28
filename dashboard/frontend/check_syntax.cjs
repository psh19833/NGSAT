const fs = require('fs');
const code = fs.readFileSync('src/App.jsx', 'utf8');
const lines = code.split('\n');
let paren = 0, brace = 0, bracket = 0;
let in_string = false, in_template = false, string_char = '';
for (let i = 0; i < lines.length; i++) {
  const line = lines[i];
  for (let c = 0; c < line.length; c++) {
    const ch = line[c];
    const prev = c > 0 ? line[c-1] : '';

    // Handle strings
    if (!in_template && !in_string && (ch === '"' || ch === "'" || ch === '`')) {
      in_string = true; string_char = ch;
      if (ch === '`') in_template = true;
      continue;
    }
    if (in_string && ch === string_char && prev !== '\\') {
      in_string = false; in_template = false;
      continue;
    }
    if (in_template && ch === '`' && prev !== '\\') {
      in_template = false; in_string = false;
      continue;
    }
    if (in_string || in_template) continue;

    if (!in_string && !in_template) {
      if (ch === '(') paren++;
      if (ch === ')') paren--;
      if (ch === '{') brace++;
      if (ch === '}') brace--;
      if (ch === '[') bracket++;
      if (ch === ']') bracket--;
    }
  }
  if (brace === 0 && paren === 0 && bracket === 0) {
    // all balanced at this line
  }
}
// Find the last line where brace is still positive
console.log(`Final: paren=${paren} brace=${brace} bracket=${bracket}`);
