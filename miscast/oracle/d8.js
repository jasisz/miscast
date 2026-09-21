// d8 oracle: d8 [flags] d8.js -- <module.wasm> [export=f] [args...]
const path = arguments[0];
const exp = arguments[1] || 'f';
const args = arguments.slice(2).map(s => /n$/.test(s) ? BigInt(s.slice(0, -1)) : Number(s));
const bytes = readbuffer(path);
const tag = path.split('/').pop();

if (exp === '__validate__') {
  print(tag + (WebAssembly.validate(bytes) ? ' => VALID' : ' => INVALID'));
} else {
  try {
    const module = new WebAssembly.Module(bytes);
    const instance = new WebAssembly.Instance(module, {});
    const fn = instance.exports[exp];
    if (typeof fn !== 'function') {
      print(tag + ' => NOEXPORT');
    } else {
      try {
        const result = fn(...args);
        const out = result === undefined ? '_'
          : (typeof result === 'number' || typeof result === 'bigint') ? String(result) : 'ref';
        print(tag + ' => OK ' + out);
      } catch (error) {
        print(tag + ' => TRAP (' + error.constructor.name + ')');
      }
    }
  } catch (error) {
    print(tag + ' => INSTANTIATE-FAIL: ' + error.message);
  }
}
