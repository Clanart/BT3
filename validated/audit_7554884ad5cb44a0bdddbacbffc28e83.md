### Title
Permissionless `ExtendProgram` instruction lets any user front-run and block legitimate program `Upgrade` transactions - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The BPF Upgradeable Loader's `ExtendProgram` instruction is dispatched through `common_extend_program(invoke_context, additional_bytes, false)` with `check_authority = false`, meaning **any unprivileged account** can extend a program's `ProgramData` account size without providing the upgrade authority's signature. This call unconditionally rewrites the `ProgramData` account's `slot` field to the current `clock.slot`. Since `Upgrade` refuses to proceed when `clock.slot == slot` ("Program was deployed in this block already"), any attacker can grief a program's upgrade authority by submitting a near-zero-cost `ExtendProgram` transaction that lands in the same slot as (or ahead of) the authority's legitimate `Upgrade` transaction, causing the `Upgrade` to fail with `InstructionError::InvalidArgument`.

### Finding Description
`common_extend_program` is shared by both the permissioned `ExtendProgramChecked` path (`check_authority = true`) and the permissionless `ExtendProgram` path (`check_authority = false`): [1](#0-0) 

Because `check_authority` is `false` for `ExtendProgram`, the `AUTHORITY_ACCOUNT_INDEX` is never read or signature-checked, and the caller only needs to supply the `ProgramData` and `Program` accounts (both merely `is_writable`, not signer) plus an optional payer: [2](#0-1) 

After validating account relationships, the function unconditionally sets the `ProgramData` account state with `slot: clock_slot`: [3](#0-2) 

Separately, the `Upgrade` instruction handler treats `slot == clock.slot` as a hard failure to prevent double-deployment in one block: [4](#0-3) 

Because `ExtendProgram` writes `slot: clock_slot` on every successful call — including calls made by attackers with no relationship to the program — a single attacker-submitted `ExtendProgram { additional_bytes: 1 }` instruction targeting the victim program's `ProgramData` account in the same slot will cause a legitimate, correctly-signed `Upgrade` transaction landing in that same slot to be rejected with `InstructionError::InvalidArgument` ("Program was deployed in this block already"). If the `ProgramData` account's lamport balance already exceeds the rent-exempt minimum for one extra byte (true for essentially all real programs), `required_payment` is `0`, so the griefing call costs the attacker only the base transaction fee and requires no payer account at all.

This directly mirrors the referenced St1inch bug class: a state field (`slot`, analogous to `_unlockTime`) intended to be mutated only in conjunction with a privileged action (`Upgrade`, analogous to `withdraw`) can instead be mutated by an unprivileged, unrelated caller via a permissionless side-entrypoint (`ExtendProgram`, analogous to `depositFor` with amount 0), producing a state collision that blocks the legitimate privileged operation.

### Impact Explanation
An attacker can indefinitely delay or block upgrades to any upgradeable program on the network — including emergency/security patches — by resubmitting a cheap `ExtendProgram` instruction every slot targeting the victim's `ProgramData` account. This is a persistent, low-cost griefing/DoS primitive against program upgrade authorities, which is especially dangerous when time-sensitive security fixes need to be deployed.

### Likelihood Explanation
High. The attack requires no special privileges, no signature from the program's upgrade authority, and (in the common case) no funds beyond the transaction fee. The attacker only needs to know the target program's `ProgramData` address (derivable from the program ID) and race it into the same slot as the authority's `Upgrade` transaction, which is straightforward given public mempool/leader-schedule visibility.

### Recommendation
Require that `ExtendProgram`/`common_extend_program` either (a) also require the upgrade authority signature when the caller is not the payer/authority, or (b) avoid overwriting the `ProgramData.slot` field (the "deployed in this block" guard) on a simple size-extension that performs no code deployment/redeploy. The `slot` update in `common_extend_program` should be decoupled from the deployment-collision check used by `Upgrade`, or `ExtendProgram` should track its own "extended in this block" state separately from `Upgrade`'s "deployed in this block" state so the two do not collide.

### Proof of Concept
1. Victim owns an upgradeable program `P` with `ProgramData` account `PD` and upgrade authority `A`.
2. `A` submits a valid `Upgrade` transaction for `P` at slot `S`.
3. Attacker, with no relationship to `P`, submits `ExtendProgram { additional_bytes: 1 }` targeting `PD` (and `P`) at the same slot `S`, requiring only that `PD`'s current balance already covers the (negligible) rent for one extra byte.
4. If the attacker's transaction lands first (trivial to arrange via fee bumping or resubmission every slot), `common_extend_program` sets `PD`'s state to `ProgramData { slot: S, upgrade_authority_address: A }` at `programs/bpf_loader/src/lib.rs:989-992`.
5. `A`'s `Upgrade` transaction, processed in the same slot `S`, hits `if clock.slot == slot` at `programs/bpf_loader/src/lib.rs:464-467` and fails with `InstructionError::InvalidArgument` ("Program was deployed in this block already"), even though no real deployment occurred.
6. Repeating step 3 every slot indefinitely blocks all upgrades to `P`.

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

**File:** programs/bpf_loader/src/lib.rs (L790-817)
```rust
        UpgradeableLoaderInstruction::ExtendProgram { additional_bytes } => {
            common_extend_program(invoke_context, additional_bytes, false)?;
        }
    }

    Ok(())
}

fn common_extend_program(
    invoke_context: &mut InvokeContext,
    additional_bytes: u32,
    check_authority: bool,
) -> Result<(), InstructionError> {
    let log_collector = invoke_context.get_log_collector();
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;
    let program_id = instruction_context.get_program_key()?;

    const PROGRAM_DATA_ACCOUNT_INDEX: IndexOfAccount = 0;
    const PROGRAM_ACCOUNT_INDEX: IndexOfAccount = 1;
    const AUTHORITY_ACCOUNT_INDEX: IndexOfAccount = 2;
    // The unused `system_program_account_index` is 3 if `check_authority` and 2 otherwise.
    let optional_payer_account_index = if check_authority { 4 } else { 3 };

    if additional_bytes == 0 {
        ic_logger_msg!(log_collector, "Additional bytes must be greater than 0");
        return Err(InstructionError::InvalidInstructionData);
    }
```

**File:** programs/bpf_loader/src/lib.rs (L900-939)
```rust
        .sysvar_cache()
        .get_clock()
        .map(|clock| clock.slot)?;

    let upgrade_authority_address = if let UpgradeableLoaderState::ProgramData {
        slot,
        upgrade_authority_address,
    } = programdata_account.get_state()?
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

        upgrade_authority_address
    } else {
        ic_logger_msg!(log_collector, "ProgramData state is invalid");
        return Err(InstructionError::InvalidAccountData);
    };
```

**File:** programs/bpf_loader/src/lib.rs (L987-993)
```rust
    let mut programdata_account =
        instruction_context.try_borrow_instruction_account(PROGRAM_DATA_ACCOUNT_INDEX)?;
    programdata_account.set_state(&UpgradeableLoaderState::ProgramData {
        slot: clock_slot,
        upgrade_authority_address,
    })?;

```
