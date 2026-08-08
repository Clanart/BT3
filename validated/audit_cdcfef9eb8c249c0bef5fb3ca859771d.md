### Title
Underpriced CPI compute-unit charging due to integer-division truncation in instruction/account-data cost calculation - (File: program-runtime/src/cpi.rs)

### Summary
Several CPI (Cross-Program Invocation) cost calculations in `program-runtime/src/cpi.rs` divide the number of bytes being copied/translated by `cpi_bytes_per_unit` (default `250`) using `checked_div(...).unwrap_or(u64::MAX)`, with **no minimum-charge floor**. Because integer division truncates, any CPI whose instruction data, account metas, or account data length is smaller than the divisor (250 bytes) is charged **zero** compute units for that component — the exact same rounding-to-zero pattern described in the external report for `Scaler.scale()`.

### Finding Description
`translate_instruction_rust`, `translate_instruction_c`, `translate_account_infos` (via `translate_account_infos`/`translate_accounts_common`), and the `syscall_parameter_address_restrictions` path all compute a compute-unit charge as `bytes / cpi_bytes_per_unit` and pass the raw truncated result straight into `compute_meter.consume_checked(...)`: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

The default divisor is `cpi_bytes_per_unit: 250`: [6](#0-5) 

Since these divisions have no `.max(mem_op_base_cost)` or similar floor (unlike the sibling implementation in `mem_ops.rs`, which explicitly guards against this), any CPI instruction whose `data.len()`, `account_metas.len() * size_of::<AccountMeta>()`, `account_infos_bytes`, executable account data length, or `ref_to_len_in_vm` value is `< 250` results in `0` compute units charged for that component — mirroring the reported bug class: "stakes/values smaller than the divisor round down to zero."

Contrast with `mem_ops.rs`, which floors the byte-cost with `mem_op_base_cost`: [7](#0-6) 

### Impact Explanation
A program can perform many CPI invocations each passing instruction data, account metas, or account data under 250 bytes (a very common case — most instructions are far smaller than 250 bytes) and pay zero compute units for the CPI data-translation cost component of each. While other flat costs (`invoke_units`, `syscall_base_cost`, per-account write-lock costs, etc.) still apply, the byte-proportional cost intended to price CPU/memory work for copying caller/callee data across the VM boundary is silently waived below the 250-byte threshold. This is a materially underpriced compute path: an attacker can construct transactions that perform substantially more CPI data-copy work than they are charged for, within the `cpi_bytes_per_unit` bound repeated across many CPIs in a single transaction (bounded by `max_instruction_trace_length`), undercharging the true cost of the work performed.

### Likelihood Explanation
This is trivially reachable by any unprivileged on-chain program via ordinary `invoke`/`invoke_signed` CPI calls with small instruction data/account counts — no special privileges, validator role, or malicious snapshot required. The condition (`bytes < 250`) is the common case for typical instructions (e.g., SPL Token transfers, small custom instructions), making the underpricing systematic rather than an edge case.

### Recommendation
Apply the same floor pattern used in `mem_ops.rs::mem_op_consume` to all CPI byte-cost calculations in `cpi.rs`: use `mem_op_base_cost.max(bytes / cpi_bytes_per_unit)` (or otherwise round up, e.g. `(bytes + cpi_bytes_per_unit - 1) / cpi_bytes_per_unit`) instead of a bare truncating division with `unwrap_or(u64::MAX)`, ensuring any non-zero byte length incurs a non-zero, appropriately proportional charge.

### Proof of Concept
1. Deploy a program `A` that repeatedly invokes program `B` via CPI (`invoke`/`invoke_signed`), passing instruction `data` of length `< 250` bytes and a small number of `AccountMeta`s (`accounts.len() * 34 < 250`).
2. Observe (via `SyscallGetRemainingCompute`/logs) that `translate_instruction_rust`/`translate_instruction_c` charge `data.len() / 250 = 0` and `account_metas.len() * 34 / 250 = 0` compute units for the translation cost — i.e., `total_cu_translation_cost == 0` for the CPI's byte-proportional component.
3. Repeat this CPI within the same transaction up to the `max_instruction_trace_length`/CU limit, and compare total charged CU for the byte components (0) against the actual bytes copied across the VM boundary, demonstrating the byte-proportional CPI cost is systematically avoided for realistic instruction sizes.

### Citations

**File:** program-runtime/src/cpi.rs (L560-575)
```rust
    let mut total_cu_translation_cost: u64 = (data.len() as u64)
        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
        .unwrap_or(u64::MAX);

    // Each account meta is 34 bytes (32 for pubkey, 1 for is_signer, 1 for is_writable)
    let account_meta_translation_cost =
        (account_metas.len().saturating_mul(size_of::<AccountMeta>()) as u64)
            .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
            .unwrap_or(u64::MAX);

    total_cu_translation_cost =
        total_cu_translation_cost.saturating_add(account_meta_translation_cost);

    invoke_context
        .compute_meter
        .consume_checked(total_cu_translation_cost)?;
```

**File:** program-runtime/src/cpi.rs (L695-711)
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

    invoke_context
        .compute_meter
        .consume_checked(total_cu_translation_cost)?;
```

**File:** program-runtime/src/cpi.rs (L933-938)
```rust
    let account_infos_bytes = account_infos.len().saturating_mul(ACCOUNT_INFO_BYTE_SIZE);

    let amount = (account_infos_bytes as u64)
        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
        .unwrap_or(u64::MAX);
    invoke_context.compute_meter.consume_checked(amount)?;
```

**File:** program-runtime/src/cpi.rs (L1013-1019)
```rust
        #[expect(deprecated)]
        if callee_account.is_executable() {
            // Use the known account
            let amount = (callee_account.get_data().len() as u64)
                .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
                .unwrap_or(u64::MAX);
            invoke_context.compute_meter.consume_checked(amount)?;
```

**File:** program-runtime/src/cpi.rs (L1052-1057)
```rust
            if syscall_parameter_address_restrictions {
                // Moved from do_translate() via feature gate.
                let amount = (*caller_account.ref_to_len_in_vm)
                    .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
                    .unwrap_or(u64::MAX);
                invoke_context.compute_meter.consume_checked(amount)?;
```

**File:** program-runtime/src/execution_budget.rs (L216-216)
```rust
            cpi_bytes_per_unit: 250, // ~50MB at 200,000 units
```

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
