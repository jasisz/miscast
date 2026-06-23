// V8 reference oracle: instantiate a wasm module and invoke an export.
//   node v8.js <module.wasm> [export=f] [args...]
// args ending in `n` are passed as BigInt (i64). Needs Node >= 22 (WasmGC).
// Output: "<file> => OK <val>" (val "_" for a void return) | "=> TRAP (...)"
//         | "=> NOEXPORT" | "=> INSTANTIATE-FAIL: ...".
const fs = require('fs');
(async () => {
  const p = process.argv[2];
  const exp = process.argv[3] || 'f';
  const args = process.argv.slice(4).map(s => /n$/.test(s) ? BigInt(s.slice(0, -1)) : Number(s));
  const tag = p.split('/').pop();
  try {
    const { instance } = await WebAssembly.instantiate(fs.readFileSync(p), {});
    const fn = instance.exports[exp];
    if (typeof fn !== 'function') { console.log(tag + " => NOEXPORT"); return; }
    try {
      const r = fn(...args);
      const out = r === undefined ? "_"
        : (typeof r === "number" || typeof r === "bigint") ? String(r) : "ref";
      console.log(tag + " => OK " + out);
    } catch (e) { console.log(tag + " => TRAP (" + e.constructor.name + ")"); }
  } catch (e) { console.log(tag + " => INSTANTIATE-FAIL: " + e.message); }
})();
