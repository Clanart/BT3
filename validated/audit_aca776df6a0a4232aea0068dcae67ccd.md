## Title
Permissionless `ExtendProgram` instruction lets any unprivileged account overwrite a target program's `ProgramData.slot`, griefing/DOS-ing the legitimate upgrade authority from upgrading in the same slot — ([File: programs/bpf_loader/src/lib.rs])

### Summary
The BPF Upgradeable Loader's legacy `ExtendProgram` instruction is dispatched with `check_authority = false`, meaning any unprivileged signer can extend (and thus rewrite the `slot` field of) *any* program's `ProgramData` account without being its upgrade authority. This is the same "anyone can act on a resource that isn't theirs to reset a timing gate" pattern as the reported `Cred.sol::buyShareCredFor()` issue: an attacker with no relationship to the program can perform a cheap, permissionless operation against a victim's account to reset a "last modified this slot" guard and block the victim's legitimate, authorized action.

### Finding Description
`UpgradeableLoaderInstruction::ExtendProgram` is handled by calling `common_extend_program(invoke_context, additional_bytes, false)`, explicitly passing `check_authority = false`: [1](#0-0) 

Inside `common_extend_program`, the authority check is entirely skipped when `check_authority` is `false`; only a payer account (any account, not the authority) is required to fund the rent difference: [2](#0-1) 

At the end of the routine, the `ProgramData` account's `slot` field — the same field checked to detect a "same-block" write — is unconditionally overwritten with the current `clock_slot`, regardless of who invoked the instruction: [3](#0-2) 

The `Upgrade` instruction (which *does* require the real upgrade authority to sign) checks this same `slot` field and rejects the transaction if it equals the current slot, with the message "Program was deployed in this block already": [4](#0-3) 

Similarly, a subsequent `ExtendProgram`/`ExtendProgramChecked` call in the same slot is rejected with "Program was extended in this block already": [5](#0-4) 

Because any unprivileged account can call `ExtendProgram` on someone else's program, an attacker can proactively "claim" the current slot for that `ProgramData` account before the legitimate authority's `Upgrade` transaction lands, causing the authority's transaction to fail that slot — directly mirroring the reported pattern where `buyShareCredFor()` let anyone reset another user's `lastTradeTimestamp` lock gate.

### Impact Explanation
An attacker who resubmits a 1-instruction, low-cost `ExtendProgram` transaction every slot (or repeatedly races the authority's transaction into the same slot) can indefinitely delay a legitimate program upgrade. As the original report's judge noted for the analogous case, this becomes especially severe if an upgrade is urgently needed to patch a live exploit/zero-day: even a short, sustained delay in deploying the fix has outsized impact. The attack is cheap: the attacker only pays the incremental rent-exemption lamports for extending the account (and, once `loader_v3_minimum_extend_program_size` is active, must extend by at least the minimum chunk size, bounding but not preventing the attack), and does not need to be, or coordinate with, the program's authority.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to race/land a transaction in the same slot as the authority's `Upgrade` call, or continuously spam `ExtendProgram` every slot against a specific target program to keep the account's `slot` pinned to "now." This is more feasible than the original Cred.sol timing scenario because it requires no insider knowledge of intent — an attacker can simply keep a target program permanently "extended this slot" as a standing DoS, needing only one transaction roughly every slot, well within the reachable-cost bar for a targeted griefing campaign against a specific upgradeable program (e.g. a widely used DeFi program mid-incident).

### Recommendation
Require the upgrade authority's signature for the legacy `ExtendProgram` path as well (i.e., always pass `check_authority = true`, or otherwise deprecate/gate `UpgradeableLoaderInstruction::ExtendProgram` in favor of the checked variant), so that only the legitimate authority (or programs it has authorized) can update `ProgramData.slot`. At minimum, do not let an unauthenticated extend touch the same `slot`/same-block guard that the authenticated `Upgrade` path relies on for its once-per-slot protection.

### Proof of Concept
1. Deploy an upgradeable program `P` with authority `A` and `ProgramData` account `PD`.
2. Attacker `X` (unrelated to `P`) submits `ExtendProgram { additional_bytes: N }` for `PD`, referencing `PD`, `P`, and paying rent from their own funds — no signature from `A` is required since `check_authority=false` for this instruction variant (`programs/bpf_loader/src/lib.rs:790-792`, `908-933`).
3. This call succeeds and sets `PD.slot = clock.slot` (`programs/bpf_loader/src/lib.rs:987-992`).
4. `A` submits `Upgrade` for `P` in the same slot; it fails with `InstructionError::InvalidArgument` ("Program was deployed in this block already") because `clock.slot == PD.slot` (`programs/bpf_loader/src/lib.rs:459-467`).
5. `X` repeats step 2 every slot (or races it against `A`'s retries), indefinitely blocking `A` from upgrading `P`.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L459-467)
```rust
            if let UpgradeableLoaderState::ProgramData {
                slot,
                upgrade_authority_address,
            } = programdata.get_state()?
            {
                if clock.slot == slot {
                    ic_logger_msg!(log_collector, "Program was deployed in this block already");
                    return Err(InstructionError::InvalidArgument);
                }
```

**File:** programs/bpf_loader/src/lib.rs (L790-792)
```rust
        UpgradeableLoaderInstruction::ExtendProgram { additional_bytes } => {
            common_extend_program(invoke_context, additional_bytes, false)?;
        }
```

**File:** programs/bpf_loader/src/lib.rs (L908-933)
```rust
    {
        if clock_slot == slot {
            ic_logger_msg!(log_collector, "Program was extended in this block already");
            return Err(InstructionError::InvalidArgument);
        }

        if upgrade_authority_address.is_none() {
            ic_logger_msg!(
                log_collector,
                "Cannot extend ProgramData accounts that are not upgradeable"
            );
            return Err(InstructionError::Immutable);
        }

        if check_authority {
            let authority_key =
                Some(*instruction_context.get_key_of_instruction_account(AUTHORITY_ACCOUNT_INDEX)?);
            if upgrade_authority_address != authority_key {
                ic_logger_msg!(log_collector, "Incorrect upgrade authority provided");
                return Err(InstructionError::IncorrectAuthority);
            }
            if !instruction_context.is_instruction_account_signer(AUTHORITY_ACCOUNT_INDEX)? {
                ic_logger_msg!(log_collector, "Upgrade authority did not sign");
                return Err(InstructionError::MissingRequiredSignature);
            }
        }
```

**File:** programs/bpf_loader/src/lib.rs (L987-992)
```rust
    let mut programdata_account =
        instruction_context.try_borrow_instruction_account(PROGRAM_DATA_ACCOUNT_INDEX)?;
    programdata_account.set_state(&UpgradeableLoaderState::ProgramData {
        slot: clock_slot,
        upgrade_authority_address,
    })?;
```
