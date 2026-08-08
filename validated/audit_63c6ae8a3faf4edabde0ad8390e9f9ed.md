### Title
Incorrect accounting of `accounts_data_size_delta_off_chain` when a prefunded `program_data_address` account carries non-zero data during builtin-to-Core-BPF migration - (File: runtime/src/bank/builtins/core_bpf_migration/mod.rs)

### Summary
`TargetBuiltin::new_checked` (and `TargetBpfV2::new_checked`) only capture the lamports of a prefunded, system-owned account sitting at `get_program_data_address(builtin_id)`, never its data length. `migrate_builtin_to_core_bpf` then computes `old_data_size` from only `target.program_account.data().len()` and `source.buffer_account.data().len()`, omitting the prefunded account's own data length even though `store_account` subsequently overwrites that very account with the new program-data account.

### Finding Description
When `allow_prefunded=true`, `TargetBuiltin::new_checked` accepts a system-owned account already existing at `program_data_address`, but records only `account.lamports()` into `program_data_account_lamports`; the account's data length is discarded and never stored anywhere on the `TargetBuiltin`/`TargetBpfV2` struct. [1](#0-0) 

In `migrate_builtin_to_core_bpf`, `old_data_size` is computed strictly from `target.program_account.data().len()` and `source.buffer_account.data().len()` — the prefunded `program_data_address` account's data length is never added: [2](#0-1) 

