// d8 Shared-Everything worker oracle.
// Module ABI: make() -> shared state; workers()/iterations() -> i32;
// run(state, iterations, worker_id) -> i64; check(state, sum_of_run_results) -> i32.
const path = arguments[0];
const bytes = readbuffer(path);
const tag = path.split('/').pop();

function workerCode() {
  onmessage = ({data: msg}) => {
    try {
      const instance = new WebAssembly.Instance(msg.module, {});
      const result = instance.exports.run(msg.state, msg.iterations, msg.id);
      postMessage({ok: true, result: String(result)});
    } catch (error) {
      postMessage({ok: false, error: error.name + ': ' + error.message});
    }
  };
}

try {
  const module = new WebAssembly.Module(bytes);
  const instance = new WebAssembly.Instance(module, {});
  const state = instance.exports.make();
  const count = instance.exports.workers();
  const iterations = instance.exports.iterations();
  const workers = [];
  for (let id = 0; id < count; id++) {
    const worker = new Worker(workerCode, {type: 'function'});
    workers.push(worker);
    worker.postMessage({module, state, iterations, id});
  }
  let sum = 0n;
  let failure = null;
  for (const worker of workers) {
    const message = worker.getMessage();
    if (!message.ok) failure = message.error;
    else sum += BigInt(message.result);
    worker.terminate();
  }
  if (failure !== null) {
    print(tag + ' => INSTANTIATE-FAIL: worker ' + failure);
  } else {
    const result = instance.exports.check(state, sum);
    print(tag + ' => OK ' + String(result));
  }
} catch (error) {
  print(tag + ' => INSTANTIATE-FAIL: ' + error.name + ': ' + error.message);
}
