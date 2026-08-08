### Title
CPI account-data compute-unit charging truncates (floors) size/cpi_bytes_per_unit, systematically underpricing repeated cross-program invocations - (File: `program-runtime/src/cpi.rs`)

### Summary
The Mento report describes `TradingLimits::update()` truncating `_deltaFlow / 10^decimals` toward zero and only special-casing the result-is-zero case, letting repeated small operations be charged (and thus limited) far below their real value. Agave's CPI compute-metering code has the same root-cause pattern: every place that prices cross-program invocation work by account-data size uses plain integer division (`checked_div`) by `cpi_bytes_per_unit` with no rounding-up and no non-zero floor, so any data length that is not an exact multiple of `cpi_bytes_per_unit` is under-charged, and lengths smaller than `cpi_bytes_per_unit` are charged **zero**.

### Finding Description
`SVMTransactionExecutionCost::cpi_bytes_per_unit` defaults to `250` [1](#0-0)  and is used throughout `program-runtime/src/cpi.rs` to bill the compute meter for copying/translating account data and metas during a CPI:

- Translating a caller `AccountInfo`'s data: `(data.len() as u64).checked_div(cpi_bytes_per_unit)` [2](#0-1) 
- Translating a `SolAccountInfo`'s `data_len`: `account_info.data_len.checked_div(cpi_bytes_per_unit)` [3](#0-2) 
- C-ABI instruction data + account-meta translation cost: `(data.len() as u64).checked_div(cpi_bytes_per_unit)` and `(accounts_len * size_of::<AccountMeta>()).checked_div(cpi_bytes_per_unit)` [4](#0-3) 
- Bulk `account_infos` array translation: `(account_infos_bytes as u64).checked_div(cpi_bytes_per_unit)` [5](#0-4) 
- Executable/known-account data size: `(callee_account.get_data().len() as u64).checked_div(cpi_bytes_per_unit)` [6](#0-5) 
- Reported account length under `syscall_parameter_address_restrictions`: `(*caller_account.ref_to_len_in_vm).checked_div(cpi_bytes_per_unit)` [7](#0-6) 

In every one of these call sites, `Rust` integer division rounds toward zero, exactly like the Solidity `_deltaFlow / 10**decimals` in the report. Unlike `TradingLimits::update()`, none of these compute-charging sites even apply the report's own weak mitigation (special-casing the zero case); an account/instruction-data length of `0..249` bytes is priced at exactly `0` compute units, and any length `L` is priced at `floor(L/250)` instead of `ceil(L/250)`, silently discounting up to `249` CU worth of real work on every single charge site, on every single CPI.

This is structurally identical to the reported bug class: a linear "value-to-unit" conversion that floors instead of ceilings, invoked repeatedly by an unprivileged caller (any BPF program performing CPIs), whose cumulative rounding error is bounded only by the number of times the attacker chooses to invoke it — which, for CPI, is bounded by `max_instruction_trace_length` and the transaction's total compute budget, not by the size of any single operation.

### Impact Explanation
This falls into the "materially underpriced compute" acceptance category from the validation rules. A malicious BPF program can perform many CPIs (or CPI-adjacent syscalls that reuse this same billing pattern, e.g. `sysvar` reads and `get_return_data` which follow the identical `checked_div` pattern) where every account's data length, `account_infos` array size, instruction data length, and account-meta array size is deliberately kept just under a multiple of `cpi_bytes_per_unit` (e.g., 249 bytes). Each such CPI performs real memory-translation/copy/validation work (bounded by `cpi_bytes_per_unit - 1` bytes) for zero or near-zero charged compute units. Aggregated across the transaction's full instruction trace (`MAX_INSTRUCTION_TRACE_LENGTH`), this lets a transaction perform substantially more real CPU work (memory copies, pointer translation, account validation) than its declared/charged compute-unit budget implies, which is the underpriced-compute analog of the report's "bypass the limit by double the amount or more."

### Likelihood Explanation
High. No special preconditions, privileges, or config are required — an ordinary BPF program invoking CPIs (`invoke`/`invoke_signed`) controls the exact byte lengths of account data, instruction data, and account-meta arrays passed to `translate_instruction_c`, `translate_accounts_rust/c`, and `translate_account_infos`, so triggering the worst-case rounding (`(cpi_bytes_per_unit - 1)` bytes "free" per charge site) on every CPI is trivial and fully reachable from unprivileged user code.

### Recommendation
Replace every `len.checked_div(cpi_bytes_per_unit)` compute-charging expression in `program-runtime/src/cpi.rs` (and the analogous pattern in `syscalls/src/sysvar.rs` and the `SyscallGetReturnData`/`mem_op_consume` helpers) with a ceiling division, e.g. `len.saturating_add(cpi_bytes_per_unit - 1) / cpi_bytes_per_unit`, or equivalently `(len - 1) / cpi_bytes_per_unit + 1` when `len > 0`, mirroring the Mento report's suggested fix, so that any non-zero byte count is charged at least one whole unit and partial units are always rounded up rather than down.

### Proof of Concept
Conceptually (matching the report's PoC style): for `cpi_bytes_per_unit = 250`, a program calls `invoke_signed` repeatedly, each time passing an account whose data length is `249` bytes (or any account-data/array length ≡ 249 mod 250). Each such CPI is charged `249 / 250 = 0` additional compute units for that byte-translation cost at each of the six division sites cited above, while the runtime still performs the real translation/copy work for the full 249 bytes each time — see the truncating divisions at [8](#0-7)  and [9](#0-8) . Repeating this across the transaction's allowed instruction trace length accumulates a large amount of uncharged real work relative to the metered compute-unit total.

### Citations

**File:** program-runtime/src/execution_budget.rs (L216-216)
```rust
            cpi_bytes_per_unit: 250, // ~50MB at 200,000 units
```

**File:** program-runtime/src/cpi.rs (L357-362)
```rust
                // Moved to translate_accounts_common() via feature gate.
                invoke_context.compute_meter.consume_checked(
                    (data.len() as u64)
                        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
                        .unwrap_or(u64::MAX),
                )?;
```

**File:** program-runtime/src/cpi.rs (L470-477)
```rust
        if !syscall_parameter_address_restrictions {
            // Moved to translate_accounts_common() via feature gate.
            invoke_context.compute_meter.consume_checked(
                account_info
                    .data_len
                    .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
                    .unwrap_or(u64::MAX),
            )?;
```

**File:** program-runtime/src/cpi.rs (L695-707)
```rust
    let mut total_cu_translation_cost: u64 = (data.len() as u64)
        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
        .unwrap_or(u64::MAX);

    // Each account meta is 34 bytes (32 for pubkey, 1 for is_signer, 1 for is_writable)
    let account_meta_translation_cost = (ix_c
        .accounts_len
        .saturating_mul(size_of::<AccountMeta>() as u64))
    .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
    .unwrap_or(u64::MAX);

    total_cu_translation_cost =
        total_cu_translation_cost.saturating_add(account_meta_translation_cost);
```

**File:** program-runtime/src/cpi.rs (L933-938)
```rust
    let account_infos_bytes = account_infos.len().saturating_mul(ACCOUNT_INFO_BYTE_SIZE);

    let amount = (account_infos_bytes as u64)
        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
        .unwrap_or(u64::MAX);
    invoke_context.compute_meter.consume_checked(amount)?;
```

**File:** program-runtime/src/cpi.rs (L1016-1019)
```rust
            let amount = (callee_account.get_data().len() as u64)
                .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
                .unwrap_or(u64::MAX);
            invoke_context.compute_meter.consume_checked(amount)?;
```

**File:** program-runtime/src/cpi.rs (L1054-1057)
```rust
                let amount = (*caller_account.ref_to_len_in_vm)
                    .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
                    .unwrap_or(u64::MAX);
                invoke_context.compute_meter.consume_checked(amount)?;
```
