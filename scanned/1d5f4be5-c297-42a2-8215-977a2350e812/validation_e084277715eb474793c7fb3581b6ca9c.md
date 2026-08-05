### Title
Unhandled Rust panic in a native builtin program crashes the entire validator process instead of failing only the offending transaction - (File: `program-runtime/src/invoke_context.rs`, `metrics/src/metrics.rs`)

### Summary
The Kakarot report describes a call-routing bug where a callee contract's failure (a Cairo-level panic) is not converted into a recoverable error by the caller, so it propagates all the way up and crashes a component (the RPC) that should have survived a single failed call. Agave has a structurally identical broken invariant for **native/builtin programs**: unlike BPF programs, whose panics are already captured by the sBPF VM and turned into `InstructionError::ProgramFailedToComplete`, a Rust-level panic thrown by a native builtin (System, Vote, Stake, Config, Compute Budget, Address Lookup Table, the loaders, etc.) is invoked as an in-process Rust function call with **no `catch_unwind` boundary** in the production runtime path. Combined with the validator's global panic hook, which calls `std::process::exit(1)` on *any* panic anywhere in the process, this converts a single malicious transaction's triggered panic into a full validator crash.

### Finding Description
For BPF programs, `process_executable_chain` in `program-runtime/src/invoke_context.rs` runs the program inside an `EbpfVm`. Any fault (including a `sol_panic_`/`abort` syscall or a memory-access violation) surfaces as `ProgramResult::Err(EbpfError::…)`, which is mapped to `InstructionError::ProgramFailedToComplete` or another recoverable `InstructionError` and returned normally: [1](#0-0) 

However, for **native/builtin programs** (owner is `native_loader`), the very same function looks up the builtin's entrypoint and invokes it as a normal Rust function through `vm.invoke_function(function)`: [2](#0-1) 

Builtins are declared with the `declare_process_instruction!` macro, which just calls the inner Rust closure directly — there is no `catch_unwind` around it: [3](#0-2) 

The only place in the whole codebase that wraps a builtin invocation in `std::panic::catch_unwind` is the **test-only** `solana-program-test` harness (`invoke_builtin_function`), which explicitly notes it re-implements the panic-catching behavior for its mock environment: [4](#0-3) 

This proves the harness authors are aware that a native/builtin entrypoint can panic and that something must catch it — but that catching only exists in the test harness, not in the production `program-runtime`/`svm` path used by `banking_stage`/`replay_stage`.

Finally, the validator process installs a global panic hook that treats *every* panic, on *any* thread, as fatal and immediately calls `std::process::exit(1)`: [5](#0-4) 

This hook is installed for the validator binary at start-up: [6](#0-5) 

Putting these together: if any native builtin program contains a reachable panic (`unwrap()`, `expect()`, slice indexing, arithmetic overflow in a debug/overflow-checked build, etc.) on attacker-influenced but otherwise well-formed instruction data, an ordinary unprivileged transaction can trigger a Rust panic during `process_executable_chain`. That panic unwinds uncaught through `process_instruction` → `load_and_execute_transactions` → the banking/replay thread, and the process-wide panic hook forces `process::exit(1)`, killing the entire validator. This exactly mirrors the reported bug class: "callee panic is not turned into a graceful error by the calling framework, so it crashes a component that should have survived," except the blast radius in Agave is the whole validator process rather than a single RPC request.

### Impact Explanation
A crash-inducing panic reachable from unprivileged, syntactically valid transaction data against a native builtin program is a remote, non-RPC crash primitive: it can be sent by any user via normal transaction submission (no malicious peer/validator/admin assumption needed), and because of the global "exit on panic" policy, the effect is not merely "this instruction fails" but "this validator process terminates," which is a denial-of-service / consensus-availability impact consistent with a "non-RPC remote exhaustion/crash" class.

### Likelihood Explanation
The likelihood depends entirely on whether a concrete panicking code path exists today inside a native builtin's `declare_process_instruction!` body that is reachable with attacker-controlled instruction/account data (e.g., unchecked slice indexing, `unwrap()` on user-supplied conversions, or an arithmetic operation not wrapped in `saturating_`/`checked_` variants). I was not able to enumerate every native builtin's instruction-processing code within the available indexed content to point at one specific panicking line; the index only surfaced test/mock builtins (`MockBuiltin`, `MockBuiltinErr`, `TestBuiltinEntrypoint`) that intentionally use `expect()`/`unwrap()` for test convenience, not production builtins. Because of this, I cannot certify a currently-live panic path in a specific production builtin from the indexed code alone — the missing `catch_unwind` boundary and the exit-on-panic hook are confirmed, but the "attacker can trigger a live panic today" piece needs deeper file-by-file review of the System/Vote/Stake/Config/AddressLookupTable/ComputeBudget/loader processors that the index did not fully surface.

### Recommendation
- Wrap native/builtin program invocation in `process_executable_chain` (program-runtime/src/invoke_context.rs) with `std::panic::catch_unwind`, converting any caught panic into `InstructionError::ProgramFailedToComplete`, matching the behavior already implemented (for test purposes only) in `program-test/src/lib.rs::invoke_builtin_function`.
- Audit all native builtin program instruction handlers for `unwrap()`/`expect()`/direct slice indexing/unchecked arithmetic on attacker-controlled instruction or account data, replacing them with proper `InstructionError` returns.
- Reconsider the blanket `process::exit(1)` panic hook policy in `metrics/src/metrics.rs`, or at minimum ensure it can never be reached from a panic originating inside per-transaction instruction execution once builtins are properly isolated with `catch_unwind`.

### Proof of Concept
A concrete transaction-level PoC could not be constructed from the indexed code alone because no specific panicking production builtin instruction path was located in the available index (only test/mock builtins that deliberately `unwrap()`/`expect()` were found: `program-runtime/src/invoke_context.rs:1186-1283` `MockBuiltin`, and `ledger/src/blockstore_processor.rs:3553-3568` `MockBuiltinErr`). The structural PoC is:
1. Craft any transaction that invokes a native builtin program with instruction/account data that hits an `unwrap()`, out-of-bounds index, or unchecked arithmetic op inside that builtin's `declare_process_instruction!` body.
2. Submit it as an ordinary user (no elevated privileges needed).
3. `process_executable_chain` calls the builtin directly with no `catch_unwind` (`program-runtime/src/invoke_context.rs:690-702`); the panic unwinds up through the processing thread.
4. The global panic hook (`metrics/src/metrics.rs:499-530`), installed at validator startup, calls `std::process::exit(1)`, terminating the entire validator process instead of only failing the single transaction.

Given the uncertainty noted in the Likelihood section about whether a currently reachable panicking line exists in a production builtin, this should be treated as a structural/architectural finding requiring a source-level audit of builtin instruction processors to confirm or rule out a live trigger, rather than a fully proven exploit chain.

### Citations

**File:** program-runtime/src/invoke_context.rs (L66-99)
```rust
#[macro_export]
macro_rules! declare_process_instruction {
    ($process_instruction:ident, $cu_to_consume:expr, |$invoke_context:ident| $inner:tt) => {
        $crate::solana_sbpf::declare_builtin_function!(
            $process_instruction,
            fn rust(
                invoke_context: &mut $crate::invoke_context::InvokeContext<'_, '_>,
                _arg0: u64,
                _arg1: u64,
                _arg2: u64,
                _arg3: u64,
                _arg4: u64,
            ) -> Result<u64, Box<dyn std::error::Error>> {
                fn process_instruction_inner(
                    $invoke_context: &mut $crate::invoke_context::InvokeContext,
                ) -> std::result::Result<(), $crate::__private::InstructionError>
                    $inner

                let consumption_result = if $cu_to_consume > 0
                {
                    invoke_context.compute_meter.consume_checked($cu_to_consume)
                } else {
                    Ok(())
                };
                consumption_result
                    .and_then(|_| {
                        process_instruction_inner(invoke_context)
                            .map(|_| 0)
                            .map_err(|err| Box::new(err) as Box<dyn std::error::Error>)
                    })
                    .into()
            }
        );
    };
```

**File:** program-runtime/src/invoke_context.rs (L642-722)
```rust
        let builtin_id = {
            let owner_id = instruction_context.get_program_owner()?;
            if native_loader::check_id(&owner_id) {
                *instruction_context.get_program_key()?
            } else if bpf_loader_deprecated::check_id(&owner_id)
                || bpf_loader::check_id(&owner_id)
                || bpf_loader_upgradeable::check_id(&owner_id)
                || loader_v4::check_id(&owner_id)
            {
                owner_id
            } else {
                return Err(InstructionError::UnsupportedProgramId);
            }
        };

        // The Murmur3 hash value (used by RBPF) of the string "entrypoint"
        const ENTRYPOINT_KEY: u32 = 0x71E3CF81;
        let entry = self
            .program_cache_for_tx_batch
            .find(&builtin_id)
            .ok_or(InstructionError::UnsupportedProgramId)?;
        let function = match &entry.program {
            ProgramCacheEntryType::Builtin(program) => program
                .get_function_registry()
                .lookup_by_key(ENTRYPOINT_KEY)
                .map(|(_name, (function, _codegen))| function),
            _ => None,
        }
        .ok_or(InstructionError::UnsupportedProgramId)?;

        let program_id = *instruction_context.get_program_key()?;
        self.transaction_context
            .set_return_data(program_id, Vec::new())?;
        let logger = self.get_log_collector();
        stable_log::program_invoke(&logger, &program_id, self.get_stack_height());
        let pre_remaining_units = self.get_remaining();
        // For now, only built-ins are invoked from here, so the VM and its Config are irrelevant.
        self.memory_contexts
            .set_memory_context_abi_v1(MemoryContext::new(
                BpfAllocator::new(0),
                Vec::new(),
                // SAFETY:
                // This path invokes a builtin program, so this mapping is never used.
                unsafe {
                    MemoryMapping::new(Vec::new(), &Config::default(), SBPFVersion::Reserved)
                        .unwrap()
                },
            ))?;
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

**File:** metrics/src/metrics.rs (L499-530)
```rust
/// Hook the panic handler to generate a data point on each panic
pub fn set_panic_hook(program: &'static str, version: Option<String>) {
    static SET_HOOK: Once = Once::new();
    SET_HOOK.call_once(|| {
        let default_hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |ono| {
            default_hook(ono);
            let location = match ono.location() {
                Some(location) => location.to_string(),
                None => "?".to_string(),
            };
            submit(
                DataPoint::new("panic")
                    .add_field_str("program", program)
                    .add_field_str("thread", thread::current().name().unwrap_or("?"))
                    // The 'one' field exists to give Kapacitor Alerts a numerical value
                    // to filter on
                    .add_field_i64("one", 1)
                    .add_field_str("message", &ono.to_string())
                    .add_field_str("location", &location)
                    .add_field_str("version", version.as_ref().unwrap_or(&"".to_string()))
                    .to_owned(),
                Level::Error,
            );
            // Flush metrics immediately
            flush();

            // Exit cleanly so the process don't limp along in a half-dead state
            std::process::exit(1);
        }));
    });
}
```

**File:** validator/src/commands/run/execute.rs (L1-60)
```rust
use {
    crate::{
        admin_rpc_service::{self, StakedNodesOverrides, load_staked_nodes_overrides},
        bootstrap,
        cli::{self},
        commands::{FromClapArgMatches, run::args::RunArgs},
        ledger_lockfile, lock_ledger,
    },
    agave_snapshots::{
        ArchiveFormat, SnapshotInterval, SnapshotVersion,
        paths::BANK_SNAPSHOTS_DIR,
        snapshot_config::{SnapshotConfig, SnapshotUsage},
    },
    agave_votor::vote_history_storage,
    bytesize::ByteSize,
    clap::{ArgMatches, crate_name, value_t, value_t_or_exit, values_t, values_t_or_exit},
    crossbeam_channel::unbounded,
    log::*,
    rand::{rng, seq::SliceRandom},
    solana_accounts_db::{
        accounts_db::{AccountShrinkThreshold, AccountsDbConfig},
        accounts_file::AccountsFileProvider,
        accounts_index::{
            AccountSecondaryIndexes, AccountsIndexConfig, DEFAULT_NUM_ENTRIES_OVERHEAD,
            DEFAULT_NUM_ENTRIES_TO_EVICT, IndexLimit, IndexLimitThreshold, ScanFilter,
        },
        partitioned_rewards::PartitionedEpochRewardsConfig,
        utils::{
            create_all_accounts_run_and_snapshot_dirs, create_and_canonicalize_directories,
            create_and_canonicalize_directory,
        },
    },
    solana_clap_utils::input_parsers::{keypair_of, keypairs_of, pubkey_of, value_of, values_of},
    solana_clock::{DEFAULT_SLOTS_PER_EPOCH, Slot},
    solana_core::{
        banking_stage::transaction_scheduler::scheduler_controller::SchedulerConfig,
        consensus::tower_storage,
        repair::repair_handler::RepairHandlerType,
        resource_limits,
        snapshot_packager_service::SnapshotPackagerService,
        system_monitor_service::SystemMonitorService,
        tpu::MAX_VOTES_PER_SECOND,
        validator::{
            BlockProductionMethod, BlockVerificationMethod, SchedulerPacing, Validator,
            ValidatorConfig, ValidatorLogConfig, ValidatorStartProgress, ValidatorTpuConfig,
            is_snapshot_config_valid,
        },
    },
    solana_genesis_utils::MAX_GENESIS_ARCHIVE_UNPACKED_SIZE,
    solana_gossip::{
        cluster_info::{DEFAULT_CONTACT_SAVE_INTERVAL_MILLIS, NodeConfig},
        contact_info::ContactInfo,
        node::Node,
    },
    solana_hash::Hash,
    solana_keypair::Keypair,
    solana_ledger::{
        blockstore_options::BlockstoreCleanupStrategy,
        shred::filter::TurbineMode,
        use_snapshot_archives_at_startup::{self, UseSnapshotArchivesAtStartup},
```
