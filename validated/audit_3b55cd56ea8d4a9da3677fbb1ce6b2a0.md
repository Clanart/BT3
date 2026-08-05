## Analog Found: `TargetBuiltin::new_checked` / `TargetBpfV2::new_checked` can be DoSed via a griefing lamport transfer to the derived program-data address, blocking Core BPF migration

### Title
Core BPF migration of a native builtin (or Loader v2 program) can be permanently blocked by an unprivileged attacker pre-funding the deterministic program-data PDA - (File: `runtime/src/bank/builtins/core_bpf_migration/target_builtin.rs`)

### Summary
The reported bug pattern is a strict "balance must be exactly zero/absent" invariant used as a precondition gate, which any unprivileged party can violate by sending a trivial amount of value to the guarded account, causing a denial of service on a privileged operation. The Agave analog is the `program_data_address` existence/ownership check performed before migrating a native builtin program (or a Loader-v2 BPF program) to Core BPF.

### Finding Description
`TargetBuiltin::new_checked` derives a program-data PDA via `get_program_data_address(program_address)` and requires that this account either not exist, or (when `allow_prefunded` is true) exist only as a plain System-owned account with lamports but no data: [1](#0-0) 

If the account exists and is *not* owned by the System program, or exists at all when `allow_prefunded` is `false`, the function returns `CoreBpfMigrationError::ProgramHasDataAccount`, aborting the migration: [2](#0-1) 

The same pattern exists for Loader v2 → v3 upgrades in `TargetBpfV2::new_checked`: [3](#0-2) 

`get_program_data_address` computes a deterministic PDA from the program's pubkey using a fixed, publicly-known seed derivation, so the address is fully predictable ahead of any feature-gated migration announcement. Any unprivileged account can send a System `Transfer` instruction to that address (a `Transfer`'s destination account never needs to sign and, if it doesn't exist, `system_processor` will create it as a System-owned account holding the lamports). This is the exact "1 lamport to a not-yet-existing PDA" analog of the reported "1 wei transfer" griefing primitive from the external report — the corrupted invariant is `program_data_account == None` (or "System-owned with zero extra state"), and it is broken purely by unprivileged fund transfer, not by any malicious/privileged assumption.

`migrate_builtin_to_core_bpf` (and `upgrade_loader_v2_program_with_loader_v3_program`) is invoked from bank feature-activation processing (`runtime/src/bank.rs`, wired through `builtins/src/lib.rs`'s `CoreBpfMigrationConfig`) once the corresponding feature is activated on-chain: [4](#0-3) 

If `TargetBuiltin::new_checked` fails, `migrate_builtin_to_core_bpf` returns `Err(CoreBpfMigrationError::ProgramHasDataAccount(..))` and the migration does not proceed for that slot/feature-activation callsite.

### Impact Explanation
This breaks a governance-controlled runtime upgrade path: once a Core BPF migration feature is scheduled/activated, an unprivileged actor who front-runs the activation slot by sending a small System transfer to the deterministic `program_data_address` can make `new_checked` return `ProgramHasDataAccount` and prevent the migration from completing at that feature-activation callsite, exactly mirroring the reported "strict zero-balance/zero-existence check DoSed by a trivial transfer" bug class. Depending on how the specific migration callsite handles this `Result` (this codebase indexes only expose the `Result`-returning API; whether call sites `unwrap()`/`expect()` this at feature-activation time versus log-and-skip could not be fully confirmed from the indexed code), the practical impact ranges from "migration silently fails to happen" (stuck on old, potentially deprecated code path) up to a bank panic/consensus halt if any call site unwraps this error during `apply_feature_activations`. I was unable to locate the exact call site in `runtime/src/bank.rs` wiring within the indexed portion of the codebase to confirm which error-handling behavior is used there, so the severity ceiling (silent stall vs. validator panic) is uncertain and should be verified directly against `runtime/src/bank.rs`.

### Likelihood Explanation
Likelihood is high for the "blocks migration" case: the PDA address is deterministic and public before any migration is announced, requires no special access, and costs only the price of one `Transfer` instruction (a fraction of a lamport-rent-exempt minimum) to pre-fund. No malicious validator, leaked key, or trusted-integration assumption is required — a single ordinary transaction from any funded keypair suffices.

### Recommendation
Do not treat "account exists with any non-zero lamports" as a hard migration-blocking condition. Instead, when `allow_prefunded` semantics are desired everywhere, allow any pre-existing System-owned account (regardless of who funded it) to be absorbed into the new program-data account's rent-exempt balance, as already done in the `allow_prefunded = true` branch, and drop or soften the strict "must not exist" branch used when `allow_prefunded = false`. If strict rejection is intentionally used for particular migrations, gate it behind checking that the account has no non-default state (already partially done via the owner check) rather than mere existence/lamports, and ensure any error returned from `new_checked` at feature-activation call sites is handled without panicking the bank.

### Proof of Concept
1. Observe a scheduled Core BPF migration for builtin `builtin_id` with `allow_prefunded = false` (or `true` combined with pre-owning by a non-System program), by locally deriving `program_data_address = get_program_data_address(&builtin_id)` (public computation).
2. Before the migration feature activates on-chain, submit an ordinary `system_instruction::transfer` (or `create_account` owned by a non-system program) to `program_data_address` from any funded account. No signature from `program_data_address` is required for a `Transfer` destination.
3. When the feature activates and `Bank::migrate_builtin_to_core_bpf` calls `TargetBuiltin::new_checked`, the check at `runtime/src/bank/builtins/core_bpf_migration/target_builtin.rs:57-82` sees the pre-existing account and returns `Err(CoreBpfMigrationError::ProgramHasDataAccount(builtin_id))`, exactly as exercised by the existing unit test `test_target_program_builtin` at lines 188-244 of the same file, which demonstrates the failure path when the data account is pre-funded/owned incorrectly. [5](#0-4)

### Citations

**File:** runtime/src/bank/builtins/core_bpf_migration/target_builtin.rs (L55-82)
```rust
        let program_data_address = get_program_data_address(program_address);

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
        } else {
            // The program data account should not exist and have zero lamports.
            if bank
                .get_account_with_fixed_root(&program_data_address)
                .is_some()
            {
                return Err(CoreBpfMigrationError::ProgramHasDataAccount(
                    *program_address,
                ));
            }

            0
        };
```

**File:** runtime/src/bank/builtins/core_bpf_migration/target_builtin.rs (L188-244)
```rust
        // Fail if the program data account exists
        store_account(
            &bank,
            &program_address,
            &program_account.data(),
            program_account.executable(),
            program_account.owner(),
        );
        store_account(
            &bank,
            &program_data_address,
            &UpgradeableLoaderState::ProgramData {
                slot: 0,
                upgrade_authority_address: Some(Pubkey::new_unique()),
            },
            false,
            &BPF_LOADER_UPGRADEABLE_ID,
        );
        assert_matches!(
            TargetBuiltin::new_checked(&bank, &program_address, &migration_target, allow_prefund)
                .unwrap_err(),
            CoreBpfMigrationError::ProgramHasDataAccount(..)
        );

        // Allow some lamports in the program data account owned by the system program
        store_account(
            &bank,
            &program_data_address,
            &vec![0u8; 100],
            false,
            &SYSTEM_PROGRAM_ID,
        );

        if allow_prefund {
            // Succeed if prefund is allowed
            assert!(
                TargetBuiltin::new_checked(
                    &bank,
                    &program_address,
                    &migration_target,
                    allow_prefund,
                )
                .is_ok()
            );
        } else {
            // Fail if prefund is not allowed
            assert_matches!(
                TargetBuiltin::new_checked(
                    &bank,
                    &program_address,
                    &migration_target,
                    allow_prefund
                )
                .unwrap_err(),
                CoreBpfMigrationError::ProgramHasDataAccount(..)
            );
        }
```

**File:** runtime/src/bank/builtins/core_bpf_migration/target_bpf_v2.rs (L49-74)
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
        } else {
            // The program data account should not exist and have zero lamports.
            if bank
                .get_account_with_fixed_root(&program_data_address)
                .is_some()
            {
                return Err(CoreBpfMigrationError::ProgramHasDataAccount(
                    *program_address,
                ));
            }

            0
        };
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L224-237)
```rust
    pub(crate) fn migrate_builtin_to_core_bpf(
        &mut self,
        builtin_program_id: &Pubkey,
        config: &CoreBpfMigrationConfig,
        allow_prefunded: bool,
    ) -> Result<(), CoreBpfMigrationError> {
        datapoint_info!(config.datapoint_name, ("slot", self.slot, i64));

        let target = TargetBuiltin::new_checked(
            self,
            builtin_program_id,
            &config.migration_target,
            allow_prefunded,
        )?;
```
