use std::{
    sync::{Arc, Barrier},
    thread,
};
use wasmtime::{
    Config, Engine, Instance, MemoryType, Module, OptLevel, Result, SharedMemory, Store,
};

#[derive(Clone, Copy)]
enum Profile {
    Opt0,
    Opt2,
    ExplicitBounds,
}

impl Profile {
    fn name(self) -> &'static str {
        match self {
            Self::Opt0 => "cranelift-opt0",
            Self::Opt2 => "cranelift-opt2",
            Self::ExplicitBounds => "cranelift-explicit-bounds",
        }
    }
}

fn engine(profile: Profile) -> Result<Engine> {
    let mut config = Config::new();
    config.wasm_threads(true).shared_memory(true);
    config.cranelift_opt_level(match profile {
        Profile::Opt0 => OptLevel::None,
        Profile::Opt2 | Profile::ExplicitBounds => OptLevel::Speed,
    });
    if matches!(profile, Profile::ExplicitBounds) {
        config
            .memory_reservation(9 * 65_536)
            .memory_reservation_for_growth(0)
            .memory_guard_size(0)
            .memory_may_move(true);
    }
    Engine::new(&config)
}

fn instantiate(engine: &Engine, module: &Module, memory: SharedMemory) -> (Store<()>, Instance) {
    let mut store = Store::new(engine, ());
    let instance = Instance::new(&mut store, module, &[memory.into()]).unwrap();
    (store, instance)
}

fn atomic_counter(engine: &Engine) -> Result<()> {
    const THREADS: usize = 6;
    const LOOPS: i32 = 20_000;
    let module = Module::new(
        engine,
        r#"(module
          (import "m" "memory" (memory 1 1 shared))
          (func (export "run") (param $n i32) (result i64)
            (local $i i32) (local $sum i64)
            (loop $again
              (local.set $sum (i64.add (local.get $sum) (i64.extend_i32_u
                (i32.atomic.rmw.add (i32.const 0) (i32.const 1)))))
              (local.set $i (i32.add (local.get $i) (i32.const 1)))
              (br_if $again (i32.lt_u (local.get $i) (local.get $n))))
            (local.get $sum))
          (func (export "get") (result i32) (i32.atomic.load (i32.const 0))))"#,
    )?;
    let memory = SharedMemory::new(engine, MemoryType::shared(1, 1))?;
    let mut handles = Vec::new();
    for _ in 0..THREADS {
        let engine = engine.clone();
        let module = module.clone();
        let memory = memory.clone();
        handles.push(thread::spawn(move || {
            let (mut store, instance) = instantiate(&engine, &module, memory);
            instance
                .get_typed_func::<i32, i64>(&mut store, "run")
                .unwrap()
                .call(&mut store, LOOPS)
                .unwrap()
        }));
    }
    let sum: i64 = handles.into_iter().map(|h| h.join().unwrap()).sum();
    let (mut store, instance) = instantiate(engine, &module, memory);
    let final_value = instance
        .get_typed_func::<(), i32>(&mut store, "get")?
        .call(&mut store, ())?;
    let total = THREADS as i64 * LOOPS as i64;
    assert_eq!(final_value as i64, total);
    assert_eq!(sum, total * (total - 1) / 2);
    Ok(())
}

fn release_acquire_publication(engine: &Engine) -> Result<()> {
    const LOOPS: i32 = 100_000;
    let module = Module::new(
        engine,
        r#"(module
          (import "m" "memory" (memory 1 1 shared))
          (func (export "producer") (param $n i32)
            (local $i i32)
            (local.set $i (i32.const 1))
            (loop $round
              (loop $wait
                (br_if $wait (i32.ne (i32.atomic.load (i32.const 4)) (i32.const 0))))
              (i32.store (i32.const 8) (local.get $i))
              (i32.atomic.store (i32.const 4) (i32.const 1))
              (local.set $i (i32.add (local.get $i) (i32.const 1)))
              (br_if $round (i32.le_u (local.get $i) (local.get $n)))))
          (func (export "consumer") (param $n i32) (result i32)
            (local $i i32) (local $errors i32)
            (local.set $i (i32.const 1))
            (loop $round
              (loop $wait
                (br_if $wait (i32.ne (i32.atomic.load (i32.const 4)) (i32.const 1))))
              (local.set $errors (i32.add (local.get $errors)
                (i32.ne (i32.load (i32.const 8)) (local.get $i))))
              (i32.atomic.store (i32.const 4) (i32.const 0))
              (local.set $i (i32.add (local.get $i) (i32.const 1)))
              (br_if $round (i32.le_u (local.get $i) (local.get $n))))
            (local.get $errors)))"#,
    )?;
    let memory = SharedMemory::new(engine, MemoryType::shared(1, 1))?;
    let producer = {
        let engine = engine.clone();
        let module = module.clone();
        let memory = memory.clone();
        thread::spawn(move || {
            let (mut store, instance) = instantiate(&engine, &module, memory);
            instance
                .get_typed_func::<i32, ()>(&mut store, "producer")
                .unwrap()
                .call(&mut store, LOOPS)
                .unwrap();
        })
    };
    let consumer = {
        let engine = engine.clone();
        let module = module.clone();
        thread::spawn(move || {
            let (mut store, instance) = instantiate(&engine, &module, memory);
            instance
                .get_typed_func::<i32, i32>(&mut store, "consumer")
                .unwrap()
                .call(&mut store, LOOPS)
                .unwrap()
        })
    };
    producer.join().unwrap();
    assert_eq!(consumer.join().unwrap(), 0);
    Ok(())
}

