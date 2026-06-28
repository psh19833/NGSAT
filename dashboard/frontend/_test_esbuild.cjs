// Test esbuild JSX transformation
const esb = require('esbuild');
const viteEsb = require('vite/node_modules/esbuild');

const samples = [
  { name: 'simple div', code: 'export default () => <div>hello</div>' },
  { name: 'fragment', code: 'export default () => <><div>a</div><div>b</div></>' },
  { name: 'ternary in class', code: 'export default function App({x}) { return <div className={x ? \"a\" : \"b\"}>hello</div> }' },
  { name: 'arrow onclick', code: 'export default function App() { return <div onClick={() => setX(null)}>click</div> }' },
];

for (const esb of [esb, viteEsb]) {
  const label = esb === esb ? 'ROOT esbuild' : 'VITE esbuild';
  for (const s of samples) {
    try {
      const r = esb.transformSync(s.code, { loader: 'jsx', jsx: 'automatic' });
      console.log(`${label}: ✅ ${s.name}`);
    } catch (e) {
      console.log(`${label}: ❌ ${s.name}: ${e.message.slice(0, 100)}`);
    }
  }
}
