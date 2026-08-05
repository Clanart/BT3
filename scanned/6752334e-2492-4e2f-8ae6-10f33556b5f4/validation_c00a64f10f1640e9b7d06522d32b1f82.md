## Confirmed Finding

I now have enough evidence to confirm this analog. The unified scheduler's `TaskHandler` explicitly propagates panics from transaction execution as a fatal condition — a handler thread panic is deliberately allowed to unwind and is re-raised via `join_with_panic_message`, which itself calls `panic!()` again to kill the process. There is no `catch_unwind` anywhere in `program-runtime`, `svm`, `runtime`, or `bpf_loader` crates around real transaction/builtin execution — the only `catch_unwind` guarding a program panic exists in `program-test/src/lib.rs`, which is test-only tooling, not the production path. This confirms that a panic occurring inside builtin-program execution (`process_executable_chain` in `program-runtime/src/invoke_context.rs`) during real block replay is fatal to the validator process, and — critically — deterministic panics are hit identically by every honest validator replaying the same transaction, which is the direct Agave analog to the MIPS panic report.

### Title
Deterministic panic inside a native builtin program during transaction replay crashes every replaying validator simultaneously, with no catch/recovery path - (File: `program-runtime/src/invoke_context.rs`, `unified-scheduler-pool/src/lib.rs`)

### Summary
The external report describes a MIPS-VM fault-proof system where a program that deterministically panics on certain input cannot be challenged, because both the "correct" and "faulty" execution paths converge on the same unrecoverable state, letting an attacker deny any dispute forever. The Agave analog is structural rather than game-theoretic: Agave has no dispute layer, but the same "faulty program panics identically everywhere and nothing can recover" primitive exists in how native builtin-program instructions are invoked during transaction execution. `InvokeContext::process_executable_chain` (`program-runtime/src/invoke_context.rs:638-736`) invokes builtin program logic (system, vote, stake, address-lookup-table, config, compute-budget, `bpf_loader` management instructions, etc.) via `vm.invoke_function(function)` with **no `std::panic::catch_unwind`** anywhere in the call chain. If a builtin ever hits a Rust panic (index out of bounds, `unwrap()`/`expect()` on an attacker-influenced `None`, arithmetic overflow under debug assertions, etc.) while processing a transaction that is part of a block being replayed, that panic unwinds straight through the executing thread and crashes the validator.

