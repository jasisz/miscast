//! mc-runner — a wasmtime embedder runner for miscast.
//!
//! `wasmtime run --invoke` is too shallow for a serious differential: it prints floats as text (lossy),
//! cannot set the GC collector / opt level per-run programmatically in one place, and re-instantiates per
//! call so it cannot replay a stateful invoke sequence. This runner instantiates once, runs a sequence of
//! invokes, and emits one JSON object per invoke with EXACT result bits (F32/F64 come back as their raw
//! bit pattern) plus trap/error capture, with the wasmtime config chosen by flags.
//!
//!   mc-runner <module.wasm> --invoke FUNC [--arg TYPE:VAL ...] [--collector drc|null|copying] [--opt 0|1|2|s]
//!   mc-runner <module.wasm> --seq FUNC1 FUNC2 ...        (stateful: one instance, invoked in order)
//!
//! TYPE:VAL is i32:N / i64:N / f32:BITS / f64:BITS (floats passed as their integer bit pattern). Output:
//!   {"ok":true,"results":["i32:5","f64:4591870180066957722"]}
//!   {"ok":false,"trap":true,"error":"wasm trap: ..."}

use std::env;
use std::process::exit;
use wasmtime::{Collector, Config, Engine, Instance, Module, OptLevel, Rooted, RootScope, Store, Val};

fn make_config(collector: &str, opt: &str) -> Config {
    let mut config = Config::new();
    config.wasm_gc(true);
    config.wasm_function_references(true);
    config.wasm_reference_types(true);
    config.collector(match collector {
        "null" => Collector::Null,
        "copying" => Collector::Copying,
        _ => Collector::DeferredReferenceCounting,
    });
    config.cranelift_opt_level(match opt {
        "0" => OptLevel::None,
        "s" => OptLevel::SpeedAndSize,
        _ => OptLevel::Speed,
    });
    config
}

/// Host-boundary GC rooting stress: the embedder holds a root (ManuallyRooted) to a GC struct across a
/// forced collection while it allocates garbage — the surface table_ops (in-wasm) never touches and where
/// three historical CVEs lived (GHSA-4873/5fhj/gwc9), holding an OwnedRooted (the new v46 lifecycle).
/// `func` must return a (ref struct) whose field 0 is an i32 id. We root the first object, churn
/// allocations + Store::gc(), then check the root still reads the
/// same id and still ref-eqs a second root to it. A wrong id / failed ref_eq / abort under ASan = a bug.
fn host_gc_stress(wasm: &str, func: &str, collector: &str, rounds: usize) -> anyhow::Result<String> {
    let engine = Engine::new(&make_config(collector, "2"))?;
    let module = Module::from_file(&engine, wasm)?;
    let mut store = Store::new(&engine, ());
    let instance = Instance::new(&mut store, &module, &[])?;
    let mk = instance
        .get_func(&mut store, func)
        .ok_or_else(|| anyhow::anyhow!("no exported function {func}"))?;

    let mut res = vec![Val::I32(0)];
    // allocate and manually-root the kept object (outlives any RootScope, survives GCs)
    let (kept, kept2, id0) = {
        let mut scope = RootScope::new(&mut store);
        mk.call(&mut scope, &[], &mut res)?;
        let anyref = match res[0] {
            Val::AnyRef(Some(r)) => r,
            _ => anyhow::bail!("{func} did not return a non-null gc reference"),
        };
        let sref = anyref
            .as_struct(&mut scope)?
            .ok_or_else(|| anyhow::anyhow!("returned reference is not a struct"))?;
        let id0 = match sref.field(&mut scope, 0)? {
            Val::I32(n) => n,
            _ => anyhow::bail!("struct field 0 is not i32"),
        };
        (sref.to_owned_rooted(&mut scope)?, sref.to_owned_rooted(&mut scope)?, id0)
    };

    // churn: allocate garbage and force a collection each round (copying relocates the kept object)
    for _ in 0..rounds {
        {
            let mut scope = RootScope::new(&mut store);
            mk.call(&mut scope, &[], &mut res)?; // garbage, freed when the scope drops
        }
        store.gc(None)?;
    }

    // the manually-held root must still read its id and still be the same object as the second root
    let mut scope = RootScope::new(&mut store);
    let k = kept.to_rooted(&mut scope);
    let k2 = kept2.to_rooted(&mut scope);
    let after = match k.field(&mut scope, 0)? {
        Val::I32(n) => n,
        _ => anyhow::bail!("field 0 not i32 after gc"),
    };
    let same = Rooted::ref_eq(&scope, &k, &k2)?;
    let survived = after == id0 && same;
    Ok(format!(
        "{{\"ok\":true,\"mode\":\"host-gc-stress\",\"collector\":\"{collector}\",\"rounds\":{rounds},\
         \"id0\":{id0},\"after\":{after},\"ref_eq\":{same},\"survived\":{survived}}}"
    ))
}

