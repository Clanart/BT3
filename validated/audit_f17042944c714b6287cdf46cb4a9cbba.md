[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** program-test/src/lib.rs (L339-340)
```rust
        let sysvar_id_cost = 32_u64.checked_div(cpi_bytes_per_unit).unwrap_or(0);
        let sysvar_buf_cost = length.checked_div(cpi_bytes_per_unit).unwrap_or(0);
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

**File:** program-runtime/src/execution_budget.rs (L216-216)
```rust
            cpi_bytes_per_unit: 250, // ~50MB at 200,000 units
```

**File:** program-runtime/src/execution_budget.rs (L296-301)
```rust
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SVMTransactionExecutionAndFeeBudgetLimits {
    pub budget: SVMTransactionExecutionBudget,
    pub loaded_accounts_data_size_limit: u32,
    pub fee_details: FeeDetails,
}
```