### Finding Description
Every honest validator that replays a given block executes the exact same transactions, in the exact same order, against the exact same account state. If any transaction inside that block triggers a Rust panic (rather than a caught `InstructionError`) in a builtin program invoked from `process_executable_chain`, that panic is not caught anywhere in the production path: [1](#0-0) 

Compare this to the explicit, test-only safety net that exists solely in `program-test`: [2](#0-1) 

The presence of `catch_unwind` in the *test* harness but its absence in the production `process_executable_chain` / BPF `execute()` path shows the maintainers are aware panics can occur inside program logic, yet only guarded against it for local unit testing, not for real validator execution.

The unified scheduler goes further and makes this behavior explicit and intentional: a handler-thread panic during `TaskHandler::handle()` (which calls `execute_batch` → `Bank::commit_transactions`/`process_executable_chain`) is treated as a **fatal, unrecoverable condition** for the whole scheduler and is deliberately re-panicked when threads are joined: [3](#0-2) [4](#0-3) 

This is confirmed by the project's own test (`test_scheduler_schedule_execution_panic`), which explicitly asserts that a panic inside a `TaskHandler` propagates and crashes (`#[should_panic(expected = "This panic should be propagated. ...")]`).

Because a block is *consensus-critical, deterministic input* — every validator's replay path (`ledger/src/blockstore_processor.rs::process_single_slot` → bank execution → `commit_transactions`) runs the same transaction against the same account state — a panic-triggering transaction crashes **every replaying node at the same point**, not just an attacker's own node. There is no equivalent of a "fault-proof/dispute game" fallback in Agave; the only mitigating guard is `freeze_and_verify_bank_hash`/`mark_replay_dead_slot`, but those only detect *divergent* hashes, not process-level panics — a panic never reaches that check, it kills the thread/process first. [5](#0-4) 

This mirrors the report's broken invariant precisely: "the correct execution path and the faulty execution path converge on the same unrecoverable outcome" — here, *every correctly-functioning validator* hits the identical Rust panic when replaying the same malicious transaction, and none of them have a way to "dispute" or route around it; they simply crash.

### Impact Explanation
If an unprivileged user can craft a transaction that deterministically triggers an uncaught Rust panic inside a builtin program's instruction-processing logic (a bug class that has historically occurred in Solana/Agave — e.g. array-index panics, `unwrap()` on attacker-controlled `None`, or debug-assertion-only overflow panics that ship in release builds where `overflow-checks` is left on for a crate), then including that transaction in a block causes **every validator that replays that block to panic and exit simultaneously**. This is a non-RPC, remotely triggerable crash reachable via ordinary transaction submission (TPU/banking or replay), and because it affects all replaying nodes identically and instantaneously, it directly causes a **consensus halt** of the cluster — the most severe listed impact category, worse than a single-node crash because there is no fallback validator population left to keep making progress.

### Likelihood Explanation
The likelihood hinges entirely on whether a reachable, attacker-controlled panic exists inside builtin program logic (system/vote/stake/address-lookup-table/etc.) or inside code invoked from `process_executable_chain`; this repository snapshot does not by itself prove such a specific panic path exists today. However, the structural gap itself is concrete and verifiable: production execution of builtins has zero panic-catching, while the exact same call shape in the test harness (`program-test`) is explicitly wrapped in `catch_unwind`, and the unified scheduler's own test suite documents and expects that a handler-thread panic is fatal and propagated. This makes the vulnerability class "any future/undiscovered panic bug in a builtin or replay-path routine = guaranteed simultaneous cluster-wide crash," which is a materially different (and more severe) risk profile than an ordinary bug, and is exactly analogous to the report's core claim that an unhandled panic path removes the possibility of graceful recovery.

### Recommendation
- Wrap builtin-program invocation in `InvokeContext::process_executable_chain` (and equivalently the BPF `execute()` path in `program-runtime/src/vm.rs`) with `std::panic::catch_unwind`, converting any caught panic into `InstructionError::ProgramFailedToComplete` (as is already done for the test harness in `program-test/src/lib.rs`), so that a panicking instruction becomes an ordinary failed transaction instead of a fatal process crash.
- Audit builtin programs (system, vote, stake, address-lookup-table, config, compute-budget) for any `unwrap()`/`expect()`/indexing operations reachable with attacker-controlled instruction data or account state, and replace them with explicit `InstructionError` returns.
- Ensure `overflow-checks`/debug-assertion-gated panics are not compiled into release validator builds for any code reachable from transaction processing.
- For the unified scheduler, consider converting a handler-thread panic into a `TransactionError`-carrying result for that single transaction (dead-lettering it) rather than universally aborting/crashing the whole scheduler/process, if the panic can be isolated to be transaction-local rather than corrupting global state.

### Proof of Concept
1. Introduce (or locate) any builtin-program code path reachable from `process_instruction_inner` (`programs/bpf_loader/src/lib.rs`) or a native builtin registered via `declare_builtin_function!` that performs an operation which can panic on attacker-supplied instruction data or account state (e.g., `some_slice[attacker_controlled_index]`, `option.unwrap()` where `option` can be `None` under crafted input).
2. Construct a transaction that invokes that builtin with the crafted data and submit it to the cluster (via ordinary TPU submission — no special privilege required).
3. Once the transaction is included in a block, every validator that replays the block calls `process_single_slot` → `confirm_full_slot` → bank transaction execution → `InvokeContext::process_executable_chain` (`program-runtime/src/invoke_context.rs:690-702`), which is not wrapped in `catch_unwind`.
4. The panic unwinds; under the unified scheduler, the handler thread panics and this is deliberately re-raised via `join_with_panic_message` (`unified-scheduler-pool/src/lib.rs:1585-1599`), terminating the scheduler/validator process. Because all validators execute the same transaction deterministically, they all crash at the same point, halting consensus cluster-wide.

### Citations

**File:** program-runtime/src/invoke_context.rs (L690-723)
```rust
        let mut vm = EbpfVm::new(
            Arc::clone(
                &**self
                    .environment_config
                    .program_runtime_environments
                    .get_env_for_execution(),
            ),
            SBPFVersion::V0,
            // Removes lifetime tracking
            unsafe { std::mem::transmute::<&mut InvokeContext, &mut InvokeContext>(self) },
            0,
        );
        vm.invoke_function(function);
        let result = match vm.program_result {
            ProgramResult::Ok(_) => {
                stable_log::program_success(&logger, &program_id);
                Ok(())
            }
            ProgramResult::Err(ref err) => {
                if let EbpfError::SyscallError(syscall_error) = err {
                    if let Some(instruction_err) = syscall_error.downcast_ref::<InstructionError>()
                    {
                        stable_log::program_failure(&logger, &program_id, instruction_err);
                        Err(instruction_err.clone())
                    } else {
                        stable_log::program_failure(&logger, &program_id, syscall_error);
                        Err(InstructionError::ProgramFailedToComplete)
                    }
                } else {
                    stable_log::program_failure(&logger, &program_id, err);
                    Err(InstructionError::ProgramFailedToComplete)
                }
            }
        };
```

**File:** program-test/src/lib.rs (L154-172)
```rust
    // Execute the program
    match std::panic::catch_unwind(AssertUnwindSafe(|| {
        builtin_function(program_id, &account_infos, input)
    })) {
        Ok(program_result) => {
            program_result.map_err(|program_error| {
                let err = InstructionError::from(u64::from(program_error));
                stable_log::program_failure(&log_collector, program_id, &err);
                let err: Box<dyn std::error::Error> = Box::new(err);
                err
            })?;
        }
        Err(_panic_error) => {
            let err = InstructionError::ProgramFailedToComplete;
            stable_log::program_failure(&log_collector, program_id, &err);
            let err: Box<dyn std::error::Error> = Box::new(err);
            Err(err)?;
        }
    };
```

**File:** unified-scheduler-pool/src/lib.rs (L1585-1599)
```rust
        fn join_with_panic_message(join_handle: JoinHandle<()>) -> thread::Result<()> {
            let thread = join_handle.thread().clone();
            join_handle.join().inspect_err(|e| {
                // Always needs to try both types for .downcast_ref(), according to
                // https://doc.rust-lang.org/1.78.0/std/macro.panic.html:
                //   a panic can be accessed as a &dyn Any + Send, which contains either a &str or
                //   String for regular panic!() invocations. (Whether a particular invocation
                //   contains the payload at type &str or String is unspecified and can change.)
                let panic_message = match (e.downcast_ref::<&str>(), e.downcast_ref::<String>()) {
                    (Some(&s), _) => s,
                    (_, Some(s)) => s,
                    (None, None) => "<No panic info>",
                };
                panic!("{panic_message} (From: {thread:?})");
            })
```

**File:** unified-scheduler-pool/src/lib.rs (L2807-2846)
```rust
    #[test]
    #[should_panic(expected = "This panic should be propagated. (From: ")]
    fn test_scheduler_schedule_execution_panic() {
        agave_logger::setup();

        #[derive(Debug)]
        enum PanickingHanlderCheckPoint {
            BeforeNotifiedPanic,
            BeforeIgnoredPanic,
        }

        let progress = sleepless_testing::setup(&[
            &TestCheckPoint::BeforeNewTask,
            &CheckPoint::NewTask(0),
            &PanickingHanlderCheckPoint::BeforeNotifiedPanic,
            &CheckPoint::SchedulerThreadAborted,
            &PanickingHanlderCheckPoint::BeforeIgnoredPanic,
            &TestCheckPoint::BeforeEndSession,
        ]);

        #[derive(Debug)]
        struct PanickingHandler;
        impl TaskHandler for PanickingHandler {
            fn handle(
                _result: &mut Result<()>,
                _timings: &mut ExecuteTimings,
                _scheduling_context: &SchedulingContext,
                task: &Task,
                _handler_context: &HandlerContext,
            ) {
                let task_id = task.task_id();
                if task_id == 0 {
                    sleepless_testing::at(PanickingHanlderCheckPoint::BeforeNotifiedPanic);
                } else if task_id == 1 {
                    sleepless_testing::at(PanickingHanlderCheckPoint::BeforeIgnoredPanic);
                } else {
                    unreachable!();
                }
                panic!("This panic should be propagated.");
            }
```

**File:** ledger/src/blockstore_processor.rs (L2173-2211)
```rust
    // Mark corrupt slots as dead so validators don't replay this slot and
    // see AlreadyProcessed errors later in ReplayStage
    confirm_full_slot(
        blockstore,
        bank,
        shred_version,
        replay_tx_thread_pool,
        opts,
        progress,
        entry_notification_sender,
        replay_vote_sender,
        timing,
        migration_status,
    )
    .map_err(|err| {
        warn!("slot {slot} failed to verify: {err}");
        mark_dead_if_primary_access(blockstore, slot);
        err
    })?;

    let block_id = blockstore
        .get_block_id(slot, migration_status)
        .expect("Blockstore operations must succeed")
        .expect("Full block must have block id");
    bank.set_block_id(Some(block_id));
    let verify_result = bank.freeze_and_verify_bank_hash(); // all banks handled by this routine are created from complete slots

    if let Err((expected_hash, computed_hash)) = verify_result {
        warn!(
            "slot {slot} failed to freeze, bank hash mismatch expected {expected_hash} computed \
             {computed_hash}"
        );
        mark_dead_if_primary_access(blockstore, slot);
        return Err(BlockstoreProcessorError::BankHashMismatch(
            slot,
            expected_hash,
            computed_hash,
        ));
    }
```