fn wait_notify(engine: &Engine) -> Result<()> {
    let module = Module::new(
        engine,
        r#"(module
          (import "m" "memory" (memory 1 1 shared))
          (func (export "waiter") (result i32)
            (drop (memory.atomic.wait32 (i32.const 0) (i32.const 0) (i64.const -1)))
            (i32.atomic.load (i32.const 4)))
          (func (export "notifier") (result i32)
            (i32.atomic.store (i32.const 4) (i32.const 0x5A5A1234))
            (i32.atomic.store (i32.const 0) (i32.const 1))
            (memory.atomic.notify (i32.const 0) (i32.const 1))))"#,
    )?;
    for _ in 0..100 {
        let memory = SharedMemory::new(engine, MemoryType::shared(1, 1))?;
        let waiter = {
            let engine = engine.clone();
            let module = module.clone();
            let memory = memory.clone();
            thread::spawn(move || {
                let (mut store, instance) = instantiate(&engine, &module, memory);
                instance
                    .get_typed_func::<(), i32>(&mut store, "waiter")
                    .unwrap()
                    .call(&mut store, ())
                    .unwrap()
            })
        };
        let (mut store, instance) = instantiate(engine, &module, memory);
        let notified = instance
            .get_typed_func::<(), i32>(&mut store, "notifier")?
            .call(&mut store, ())?;
        assert!(notified == 0 || notified == 1);
        assert_eq!(waiter.join().unwrap(), 0x5A5A1234);
    }
    Ok(())
}

fn grow_publication(engine: &Engine) -> Result<()> {
    let module = Module::new(
        engine,
        r#"(module
          (import "m" "memory" (memory 1 2 shared))
          (func (export "grower") (result i32)
            (local $old i32)
            (local.set $old (memory.grow (i32.const 1)))
            (i32.atomic.store (i32.const 65536) (i32.const 0x12345678))
            (i32.atomic.store (i32.const 0) (i32.const 1))
            (drop (memory.atomic.notify (i32.const 0) (i32.const 1)))
            (local.get $old))
          (func (export "reader") (result i64)
            (drop (memory.atomic.wait32 (i32.const 0) (i32.const 0) (i64.const -1)))
            (i64.or
              (i64.shl (i64.extend_i32_u (memory.size)) (i64.const 32))
              (i64.extend_i32_u (i32.atomic.load (i32.const 65536))))))"#,
    )?;
    let memory = SharedMemory::new(engine, MemoryType::shared(1, 2))?;
    let reader = {
        let engine = engine.clone();
        let module = module.clone();
        let memory = memory.clone();
        thread::spawn(move || {
            let (mut store, instance) = instantiate(&engine, &module, memory);
            instance
                .get_typed_func::<(), i64>(&mut store, "reader")
                .unwrap()
                .call(&mut store, ())
                .unwrap()
        })
    };
    let (mut store, instance) = instantiate(engine, &module, memory);
    let old = instance
        .get_typed_func::<(), i32>(&mut store, "grower")?
        .call(&mut store, ())?;
    assert_eq!(old, 1);
    assert_eq!(reader.join().unwrap() as u64, (2_u64 << 32) | 0x12345678);
    Ok(())
}

fn concurrent_grow(engine: &Engine) -> Result<()> {
    const THREADS: usize = 8;
    let module = Module::new(
        engine,
        r#"(module
          (import "m" "memory" (memory 1 9 shared))
          (func (export "grow") (result i32) (memory.grow (i32.const 1)))
          (func (export "size") (result i32) (memory.size)))"#,
    )?;
    let memory = SharedMemory::new(engine, MemoryType::shared(1, 9))?;
    let mut handles = Vec::new();
    for _ in 0..THREADS {
        let engine = engine.clone();
        let module = module.clone();
        let memory = memory.clone();
        handles.push(thread::spawn(move || {
            let (mut store, instance) = instantiate(&engine, &module, memory);
            instance
                .get_typed_func::<(), i32>(&mut store, "grow")
                .unwrap()
                .call(&mut store, ())
                .unwrap()
        }));
    }
    let mut old_sizes: Vec<i32> = handles.into_iter().map(|h| h.join().unwrap()).collect();
    old_sizes.sort_unstable();
    assert_eq!(old_sizes, (1..=THREADS as i32).collect::<Vec<_>>());
    assert_eq!(memory.size(), 9);
    Ok(())
}

