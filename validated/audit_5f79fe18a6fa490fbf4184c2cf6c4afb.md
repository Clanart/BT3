## Title
Underpriced compute in CPI account-data translation due to per-account integer-division truncation of `cpi_bytes_per_unit` charges - (File: program-runtime/src/cpi.rs)

### Summary
The externally reported bug is a class of "repeated integer-division truncation" issue: a total quantity is divided by a count in a loop/across multiple units, and the floor-division remainder is silently discarded on every iteration, allowing the cumulative loss to become material. The closest unprivileged-user-reachable analog in agave is the CPI (cross-program invocation) account/data-translation cost accounting in `program-runtime/src/cpi.rs`, where the compute cost charged per account is computed with `checked_div(cpi_bytes_per_unit)` independently for each account in a loop, rather than accounting for the aggregate byte total once.

### Finding Description
In `translate_accounts_common`, for every account passed via CPI, the compute cost of translating/copying that account's data is computed independently and floor-divided: [1](#0-0) 

Similarly, in `translate_instruction_rust`/`translate_instruction_c`, the byte cost is computed via two independent `checked_div` calls (once for instruction data, once for account metas), and in `translate_account_infos` a single floor-division is applied to the aggregate `account_infos_bytes`: [2](#0-1) [3](#0-2) 

And per-account data-length charging is also performed independently per account for the "known executable account" case and the "caller account" case: [4](#0-3) 

Because each of these `checked_div(cpi_bytes_per_unit)` calls floors independently, a caller can split what would otherwise be one large chargeable quantity into many smaller quantities (e.g., many accounts, each with a data length smaller than `cpi_bytes_per_unit`), so that each individual division floors to `0`. Real work is still performed for each account (translation, pointer validation, bounds checks, memory copies for `update_callee_account`), but the compute charge for that portion of the CPI can be reduced to `0` for every account whose length falls under the `cpi_bytes_per_unit` threshold. This mirrors the reported "distributing a total across N buckets and losing remainder every time" bug class, except here the attacker (a normal BPF program, not a validator/operator) controls the "bucketing" (number of accounts / instruction layout) specifically to maximize the amount of uncharged compute.

### Impact Explanation
`cpi_bytes_per_unit` gates how much CPI byte-processing work is priced per compute unit. Per-item floor division means an attacker-controlled program can invoke CPI with many small accounts (up to `MAX_CPI_ACCOUNT_INFOS`/instruction size limits) where each account's data length is under `cpi_bytes_per_unit`, and get that portion of CPI translation/copy work for free rather than paying the intended proportional cost. This falls under "materially underpriced compute," since a program can perform CPU work (translation, address validation, `update_callee_account` copies) across many accounts within a single instruction/transaction without being charged for it, unlike a single large buffer of equivalent total size which would be charged correctly by the aggregate divisions used elsewhere (e.g. `translate_account_infos`'s single aggregate division at line 935-937).

### Likelihood Explanation
This is reachable purely from an unprivileged, deployed BPF program performing a normal CPI call (`sol_invoke_signed`) with a crafted account list — no special privileges, validator role, or malicious snapshot needed. The per-account cost computation is on the hot CPI path (`program-runtime/src/cpi.rs`, `translate_accounts_common`) exercised by every CPI, so the code path is trivial to trigger. However, the per-account magnitude of underpricing is bounded (`cpi_bytes_per_unit - 1` bytes per account, i.e., typically under 1 CU per account before rounding), and the number of accounts per instruction/transaction is capped by existing limits (`MAX_CPI_ACCOUNT_INFOS`, instruction/account count caps and total transaction compute budget), which bounds — but does not eliminate — the achievable aggregate saving.

### Recommendation
Aggregate all per-account byte-costs that are divided by `cpi_bytes_per_unit` within a single CPI call before applying `checked_div`, mirroring the approach already used in `translate_account_infos` for `account_infos_bytes` (line 933-937), instead of dividing per-account and discarding the per-account remainder. Alternatively, carry forward the truncated remainder across accounts within the same CPI call and add it into the next account's cost calculation so that the sum of the individually rounded costs converges to `ceil(total_bytes / cpi_bytes_per_unit)`.

### Proof of Concept
Conceptual PoC (cannot execute inside this environment, but derivable directly from the code above):
1. Deploy a BPF program that performs a single CPI (`sol_invoke_signed`) passing `N` account infos, each with `data.len() < cpi_bytes_per_unit` (e.g., 1 byte each), to some low-CU-cost target program (e.g., no-op).
2. For each account, `translate_accounts_common` computes `amount = data_len.checked_div(cpi_bytes_per_unit)`, which floors to `0` for every account since `data_len < cpi_bytes_per_unit`. [4](#0-3) 
3. Repeating this CPI across many small accounts allows the program to perform `N` accounts' worth of translation/copy work while paying `0` CU for the per-account "bytes-per-unit" charge component, compared to submitting the same total bytes in one contiguous buffer, which would be charged via a single floor/ceil division on the full size.
4. Comparing CU consumption reported by `sol_remaining_compute_units()` before/after such a call (as already exercised in the existing SBF test harness) against the CU consumption of an equivalent single large-buffer transfer would demonstrate the discrepancy. [5](#0-4)

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

**File:** programs/sbf/c/src/invoke/invoke.c (L900-931)
```c
  case TEST_CU_USAGE_MINIMUM:
  {
    sol_log("Test minimum cost of a CPI invocation with 1 account meta and 1 account info");

    uint64_t accounts_len = 1;
    SolAccountMeta arguments[] = {
      {accounts[NOOP_PROGRAM_INDEX].key, false, false},
    };

    uint64_t account_infos_len = 1;
    SolAccountInfo *account_infos = sol_calloc(account_infos_len, sizeof(SolAccountInfo));
    sol_assert(0 != account_infos);
    account_infos[0] = accounts[NOOP_PROGRAM_INDEX];

    uint8_t data[] = {};
    const SolInstruction instruction = {
      accounts[NOOP_PROGRAM_INDEX].key,
      arguments, accounts_len,
      data, SOL_ARRAY_SIZE(data)
    };

    const SolSignerSeeds signers_seeds[] = {};
    uint64_t remaining = sol_remaining_compute_units();
    sol_assert(SUCCESS == sol_invoke_signed(
                          &instruction,
                          account_infos, account_infos_len,
                          signers_seeds, SOL_ARRAY_SIZE(signers_seeds)));

    uint64_t used = remaining - sol_remaining_compute_units();

    sol_assert(used == 1061);
    break;
```
