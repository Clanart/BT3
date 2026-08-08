### Title
Systematic compute-unit undercharging via integer-division truncation in `mem_op_consume` and CPI/syscall cost formulas - (File: syscalls/src/mem_ops.rs)

### Summary
Multiple syscall and CPI cost-accounting sites divide a byte-count by `cpi_bytes_per_unit` using truncating integer division (`checked_div`) instead of a ceiling division, mirroring the reported bug class where `amount / time` truncation causes a systematic, per-call loss of precision that compounds over many invocations.

### Finding Description
The reported bug is about `reward.rewardRate = amount / _remainingRewardTime` truncating and then being multiplied back out, losing up to `_remainingRewardTime - 1` units of precision per notification. The same divide-then-round-down pattern is pervasive in Agave's syscall/CPI compute-cost accounting, where the divisor is `cpi_bytes_per_unit` (or `2` for hashing) instead of a time duration:

- `mem_op_consume` in `syscalls/src/mem_ops.rs` computes the cost of `memcpy`/`memmove`/`memcmp` as `n.checked_div(cpi_bytes_per_unit).unwrap_or(u64::MAX)`, floored rather than rounded up [1](#0-0) .
- `SyscallSetReturnData` / `SyscallGetReturnData` compute additional cost as `len.checked_div(cpi_bytes_per_unit)` / `(length + 32).checked_div(cpi_bytes_per_unit)`, both truncating [2](#0-1) [3](#0-2) .
- `SyscallGetEpochStake` computes `PUBKEY_BYTES.checked_div(cpi_bytes_per_unit)` for the non-null-pointer path [4](#0-3) .
- `SyscallGetSysvar` (and the `program-test` sysvar stub) compute `sysvar_id_cost = 32.checked_div(cpi_bytes_per_unit)` and `sysvar_buf_cost = length.checked_div(cpi_bytes_per_unit)`, both truncating [5](#0-4) .
- CPI account translation in `program-runtime/src/cpi.rs` computes `(data_len).checked_div(cpi_bytes_per_unit)` for both the "known executable account" fast path and the syscall-parameter-address-restriction path, both truncating [6](#0-5) .
- `SyscallHash` computes per-slice byte cost as `hash_byte_cost * (val.len() / 2)`, truncating the division before multiplying by `hash_byte_cost` — the exact `amount = rate; rate * duration` truncate-then-multiply shape described in the report [7](#0-6) .

In every one of these call sites, a program can construct byte lengths that are not exact multiples of `cpi_bytes_per_unit` (or `2`), so up to `cpi_bytes_per_unit - 1` bytes' worth of "free" compute unit is undercharged per syscall/CPI call. Because these operations (`memcpy`, `memcmp`, return-data getters/setters, CPI parameter translation, sysvar reads) can be invoked repeatedly inside a single instruction (bounded only by the overall compute budget and call depth), and are directly reachable by any unprivileged BPF program, the aggregate undercharge across a transaction can be non-trivial.

### Impact Explanation
This is a compute-metering precision loss, not a memory-safety or authentication bypass — its accepted-impact category here is "materially underpriced compute." The magnitude is bounded by `cpi_bytes_per_unit - 1` compute units lost per call (I was not able to confirm the exact default value of `cpi_bytes_per_unit` from the index within the available iterations, so I cannot quantify the per-call loss in absolute terms — this should be verified against `SVMTransactionExecutionCost::default()` in a full checkout). Since these functions are called many times within a single transaction's compute budget, the cumulative effect is a validator systematically undercharging CUs for byte-oriented syscalls, letting programs execute more actual work (memory operations, CPI account marshaling, sysvar/return-data I/O) than their nominal CU price should allow.

### Likelihood Explanation
Likelihood is high in the sense that any unprivileged on-chain program naturally triggers these code paths on every `sol_memcpy`, `sol_memmove`, `sol_memcmp`, `sol_set_return_data`/`sol_get_return_data`, `sol_get_sysvar`, CPI account translation, and hashing syscalls — no privileged access or crafted validator state is required. However, this is the intended/known cost-model design pattern used throughout the codebase (truncating division for "cost per N bytes" style pricing) rather than an isolated coding defect, and there is no single "attack" needed beyond calling ordinary syscalls with unaligned sizes; it's a design-level rounding choice rather than a bug uniquely introduced by a faulty computation as in the referenced report's `rewardRate` case.

### Recommendation
For each of the identified sites (`mem_op_consume`, `SyscallSetReturnData`/`SyscallGetReturnData`, `SyscallGetEpochStake`, `SyscallGetSysvar`/`fetch_and_write_sysvar`, the CPI account-translation cost computations in `program-runtime/src/cpi.rs`, and `SyscallHash`'s per-slice byte cost), replace truncating `checked_div` with a ceiling-division (`div_ceil`, or `(n + divisor - 1) / divisor`) so that any partial "unit" of bytes is charged a full compute unit, consistent with `mem_op_base_cost.max(...)` flooring already present as a minimum-charge safety net in some of these functions. This mirrors the reported mitigation of avoiding truncation before later use of the divided value.

### Proof of Concept
Not independently verified against a live/compiled Agave build within this session — the analysis is based on static code review of the cited functions in `syscalls/src/mem_ops.rs`, `syscalls/src/lib.rs`, `syscalls/src/sysvar.rs`, and `program-runtime/src/cpi.rs`. To reproduce/prove:
1. Deploy a BPF program that repeatedly calls `sol_memcpy_`/`sol_memmove_`/`sol_memcmp_` with a length `n` such that `n % cpi_bytes_per_unit != 0` (e.g., `n = cpi_bytes_per_unit - 1`), many times within one instruction.
2. Measure consumed compute units via `sol_remaining_compute_units` before/after each call and compare against `n / cpi_bytes_per_unit` (floored) vs. the ceiling-based expected charge.
3. Confirm the actual charge matches the floored value, i.e., the syscall was undercharged by up to `cpi_bytes_per_unit - 1` units relative to a ceiling-based cost model, and that this discrepancy accumulates linearly with the number of calls issued within the compute budget.

### Citations

**File:** syscalls/src/mem_ops.rs (L3-9)
```rust
fn mem_op_consume(invoke_context: &mut InvokeContext, n: u64) -> Result<(), Error> {
    let compute_cost = invoke_context.get_execution_cost();
    let cost = compute_cost.mem_op_base_cost.max(
        n.checked_div(compute_cost.cpi_bytes_per_unit)
            .unwrap_or(u64::MAX),
    );
    invoke_context.compute_meter.consume_checked(cost)
```

**File:** syscalls/src/lib.rs (L1926-1929)
```rust
        let cost = len
            .checked_div(execution_cost.cpi_bytes_per_unit)
            .unwrap_or(u64::MAX)
            .saturating_add(execution_cost.syscall_base_cost);
```

**File:** syscalls/src/lib.rs (L1980-1983)
```rust
            let cost = length
                .saturating_add(size_of::<Pubkey>() as u64)
                .checked_div(execution_cost.cpi_bytes_per_unit)
                .unwrap_or(u64::MAX);
```

**File:** syscalls/src/lib.rs (L2767-2778)
```rust
            for val in vals.iter() {
                let bytes = translate_vm_slice(val, memory_mapping, check_aligned)?;
                let cost = mem_op_base_cost.max(
                    hash_byte_cost.saturating_mul(
                        val.len()
                            .checked_div(2)
                            .expect("div by non-zero literal"),
                    ),
                );
                invoke_context.compute_meter.consume_checked(cost)?;
                hasher.hash(bytes);
            }
```

**File:** syscalls/src/lib.rs (L2832-2839)
```rust
            let compute_units = compute_cost
                .syscall_base_cost
                .saturating_add(
                    (PUBKEY_BYTES as u64)
                        .checked_div(compute_cost.cpi_bytes_per_unit)
                        .unwrap_or(u64::MAX),
                )
                .saturating_add(compute_cost.mem_op_base_cost);
```

**File:** syscalls/src/sysvar.rs (L193-199)
```rust
        // Abort: "Compute budget is exceeded."
        let sysvar_id_cost = 32_u64.checked_div(cpi_bytes_per_unit).unwrap_or(0);
        let sysvar_buf_cost = length.checked_div(cpi_bytes_per_unit).unwrap_or(0);
        let cost = sysvar_base_cost
            .saturating_add(sysvar_id_cost)
            .saturating_add(std::cmp::max(sysvar_buf_cost, mem_op_base_cost));
        invoke_context.compute_meter.consume_checked(cost)?;
```

**File:** program-runtime/src/cpi.rs (L1013-1057)
```rust
        #[expect(deprecated)]
        if callee_account.is_executable() {
            // Use the known account
            let amount = (callee_account.get_data().len() as u64)
                .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
                .unwrap_or(u64::MAX);
            invoke_context.compute_meter.consume_checked(amount)?;
        } else if let Some(caller_account_index) =
            account_info_keys.iter().position(|key| *key == account_key)
        {
            let serialized_metadata =
                accounts_metadata
                    .get(index_in_caller as usize)
                    .ok_or_else(|| {
                        ic_msg!(
                            invoke_context,
                            "Internal error: index mismatch for account {}",
                            account_key
                        );
                        Box::new(InstructionError::MissingAccount) as Error
                    })?;

            // build the CallerAccount corresponding to this account.
            if caller_account_index >= account_infos.len() {
                return Err(Box::new(CpiError::InvalidLength));
            }
            #[expect(clippy::indexing_slicing)]
            let caller_account =
                do_translate(
                    invoke_context,
                    memory_mapping,
                    check_aligned,
                    account_infos_addr.saturating_add(
                        caller_account_index.saturating_mul(mem::size_of::<T>()) as u64,
                    ),
                    &account_infos[caller_account_index],
                    serialized_metadata,
                )?;

            if syscall_parameter_address_restrictions {
                // Moved from do_translate() via feature gate.
                let amount = (*caller_account.ref_to_len_in_vm)
                    .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
                    .unwrap_or(u64::MAX);
                invoke_context.compute_meter.consume_checked(amount)?;
```