fn concurrent_grow_and_access(engine: &Engine) -> Result<()> {
    const GROWERS: usize = 4;
    const READERS: usize = 4;
    const GROWS_PER_THREAD: i32 = 4;
    const READ_LOOPS: i32 = 50_000;
    const ROUNDS: usize = 10;

    let module = Module::new(
        engine,
        r#"(module
          (import "m" "memory" (memory 1 9 shared))
          (func (export "grow_many") (param $n i32) (result i64)
            (local $i i32) (local $old i32) (local $ok i32) (local $sum i32)
            (loop $again
              (local.set $old (memory.grow (i32.const 1)))
              (if (i32.ne (local.get $old) (i32.const -1))
                (then
                  (local.set $ok (i32.add (local.get $ok) (i32.const 1)))
                  (local.set $sum (i32.add (local.get $sum) (local.get $old)))))
              (drop (i32.atomic.rmw.add (i32.const 0) (i32.const 1)))
              (local.set $i (i32.add (local.get $i) (i32.const 1)))
              (br_if $again (i32.lt_u (local.get $i) (local.get $n))))
            (i64.or
              (i64.shl (i64.extend_i32_u (local.get $ok)) (i64.const 32))
              (i64.extend_i32_u (local.get $sum))))
          (func (export "touch_last_page") (param $n i32) (result i32)
            (local $i i32) (local $pages i32) (local $addr i32)
            (loop $again
              (local.set $pages (memory.size))
              (local.set $addr
                (i32.shl (i32.sub (local.get $pages) (i32.const 1)) (i32.const 16)))
              (drop (i32.atomic.rmw.add (local.get $addr) (i32.const 1)))
              (local.set $i (i32.add (local.get $i) (i32.const 1)))
              (br_if $again (i32.lt_u (local.get $i) (local.get $n))))
            (memory.size)))"#,
    )?;

    for _ in 0..ROUNDS {
        let memory = SharedMemory::new(engine, MemoryType::shared(1, 9))?;
        let barrier = Arc::new(Barrier::new(GROWERS + READERS + 1));
        let mut growers = Vec::new();
        let mut readers = Vec::new();

        for _ in 0..GROWERS {
            let engine = engine.clone();
            let module = module.clone();
            let memory = memory.clone();
            let barrier = barrier.clone();
            growers.push(thread::spawn(move || {
                let (mut store, instance) = instantiate(&engine, &module, memory);
                let grow = instance
                    .get_typed_func::<i32, i64>(&mut store, "grow_many")
                    .unwrap();
                barrier.wait();
                grow.call(&mut store, GROWS_PER_THREAD).unwrap()
            }));
        }
        for _ in 0..READERS {
            let engine = engine.clone();
            let module = module.clone();
            let memory = memory.clone();
            let barrier = barrier.clone();
            readers.push(thread::spawn(move || {
                let (mut store, instance) = instantiate(&engine, &module, memory);
                let touch = instance
                    .get_typed_func::<i32, i32>(&mut store, "touch_last_page")
                    .unwrap();
                barrier.wait();
                touch.call(&mut store, READ_LOOPS).unwrap()
            }));
        }

        barrier.wait();
        let encoded: Vec<u64> = growers
            .into_iter()
            .map(|h| h.join().unwrap() as u64)
            .collect();
        for reader in readers {
            assert!((1..=9).contains(&reader.join().unwrap()));
        }
        let successes: u64 = encoded.iter().map(|v| v >> 32).sum();
        let old_size_sum: u64 = encoded.iter().map(|v| v & 0xffff_ffff).sum();
        assert_eq!(successes, 8);
        assert_eq!(old_size_sum, (1_u64..=8).sum::<u64>());
        assert_eq!(memory.size(), 9);
    }
    Ok(())
}

fn main() -> Result<()> {
    for profile in [Profile::Opt0, Profile::Opt2, Profile::ExplicitBounds] {
        let engine = engine(profile)?;
        atomic_counter(&engine)?;
        release_acquire_publication(&engine)?;
        wait_notify(&engine)?;
        grow_publication(&engine)?;
        concurrent_grow(&engine)?;
        concurrent_grow_and_access(&engine)?;
        println!("{}: 6/6 passed", profile.name());
    }
    println!("total: 18/18 passed");
    Ok(())
}
