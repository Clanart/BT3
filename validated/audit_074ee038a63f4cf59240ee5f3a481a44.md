### Title
CPI instruction-translation cost rounds down to zero for small instructions, allowing free (unpriced) compute work - ([File: program-runtime/src/cpi.rs])

### Summary
`translate_instruction_rust()` and `translate_instruction_c()` compute the CPI translation compute cost using integer division by `cpi_bytes_per_unit` with no minimum/base-cost floor, unlike other syscall cost helpers (e.g. `mem_op_consume()` in `syscalls/src/mem_ops.rs`) which apply `.max(base_cost)`. When the instruction data plus account-meta bytes are smaller than `cpi_bytes_per_unit` (250 by default), the computed cost rounds down to `0`, so the CPU work of translating and validating the instruction (memory-mapping lookups, `AccountMeta` boolean sanitization, pubkey translations) is performed for zero charged compute units. This is the same root-cause pattern as the referenced LoopFi finding: `accrued.divDown(totalShares)` (here `bytes.checked_div(cpi_bytes_per_unit)`) rounds to zero when the numerator is smaller than the divisor, silently dropping the intended charge instead of accruing/advancing proportionally.

### Finding Description
In `program-runtime/src/cpi.rs`, `translate_instruction_rust()`: [1](#0-0) 
computes:
```
let mut total_cu_translation_cost: u64 = (data.len() as u64)
    .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
    .unwrap_or(u64::MAX);
let account_meta_translation_cost = (account_metas.len()*size_of::<AccountMeta>())
    .checked_div(cpi_bytes_per_unit).unwrap_or(u64::MAX);
total_cu_translation_cost = total_cu_translation_cost.saturating_add(account_meta_translation_cost);
invoke_context.compute_meter.consume_checked(total_cu_translation_cost)?;
```
The identical pattern is duplicated in `translate_instruction_c()`: [2](#0-1) 

`cpi_bytes_per_unit` defaults to `250` (`~50MB at 200,000 units`): [3](#0-2) 

Each `AccountMeta` costs 34 bytes in this calculation. Therefore, whenever
`data.len() + accounts_len * 34 < 250`
(e.g., any CPI instruction with fewer than 7 accounts and no more than ~200 bytes of instruction data — which covers the overwhelming majority of real-world CPI calls such as SPL Token transfers, System Program transfers, etc.), integer division truncates the cost to `0`. No compute units are ever charged for this translation step, because — unlike `mem_op_consume()` in `syscalls/src/mem_ops.rs`, which floors the cost with `compute_cost.mem_op_base_cost.max(...)` — `translate_instruction_rust`/`translate_instruction_c` have no such floor: [4](#0-3) 

The translation performs real, unbounded-relative-to-charge work: `translate_type`, `translate_slice`, per-account-meta unsafe boolean sanitization, and pubkey copies, none of which are priced when the size threshold isn't reached.

### Impact Explanation
This directly matches the "materially underpriced compute" category. A program can perform an unlimited number of CPI invocations, each with small instruction data/account counts, and pay zero compute units for the translation/validation work associated with each call (aside from the separate flat `invoke_units` charge applied elsewhere for the invocation itself, which does not depend on data size). Because the vast majority of production CPI calls fall under the 250-byte threshold, this represents a systemic, silently-lost charge rather than an edge case — closely mirroring the reported bug where `accrued.divDown(totalShares)` rounds to zero for realistic inputs, not just extreme ones. Over many nested/recursive small CPIs within the instruction-trace-length and stack-depth limits, an attacker can extract additional uncharged CPU work from validators for the price of a single flat invocation cost.

### Likelihood Explanation
High. This does not require adversarial or unusual input — it happens for essentially all normal-sized CPI instructions issued by any unprivileged user/program, every time `translate_instruction_rust`/`translate_instruction_c` is invoked (i.e., every `invoke`/`invoke_signed` call from any on-chain program). No feature flag, special configuration, or malicious crafting is required; it is the default, always-on cost accounting path for CPI.

### Recommendation
Apply the same fix pattern used elsewhere for other syscalls (e.g. `mem_op_consume`): compute the cost with a floor, e.g. `base_cost.max(bytes.checked_div(cpi_bytes_per_unit).unwrap_or(u64::MAX))`, or use `div_ceil` instead of a plain `checked_div` combined with a nonzero base cost, so the rounding cannot silently degenerate to zero for realistic small inputs. This mirrors the recommended LoopFi fix of tying the "advance" (charged units) directly and proportionally to the actual amount of work performed, eliminating the systematic downward-rounding loss.

### Proof of Concept
1. Deploy/execute any program that performs a CPI (`invoke`/`invoke_signed`) with an instruction whose `data.len()` plus `accounts_len * 34` is less than 250 bytes (e.g., a typical SPL Token transfer instruction: 1-byte discriminant + 8-byte amount = 9 bytes of data, 3 accounts × 34 = 102 bytes; total 111 < 250).
2. Trace through `translate_instruction_rust` (or `translate_instruction_c` for C-ABI callers) in `program-runtime/src/cpi.rs`: `total_cu_translation_cost = (9).checked_div(250) + (102).checked_div(250) = 0 + 0 = 0`.
3. `invoke_context.compute_meter.consume_checked(0)` charges nothing for the translation, even though `translate_type`, `translate_slice`, and per-account-meta validation work is fully performed.
4. Repeat the CPI (bounded by `max_instruction_trace_length`/stack depth) to accumulate uncharged translation work across a transaction, confirming the systemic zero-cost rounding for realistic CPI payload sizes.

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

**File:** program-runtime/src/execution_budget.rs (L216-216)
```rust
            cpi_bytes_per_unit: 250, // ~50MB at 200,000 units
```

**File:** syscalls/src/mem_ops.rs (L3-10)
```rust
fn mem_op_consume(invoke_context: &mut InvokeContext, n: u64) -> Result<(), Error> {
    let compute_cost = invoke_context.get_execution_cost();
    let cost = compute_cost.mem_op_base_cost.max(
        n.checked_div(compute_cost.cpi_bytes_per_unit)
            .unwrap_or(u64::MAX),
    );
    invoke_context.compute_meter.consume_checked(cost)
}
```
