### Title
Per-account floor-division when metering CPI compute costs allows attackers to underpay for cross-program invocation data movement - ([File: program-runtime/src/cpi.rs])

### Summary
`translate_accounts_common` (and `translate_account_infos`) in `program-runtime/src/cpi.rs` meter the compute cost of moving account data during a CPI by computing `len / cpi_bytes_per_unit` **separately for each account** and summing the results via repeated `compute_meter.consume_checked()` calls, instead of summing the raw byte lengths first and dividing once. This is the same class of bug as Sherlock M-5 (`getCommunityVotingPower` in FrankenDAO's `Staking.sol`), where doing `(a/C) + (b/C) + (c/C)` instead of `(a+b+c)/C` loses precision through repeated integer-division truncation — except here the truncation works in the attacker's favor, systematically **under-charging** compute units for CPI account-data transfer.

### Finding Description
For every non-duplicate account in a CPI, the compute cost of accounting for that account's data is calculated independently: [1](#0-0) 
for executable accounts, and [2](#0-1) 
for regular (non-executable, address-restricted) accounts, both using `checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)`, with `cpi_bytes_per_unit` defaulting to 250 bytes/CU: [3](#0-2) 

Each of these divisions floors to zero whenever an individual account's length (or `ref_to_len_in_vm`) is less than `cpi_bytes_per_unit` (250 bytes), and the meter is charged the sum of these independently-floored values across the loop in `translate_accounts_common`: [4](#0-3) 

Mathematically, for a set of account lengths `l_1, l_2, ..., l_n`:
```
sum(l_i / C)  <=  sum(l_i) / C
```
with strict inequality whenever `l_i < C` for enough terms whose sum would otherwise exceed a multiple of `C`. This mirrors exactly the reported anti-pattern: dividing before summing accumulates truncation loss, whereas summing first and dividing once is the mathematically correct (and here, correctly-priced) approach — as demonstrated by the sibling function `translate_account_infos`, which *does* sum bytes first and then divides once: [5](#0-4) 

The account-loop cost accounting (`translate_accounts_common`) does not follow this same pattern and instead divides per-account.

### Impact Explanation
An unprivileged on-chain program invoking CPI with many small accounts (each with `data.len()` or `ref_to_len_in_vm` just under the 250-byte `cpi_bytes_per_unit` threshold) can move substantially more account data through the CPI account-translation path than the compute-unit charge reflects. For example, with 200 accounts of 249 bytes each (≈49,800 bytes total), the correctly-summed cost would be `49800 / 250 = 199` CUs, but the per-account floor approach charges `200 * (249 / 250) = 0` CUs — a complete bypass of this cost component. This is a materially underpriced compute vulnerability: it allows attackers to perform CPI-mediated account data movement/serialization work that is not accounted for by the compute budget, potentially enabling denial-of-service-style resource exhaustion or unfairly cheap transaction execution relative to the resources consumed, since compute unit accounting is meant to reflect (and bound) the real work performed by validators executing the transaction.

### Likelihood Explanation
This is trivially reachable by any unprivileged program author: it only requires constructing a CPI (`invoke`/`invoke_signed`) whose `account_infos` list contains many accounts each with data length just below the 250-byte `cpi_bytes_per_unit` threshold. No special privileges, validator/operator roles, or malicious snapshots are required — this is purely a guest-program-controlled compute metering discrepancy in the CPI plumbing (`program-runtime/src/cpi.rs`), squarely within the unprivileged CPI/program-runtime cost-accounting scope.

### Recommendation
Accumulate the total bytes accounted for by all accounts in the `translate_accounts_common` loop (both executable and address-restricted branches) and perform a single `checked_div(cpi_bytes_per_unit)` after the loop completes, mirroring the pattern already used in `translate_account_infos` (sum first, divide once), instead of calling `consume_checked` with a separately-floored value per account.

### Proof of Concept
Conceptual PoC (illustrating the arithmetic discrepancy; exact reproduction requires a Devin session with build/test access to the sbf CPI test programs, e.g. `programs/sbf/rust/invoke/src/lib.rs`):

1. Deploy a program that performs a single `invoke_signed` CPI call to a no-op program, passing `N = 200` `AccountInfo`s where the callee-side account (or `ref_to_len_in_vm`, when `syscall_parameter_address_restrictions` is active) reports a data length of 249 bytes for each account (just under `cpi_bytes_per_unit = 250`).
2. Measure remaining compute units before and after the CPI call (`sol_remaining_compute_units()`), similar to the existing CU-usage tests: [6](#0-5) 
3. Compare the CU consumed for the account-data-length component to `total_bytes / cpi_bytes_per_unit` (i.e., `200*249/250 = 199`). Because each account's cost is computed as `249/250 = 0` and summed, the observed CU charge for this component will be `0` instead of `199`, demonstrating the underpricing caused by dividing before summing.

I could not fully verify the exact maximum number of `AccountInfo`s permitted per CPI call (`MAX_ACCOUNTS_PER_INSTRUCTION`/account-info limits) within the tool budget, so the precise maximum magnitude of underpriced compute per single CPI call is not confirmed — a Devin session with full repo/build access would be needed to pin down `MAX_ACCOUNTS_PER_INSTRUCTION` and `check_account_infos`'s limit and thus the worst-case underpricing bound, and to run an actual on-chain test confirming the CU discrepancy.

### Citations

**File:** program-runtime/src/cpi.rs (L933-938)
```rust
    let account_infos_bytes = account_infos.len().saturating_mul(ACCOUNT_INFO_BYTE_SIZE);

    let amount = (account_infos_bytes as u64)
        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
        .unwrap_or(u64::MAX);
    invoke_context.compute_meter.consume_checked(amount)?;
```

**File:** program-runtime/src/cpi.rs (L996-1019)
```rust
    for (instruction_account_index, instruction_account) in
        next_instruction_accounts.iter().enumerate()
    {
        if next_instruction_context
            .is_instruction_account_duplicate(instruction_account_index as IndexOfAccount)?
            .is_some()
        {
            continue; // Skip duplicate account
        }

        let index_in_caller = instruction_context
            .get_index_of_account_in_instruction(instruction_account.index_in_transaction)?;
        let callee_account = instruction_context.try_borrow_instruction_account(index_in_caller)?;
        let account_key = invoke_context
            .transaction_context
            .get_key_of_account_at_index(instruction_account.index_in_transaction)?;

        #[expect(deprecated)]
        if callee_account.is_executable() {
            // Use the known account
            let amount = (callee_account.get_data().len() as u64)
                .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
                .unwrap_or(u64::MAX);
            invoke_context.compute_meter.consume_checked(amount)?;
```

**File:** program-runtime/src/cpi.rs (L1052-1058)
```rust
            if syscall_parameter_address_restrictions {
                // Moved from do_translate() via feature gate.
                let amount = (*caller_account.ref_to_len_in_vm)
                    .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
                    .unwrap_or(u64::MAX);
                invoke_context.compute_meter.consume_checked(amount)?;
            }
```

**File:** program-runtime/src/execution_budget.rs (L216-216)
```rust
            cpi_bytes_per_unit: 250, // ~50MB at 200,000 units
```

**File:** programs/sbf/rust/invoke/src/lib.rs (L1570-1577)
```rust
            let before_cpi = sol_remaining_compute_units();
            invoke_signed(&instruction, &account_infos, &[])?;
            let after_cpi = sol_remaining_compute_units();
            let cu_used = before_cpi - after_cpi;
            //need to use upper bound here, as different versions of sbpf add/remove speciliazed intructions hence leading to different CU usage
            if cu_used > 1756 {
                panic!("CU used more than baseline");
            }
```