fn parse_val(s: &str) -> Option<Val> {
    let (t, v) = s.split_once(':')?;
    Some(match t {
        "i32" => Val::I32(v.parse().ok()?),
        "i64" => Val::I64(v.parse().ok()?),
        "f32" => Val::F32(v.parse::<u32>().ok()?),
        "f64" => Val::F64(v.parse::<u64>().ok()?),
        _ => return None,
    })
}

fn val_str(v: &Val) -> String {
    match v {
        Val::I32(n) => format!("i32:{n}"),
        Val::I64(n) => format!("i64:{n}"),
        Val::F32(b) => format!("f32:{b}"), // raw bits — no lossy text rounding
        Val::F64(b) => format!("f64:{b}"),
        Val::V128(x) => format!("v128:{}", x.as_u128()),
        Val::AnyRef(r) => format!("ref:any:{}", r.is_some() as u8),
        Val::ExternRef(r) => format!("ref:extern:{}", r.is_some() as u8),
        Val::FuncRef(r) => format!("ref:func:{}", r.is_some() as u8),
        _ => "ref:other".to_string(),
    }
}

fn json_escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"").replace('\n', " ").replace('\r', " ")
}

fn run(wasm: &str, funcs: &[String], args: &[Val], collector: &str, opt: &str) -> anyhow::Result<Vec<Vec<Val>>> {
    let engine = Engine::new(&make_config(collector, opt))?;
    let module = Module::from_file(&engine, wasm)?;
    let mut store = Store::new(&engine, ());
    let instance = Instance::new(&mut store, &module, &[])?; // miscast modules import nothing
    let mut out = Vec::new();
    for name in funcs {
        let f = instance
            .get_func(&mut store, name)
            .ok_or_else(|| anyhow::anyhow!("no exported function {name}"))?;
        let nres = f.ty(&store).results().len();
        let mut results = vec![Val::I32(0); nres];
        f.call(&mut store, args, &mut results)?;
        out.push(results);
    }
    Ok(out)
}

fn main() {
    let argv: Vec<String> = env::args().collect();
    let mut wasm: Option<String> = None;
    let mut funcs: Vec<String> = Vec::new();
    let mut args: Vec<Val> = Vec::new();
    let mut collector = "drc".to_string();
    let mut opt = "2".to_string();
    let mut host_stress: Option<String> = None;
    let mut rounds = 200usize;
    let mut i = 1;
    while i < argv.len() {
        let next = argv.get(i + 1).cloned(); // bounds-safe: a flag missing its value must not panic
        match argv[i].as_str() {
            "--invoke" => {
                if let Some(f) = next {
                    funcs.push(f);
                }
                i += 2;
            }
            "--host-gc-stress" => {
                host_stress = next;
                i += 2;
            }
            "--rounds" => {
                rounds = next.and_then(|s| s.parse().ok()).unwrap_or(200);
                i += 2;
            }
            "--seq" => {
                i += 1;
                while i < argv.len() && !argv[i].starts_with("--") {
                    funcs.push(argv[i].clone());
                    i += 1;
                }
            }
            "--arg" => {
                if let Some(v) = next.as_deref().and_then(parse_val) {
                    args.push(v);
                }
                i += 2;
            }
            "--collector" => {
                if let Some(c) = next {
                    collector = c;
                }
                i += 2;
            }
            "--opt" => {
                if let Some(o) = next {
                    opt = o;
                }
                i += 2;
            }
            s if !s.starts_with("--") => {
                wasm = Some(s.to_string());
                i += 1;
            }
            _ => i += 1,
        }
    }
    let wasm = match wasm {
        Some(w) => w,
        None => {
            eprintln!("usage: mc-runner <module.wasm> --invoke FUNC | --host-gc-stress FUNC [--collector C] [--opt L]");
            exit(2);
        }
    };
    if let Some(func) = host_stress {
        match host_gc_stress(&wasm, &func, &collector, rounds) {
            Ok(s) => println!("{s}"),
            Err(e) => {
                let msg = json_escape(&e.to_string());
                println!("{{\"ok\":false,\"error\":\"{msg}\"}}");
                exit(1);
            }
        }
        return;
    }
    if funcs.is_empty() {
        eprintln!("usage: mc-runner <module.wasm> --invoke FUNC | --host-gc-stress FUNC [--collector C] [--opt L]");
        exit(2);
    }
    match run(&wasm, &funcs, &args, &collector, &opt) {
        Ok(all) => {
            let blocks: Vec<String> = all
                .iter()
                .map(|res| {
                    let inner: Vec<String> = res.iter().map(|v| format!("\"{}\"", val_str(v))).collect();
                    format!("[{}]", inner.join(","))
                })
                .collect();
            // one line per run for a single invoke; an array of arrays for --seq
            if blocks.len() == 1 {
                println!("{{\"ok\":true,\"results\":{}}}", blocks[0]);
            } else {
                println!("{{\"ok\":true,\"seq\":[{}]}}", blocks.join(","));
            }
        }
        Err(e) => {
            let msg = json_escape(&e.to_string());
            let is_trap = format!("{e:?}").to_lowercase().contains("trap"); // reason lives in the cause chain
            println!("{{\"ok\":false,\"trap\":{is_trap},\"error\":\"{msg}\"}}");
            exit(1);
        }
    }
}
