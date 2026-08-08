Confirmed: `UpgradeableLoaderInstruction::ExtendProgram` is dispatched with `check_authority = false` at [1](#0-0) , so `common_extend_program` never validates the upgrade authority for the plain `ExtendProgram` instruction — only the payer must sign, and the account-parsing helper confirms the instruction only requires `programDataAccount`, `programAccount`, optional `systemProgram`, and optional `payerAccount` [2](#0-1) . This makes `ExtendProgram` a fully permissionless, anyone-can-pay operation on any upgradeable program's `ProgramData` account. Combined with the strict once-per-slot guard at [3](#0-2) , this gives an unprivileged-user analog to the reported bug class: a minuscule, cheap operation (extend-by-N-bytes) that an attacker can front-run to force a legitimate, larger `ExtendProgram` call to revert deterministically.

### Title
Permissionless `ExtendProgram` combined with the once-per-slot guard allows any attacker to indefinitely DoS program size extensions (and downstream deploys/upgrades) - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
`ExtendProgram` requires no upgrade-authority signature, only a payer signature to cover any incremental rent. Any account can invoke it against any upgradeable program's `ProgramData` account. The handler also enforces that a given `ProgramData` account can only be extended once per slot, recording the current `clock.slot` in `UpgradeableLoaderState::ProgramData` and rejecting later `ExtendProgram` calls in the same slot with `InstructionError::InvalidArgument`.

### Finding Description
In `common_extend_program`, the authority check is entirely gated behind `check_authority`, which is `false` for the plain `ExtendProgram` instruction path: [1](#0-0) 
The function's account layout only requires a writable `ProgramData` account, a writable `Program` account, and (optionally) a payer to fund the rent difference: [4](#0-3) 
No signer/authority check occurs for the un-checked variant — `check_authority` only gates the authority verification block later: [5](#0-4) 
This is by design: `ExtendProgram` is intentionally callable by any payer so third parties can help pay rent to grow a program before deployment. However, it interacts badly with this same-slot restriction: [3](#0-2) 
Because the restriction is keyed only on the `ProgramData` account and the current slot — not on payer identity or requested size — an attacker can submit their own trivial `ExtendProgram { additional_bytes: 1 }` transaction (paying only for 1 byte of rent) targeting a victim's `ProgramData` account. If the attacker's transaction lands first within a slot, the account's stored `slot` is updated to the current slot, and any subsequent legitimate `ExtendProgram` call (e.g., a deployer trying to grow the account enough to fit a new/larger program body before an `Upgrade`) fails with `InstructionError::InvalidArgument` ("Program was extended in this block already").

### Impact Explanation
This is a low-cost, permissionless griefing vector against the BPF Loader Upgradeable's program-size-extension path. Any program owner attempting to extend a `ProgramData` account (a prerequisite step before deploying/upgrading to a larger program, both in the CLI helper `extend_program_data_if_needed` and directly on-chain) can be blocked every slot by an attacker repeatedly front-running with a 1-byte extend costing only marginal rent-for-1-byte lamports plus a transaction fee. Since the attacker can repeat this every slot indefinitely, this can indefinitely delay or fully block a legitimate program's size extension and therefore its deployment/upgrade pipeline — directly analogous to the reported class where a minuscule operation forces a legitimate, larger operation to revert.

### Likelihood Explanation
The attack requires no privileged role, no authority key, and only knowledge of a target `program_id`/`programdata_address` (both are public on-chain data) and enough lamports to cover rent for extending by 1 byte plus a transaction fee. It does require the attacker's transaction to be included in the same slot before the victim's, which is a standard MEV/ordering assumption (e.g., higher priority fee) similar to what the original report's judge noted ("attack vector includes MEV as a necessary condition").

### Recommendation
Consider one or more of:
- Track the once-per-slot restriction against `(program_id, upgrade_authority)`-authorized calls only, or otherwise decouple the anti-reentrancy protection from unauthenticated third-party extends.
- Require the upgrade authority to co-sign (or at least be present) even for the "pay for rent only" flow, so an unrelated party cannot trigger state changes that block the authority's own subsequent instruction in the same slot.
- Allow a same-slot `ExtendProgram` to *succeed* by accumulating `additional_bytes` within the same slot (matching the deploy-cache-consistency the slot-check is trying to protect, at least for cases with the same target size) rather than hard-failing all subsequent attempts.

### Proof of Concept
1. Alice controls program `P` with upgrade authority `A`. `P`'s `ProgramData` account is under-sized for the next deployment; Alice will run `ExtendProgram(programdata, program, payer=Alice, additional_bytes=N)`.
2. Attacker (no relationship to `P`) submits `ExtendProgram(programdata, program, payer=attacker, additional_bytes=1)` in the same slot as Alice's transaction, paying only for the incremental rent of 1 byte.
3. If the attacker's transaction lands first (e.g., via a slightly higher priority fee), `common_extend_program` succeeds for the attacker and sets `UpgradeableLoaderState::ProgramData.slot = clock_slot` per [3](#0-2) .
4. Alice's transaction, processed later in the same slot, hits the `clock_slot == slot` branch and returns `InstructionError::InvalidArgument` ("Program was extended in this block already"), even though Alice signed as upgrade authority and requested a legitimate extension.
5. Attacker repeats step 2 every subsequent slot Alice retries, indefinitely blocking Alice's ability to extend `P`'s `ProgramData` account.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L790-792)
```rust
        UpgradeableLoaderInstruction::ExtendProgram { additional_bytes } => {
            common_extend_program(invoke_context, additional_bytes, false)?;
        }
```

**File:** programs/bpf_loader/src/lib.rs (L808-817)
```rust
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

**File:** programs/bpf_loader/src/lib.rs (L898-912)
```rust
    let clock_slot = invoke_context
        .environment_config
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
```

**File:** programs/bpf_loader/src/lib.rs (L920-933)
```rust
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

**File:** transaction-status/src/parse_bpf_loader.rs (L169-189)
```rust
        UpgradeableLoaderInstruction::ExtendProgram { additional_bytes } => {
            check_num_bpf_upgradeable_loader_accounts(&instruction.accounts, 2)?;
            Ok(ParsedInstructionEnum {
                instruction_type: "extendProgram".to_string(),
                info: json!({
                    "additionalBytes": additional_bytes,
                    "programDataAccount": account_keys[instruction.accounts[0] as usize].to_string(),
                    "programAccount": account_keys[instruction.accounts[1] as usize].to_string(),
                    "systemProgram": if instruction.accounts.len() > 2 {
                        Some(account_keys[instruction.accounts[2] as usize].to_string())
                    } else {
                        None
                    },
                    "payerAccount": if instruction.accounts.len() > 3 {
                        Some(account_keys[instruction.accounts[3] as usize].to_string())
                    } else {
                        None
                    },
                }),
            })
        }
```
