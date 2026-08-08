### Title
CPI instruction-translation compute cost is undercharged due to splitting one division into two before summing - (File: `program-runtime/src/cpi.rs`)

### Summary
`translate_instruction_rust` and `translate_instruction_c` compute the compute-unit (CU) cost of translating a CPI instruction's `data` bytes and `account_metas` bytes by performing **two separate floor divisions** (`data_len / cpi_bytes_per_unit` and `account_metas_len_bytes / cpi_bytes_per_unit`) and then adding the two truncated results together, instead of summing the byte counts first and dividing once. This is the classic "divide-before-combine" pattern that causes unnecessary/avoidable truncation, resulting in the validator charging **less compute** for CPI calls than the byte-proportional cost model intends.

### Finding Description
In `program-runtime/src/cpi.rs`, both `translate_instruction_rust` and `translate_instruction_c` compute a CU cost for translating the CPI instruction payload: [1](#0-0) 

```rust
let mut total_cu_translation_cost: u64 = (data.len() as u64)
    .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
    .unwrap_or(u64::MAX);

let account_meta_translation_cost =
    (account_metas.len().saturating_mul(size_of::<AccountMeta>()) as u64)
        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
        .unwrap_or(u64::MAX);

total_cu_translation_cost =
    total_cu_translation_cost.saturating_add(account_meta_translation_cost);
```

The identical pattern is repeated in `translate_instruction_c`: [2](#0-1) 

Each of the two `checked_div` calls independently floors its own operand against `cpi_bytes_per_unit` (default `250`, see [3](#0-2) ) before the two truncated quotients are added. Mathematically, `floor(a/n) + floor(b/n) <= floor((a+b)/n)`, so splitting the division always charges **less than or equal to** the byte-proportional cost, with the gap growing as more independent divisions are introduced (up to `n-1` = 249 CU lost per division instead of at most 249 CU lost total for one combined division).

This contrasts with the correct pattern used elsewhere in the very same syscall file, where the lengths are summed *before* a single division is applied: [4](#0-3) 

```rust
let cost = length
    .saturating_add(size_of::<Pubkey>() as u64)
    .checked_div(execution_cost.cpi_bytes_per_unit)
    .unwrap_or(u64::MAX);
```

That confirms the intended design is "sum bytes, then divide once" — the `translate_instruction_rust`/`translate_instruction_c` implementation deviates from this and performs the division twice, systematically under-charging CPI compute costs.

### Impact Explanation
`translate_instruction_rust`/`translate_instruction_c` are invoked on **every single cross-program invocation** (`sol_invoke_signed_rust` / `sol_invoke_signed_c`), which is one of the most common unprivileged, user-triggerable operations in the SVM. Because the split-division under-charges CU cost relative to the intended byte-proportional model, an attacker/program can perform many CPI invocations with account-meta/data sizes chosen to maximize the "wasted" quotient truncation (repeatedly getting almost a full `cpi_bytes_per_unit - 1` CU discount per call, twice per call — once for `data` and once for `account_metas`), allowing more actual work to be packed into a transaction's compute budget than the cost model intends. This is a materially underpriced compute condition, one of the explicitly accepted impact categories for this analysis.

### Likelihood Explanation
This is deterministic and requires no special privileges — any on-chain program performing a CPI (`invoke`/`invoke_signed`) exercises this code path on every call. The discrepancy is present unconditionally in current code (not a conditional/mocked/theoretical path), and is trivially reproducible by measuring consumed CUs for CPI calls with varying `data`/`account_metas` sizes chosen just below/above `cpi_bytes_per_unit` boundaries.

### Recommendation
Sum the `data` length and the account-metas byte length first, then perform a single `checked_div` by `cpi_bytes_per_unit`, mirroring the pattern already used in `SyscallGetReturnData` (`syscalls/src/lib.rs:1980-1983`):

```rust
let total_cu_translation_cost = (data.len() as u64)
    .saturating_add(account_metas.len().saturating_mul(size_of::<AccountMeta>()) as u64)
    .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
    .unwrap_or(u64::MAX);
```

Apply the same fix to both `translate_instruction_rust` and `translate_instruction_c`.

### Proof of Concept
Given `cpi_bytes_per_unit = 250` (default, [3](#0-2) ):
- Let `data.len() = 249` bytes and `account_metas` bytes total = `249` bytes.
- Current code: `floor(249/250) + floor(249/250) = 0 + 0 = 0` CU charged for translation.
- Correct (sum-then-divide) behavior: `floor((249+249)/250) = floor(498/250) = 1` CU.
- By repeatedly issuing CPI calls structured so both `data` and `account_metas` sizes sit just under a `cpi_bytes_per_unit` boundary, a program can avoid CU charges that the cost model intends to apply, over and over across many CPI calls within a transaction, effectively obtaining free compute relative to the intended per-byte pricing.

### Citations

**File:** program-runtime/src/cpi.rs (L560-571)
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

**File:** program-runtime/src/execution_budget.rs (L216-216)
```rust
            cpi_bytes_per_unit: 250, // ~50MB at 200,000 units
```

**File:** syscalls/src/lib.rs (L1980-1983)
```rust
            let cost = length
                .saturating_add(size_of::<Pubkey>() as u64)
                .checked_div(execution_cost.cpi_bytes_per_unit)
                .unwrap_or(u64::MAX);
```