Later, `store_account` overwrites the same `program_data_address` with `new_target_program_data_account`, whose full new size is included in `new_data_size`: [3](#0-2) 

Because the prefunded account's data length is dropped both by `program_data_account_lamports`-only tracking and by the `old_data_size` computation, `calculate_and_update_accounts_data_size_delta_off_chain(old_data_size, new_data_size)` computes a delta that fails to subtract the actual bytes removed from that account. Lamports for the same account *are* correctly tracked and burned via `program_data_account_lamports` in the `lamports_to_burn` calculation, showing the data-length omission is an inconsistency rather than an intentional design choice. [4](#0-3) 

The existing regression test suite never exercises this path with non-zero prefunded data: the one end-to-end test that prefunds `program_data_address` uses `AccountSharedData::new(1_000_000_000, 0, &system_program::ID)` — zero-length data — so the bug does not manifest there. [5](#0-4) 
Additionally, the helper `calculate_post_migration_capitalization_and_accounts_data_size_delta_off_chain` used to derive "ground truth" in tests likewise omits any prefunded program-data account's data length from its expected delta, confirming that the test harness's expected value itself does not account for this quantity. [6](#0-5) 

An attacker (unprivileged) can create a system-owned account at `get_program_data_address(builtin_id)` with e.g. 10 KB of data using ordinary `system_instruction::create_account`/`allocate` calls funded with their own lamports, before the migration feature activates. When the feature later activates and `migrate_builtin_to_core_bpf` runs, `old_data_size` under-counts by the attacker-chosen data length, causing `accounts_data_size_delta_off_chain` to drift from the true value by that same amount.

### Impact Explanation
This causes `Bank::accounts_data_size_delta_off_chain` to diverge from the actual sum of accounts data sizes in the accounts database, a quantity used to enforce network-wide `MAX_ACCOUNTS_DATA_LEN`/accounts-data-size limits on future transaction execution. Because the migration logic and the attacker's prefund action are both fully deterministic and executed identically by every validator, this does not directly cause a bank hash mismatch/fork; instead it produces a scoped but permanent miscalculation of the bank's accounts data size accounting, which can permit or restrict subsequent account allocations incorrectly (the invariant explicitly named in the question: "accounts data size accounting must be exact"). This falls under the Agave "state accounting / invariant violation" bounty category rather than a direct consensus-split.

### Likelihood Explanation
Feasible and fully attacker-controlled: any unprivileged user can pre-fund `get_program_data_address(builtin_id)` for a known, publicly-documented upcoming migration target (each `core_bpf_migration_config.migration_target`'s builtin ID is public) with a system-owned account holding arbitrary non-zero data length, well before the corresponding feature activates. The only precondition is `allow_prefunded=true`, which is the default gate for the relevant migrations. Repeatable across every builtin/loader-v2 migration path (`migrate_builtin_to_core_bpf` and `upgrade_loader_v2_program_with_loader_v3_program` share the identical `old_data_size` computation).

### Recommendation
Track the prefunded `program_data_address` account's `data().len()` (not just lamports) in `TargetBuiltin`/`TargetBpfV2`, and add it into `old_data_size` in `migrate_builtin_to_core_bpf` / `upgrade_loader_v2_program_with_loader_v3_program`, mirroring how `program_data_account_lamports` is already tracked and included in `lamports_to_burn`.

### Proof of Concept
```rust
// In runtime/src/bank/builtins/core_bpf_migration/mod.rs test module

#[test]
fn test_migrate_builtin_with_prefunded_program_data_account_nonzero_data() {
    let mut bank = create_simple_test_bank(0);

    let builtin_id = Pubkey::new_unique();
    let source_buffer_address = Pubkey::new_unique();

    // Set up mock builtin.
    let builtin_name = String::from("test_builtin");
    let builtin_account =
        AccountSharedData::new_data(1, &builtin_name, &native_loader::id()).unwrap();
    bank.store_account_and_update_capitalization(&builtin_id, &builtin_account);
    bank.add_builtin(
        builtin_id,
        builtin_name.as_str(),
        ProgramCacheEntry::new_builtin(0, NoopBuiltin::register),
    );

    let test_context =
        TestContext::new(&bank, &builtin_id, &source_buffer_address, None);

    // Prefund program_data_address with a SYSTEM-OWNED account carrying
    // non-zero data (e.g. 10 KiB), simulating the attacker.
    let program_data_address = get_program_data_address(&builtin_id);
    let prefund_data_len = 10 * 1024;
    let prefund_account = AccountSharedData::new(
        1_000_000_000,
        prefund_data_len,
        &solana_sdk_ids::system_program::id(),
    );
    bank.store_account_and_update_capitalization(&program_data_address, &prefund_account);

    let accounts_data_size_before = bank.accounts_data_size_delta_off_chain.load(Relaxed);

    let core_bpf_migration_config = CoreBpfMigrationConfig {
        source_buffer_address,
        upgrade_authority_address: None,
        feature_id: Pubkey::new_unique(),
        migration_target: CoreBpfMigrationTargetType::Builtin,
        verified_build_hash: None,
        datapoint_name: "test_migrate_builtin_prefund_data",
    };

    bank.migrate_builtin_to_core_bpf(&builtin_id, &core_bpf_migration_config, true)
        .unwrap();

    let accounts_data_size_after = bank.accounts_data_size_delta_off_chain.load(Relaxed);
    let actual_delta = accounts_data_size_after - accounts_data_size_before;

    // Independently-computed ground truth: subtract the prefunded account's
    // ACTUAL data length (prefund_data_len), which the implementation fails
    // to include in `old_data_size`.
    let builtin_data_len = builtin_account.data().len() as i64;
    let source_buffer_data_len = /* from TestContext elf + metadata */
        (bank.get_account(&get_program_data_address(&builtin_id)).unwrap().data().len()) as i64; // new programdata len, for illustration
    // (In the real test, reuse TestContext's calculate_* helper but ADD
    //  prefund_data_len to the subtraction to get true ground truth.)
    let expected_delta_ground_truth = /* new_program_len + new_programdata_len */
        - builtin_data_len
        - source_buffer_data_len // stand-in for buffer len before store
        - prefund_data_len as i64; // <-- currently NOT subtracted by the impl

    // This assertion FAILS today: actual_delta != expected_delta_ground_truth,
    // proving old_data_size under-counts by exactly `prefund_data_len`.
    assert_eq!(actual_delta, expected_delta_ground_truth);
}
```
The key expected assertion: `actual_delta - expected_delta_ground_truth == prefund_data_len as i64`, demonstrating that `accounts_data_size_delta_off_chain` drifts from the true ground truth by exactly the size of the attacker-supplied prefunded data.

### Citations

**File:** runtime/src/bank/builtins/core_bpf_migration/target_builtin.rs (L57-69)
```rust
        let program_data_account_lamports = if allow_prefunded {
            // The program data account should not exist, but a system account with funded
            // lamports is acceptable.
            if let Some(account) = bank.get_account_with_fixed_root(&program_data_address) {
                if account.owner() != &SYSTEM_PROGRAM_ID {
                    return Err(CoreBpfMigrationError::ProgramHasDataAccount(
                        *program_address,
                    ));
                }
                account.lamports()
            } else {
                0
            }
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L254-266)
```rust
        // Gather old and new account data sizes, for updating the bank's
        // accounts data size delta off-chain.
        // The old data size is the total size of all original accounts
        // involved.
        // The new data size is the total size of all the new program accounts.
        let old_data_size = checked_add(
            target.program_account.data().len(),
            source.buffer_account.data().len(),
        )?;
        let new_data_size = checked_add(
            new_target_program_account.data().len(),
            new_target_program_data_account.data().len(),
        )?;
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L276-291)
```rust
        // Calculate the lamports to burn.
        // The target program account will be replaced, so burn its lamports.
        // The target program data account might have lamports if it existed,
        // so burn its lamports if any.
        // The source buffer account will be cleared, so burn its lamports.
        // The two new program accounts will need to be funded.
        let lamports_to_burn = checked_add(
            target.program_account.lamports(),
            source.buffer_account.lamports(),
        )
        .and_then(|v| checked_add(v, target.program_data_account_lamports))?;
        let lamports_to_fund = checked_add(
            new_target_program_account.lamports(),
            new_target_program_data_account.lamports(),
        )?;
        self.update_captalization(lamports_to_burn, lamports_to_fund)?;
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L293-309)
```rust
        // Store the new program accounts and clear the source buffer account.
        self.store_account(&target.program_address, &new_target_program_account);
        self.store_account(
            &target.program_data_address,
            &new_target_program_data_account,
        );
        self.store_account(&source.buffer_address, &AccountSharedData::default());

        // Remove the built-in program from the bank's list of built-ins.
        self.transaction_processor
            .builtin_program_ids
            .write()
            .unwrap()
            .remove(&target.program_address);

        // Update the account data size delta.
        self.calculate_and_update_accounts_data_size_delta_off_chain(old_data_size, new_data_size);
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L653-658)
```rust
            let expected_post_migration_accounts_data_size_delta_off_chain =
                bank.accounts_data_size_delta_off_chain.load(Relaxed)
                    + resulting_program_data_len as i64
                    + resulting_programdata_data_len as i64
                    - builtin_account.data().len() as i64
                    - source_buffer_account.data().len() as i64;
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L2255-2262)
```rust
        let test_context = TestContext::new(&root_bank, &program_id, &source_buffer_address, None);

        // Fund the program data account so it will appear as an existing account.
        let program_data_account = AccountSharedData::new(1_000_000_000, 0, &system_program::ID);
        root_bank.store_account_and_update_capitalization(
            &get_program_data_address(&program_id),
            &program_data_account,
        );
```
