## Title
DoS of Initial Program Deployment via Pre-Funding the Derived `ProgramData` PDA - (File: `programs/bpf_loader/src/lib.rs`)

## Summary
`UpgradeableLoaderInstruction::DeployWithMaxDataLen` creates the `ProgramData` account for a new program by invoking `system_instruction::create_account` against a PDA address derived from the program's public key. Because `create_account` fails whenever the target address already holds any lamports, an unprivileged attacker can front-run a program's initial deployment by sending a trivial amount of lamports to the deterministically-derivable `programdata_key`, permanently blocking that specific program address from ever being deployed. This mirrors the report's `bonding_curve_token_account` issue: an account address that is derivable in advance (`init`-style, not `init_if_needed`) can be pre-created/pre-funded by an attacker to grief the legitimate creator.

## Finding Description
In `process_loader_upgradeable_instruction`, the `DeployWithMaxDataLen` handler derives the `ProgramData` PDA and creates it via a native CPI to the System Program's plain `CreateAccount` instruction: [1](#0-0) 

The System Program's `create_account` implementation explicitly rejects the operation if the destination account already has any lamports: [2](#0-1) 

Since `programdata_key` is computed deterministically via `Pubkey::find_program_address(&[new_program_id.as_ref()], program_id)` (a PDA of the BPF Loader Upgradeable program), any observer who learns the intended `new_program_id` (visible in the mempool alongside the `CreateAccount` instruction for the program account, which is bundled in the same deployment transaction produced by `deploy_with_max_program_len`) can independently compute `programdata_key` off-chain and send it a single lamport via an ordinary `Transfer`. Once that transfer lands, the legitimate `DeployWithMaxDataLen` instruction will always fail with `SystemError::AccountAlreadyInUse`, and this failure is deterministic and permanent for that specific program address — the deployer must abandon that program pubkey and repeat the entire deployment with a new keypair.

The maintainers of Agave have already recognized this general class of PDA pre-funding griefing and introduced `SystemInstruction::CreateAccountAllowPrefund` (SIMD-0312) specifically to let account creators tolerate a pre-funded destination: [3](#0-2) [4](#0-3) 

However, `programs/bpf_loader/src/lib.rs` still constructs a plain `system_instruction::create_account` for the ProgramData account rather than the prefund-tolerant variant, so the BPF Loader Upgradeable program remains exposed to this DoS.

## Impact Explanation
This is a Medium-impact denial-of-service: it does not compromise funds or execute arbitrary code, but it lets any unprivileged network participant permanently block a specific, known program address from ever completing its initial deployment, forcing the legitimate deployer to burn the intended program identity and redo the deployment under a new keypair (loss of a chosen/vanity program address, wasted transaction fees, and deployment downtime).

## Likelihood Explanation
Likelihood is Medium: the attacker needs no special privilege, only the ability to observe the pending deployment transaction (which reveals `new_program_id`) and race a cheap `Transfer` of 1 lamport to the derived `programdata_key` ahead of it landing — a standard front-running pattern on Solana that unprivileged actors can perform without validator or operator privileges.

## Recommendation
Migrate the `DeployWithMaxDataLen` (and analogous `Deploy`/upgrade paths that create PDA-owned accounts) in `programs/bpf_loader/src/lib.rs` to use `SystemInstruction::CreateAccountAllowPrefund` instead of plain `CreateAccount` once/where the feature is active, so that a pre-funded `programdata_key` no longer blocks legitimate account initialization, consistent with the intent of SIMD-0312.

## Proof of Concept
1. Observer sees a pending transaction bundling `system_instruction::create_account` (program account) + `UpgradeableLoaderInstruction::DeployWithMaxDataLen` for a target `program_keypair.pubkey()`.
2. Attacker computes `programdata_key = Pubkey::find_program_address(&[program_pubkey.as_ref()], &bpf_loader_upgradeable::id())` and submits a `Transfer` of 1 lamport to `programdata_key` with higher priority so it lands first.
3. When the legitimate deployment transaction executes, the CPI at [5](#0-4)  triggers `create_account`, which now sees `to.get_lamports() > 0` and returns `SystemError::AccountAlreadyInUse` per [6](#0-5) , causing the entire deployment to fail irrecoverably for that program address.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L279-310)
```rust
            // Create ProgramData account
            let (derived_address, bump_seed) =
                Pubkey::find_program_address(&[new_program_id.as_ref()], program_id);
            if derived_address != programdata_key {
                ic_logger_msg!(log_collector, "ProgramData address is not derived");
                return Err(InstructionError::InvalidArgument);
            }

            // Drain the Buffer account to payer before paying for programdata account
            {
                let mut buffer = instruction_context.try_borrow_instruction_account(3)?;
                let mut payer = instruction_context.try_borrow_instruction_account(0)?;
                payer.checked_add_lamports(buffer.get_lamports())?;
                buffer.set_lamports(0)?;
            }

            let owner_id = *program_id;
            let mut instruction = system_instruction::create_account(
                &payer_key,
                &programdata_key,
                1.max(rent.minimum_balance(programdata_len)),
                programdata_len as u64,
                program_id,
            );

            // pass an extra account to avoid the overly strict UnbalancedInstruction error
            instruction
                .accounts
                .push(AccountMeta::new(buffer_key, false));

            invoke_context
                .native_invoke_signed(instruction, &[&[new_program_id.as_ref(), &[bump_seed]]])?;
```

**File:** programs/system/src/system_processor.rs (L161-174)
```rust
    // if it looks like the `to` account is already in use, bail
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        if to.get_lamports() > 0 {
            ic_msg!(
                invoke_context,
                "Create Account: account {:?} already in use",
                to_address
            );
            return Err(SystemError::AccountAlreadyInUse.into());
        }

        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
```

**File:** programs/system/src/system_processor.rs (L184-213)
```rust
/// Create a new account without checking for 0 lamports. All other checks remain.
/// Intended for use where account has already had rent paid in whole or in part
/// before creation.
#[allow(clippy::too_many_arguments)]
fn create_account_allow_prefund(
    to_account_index: IndexOfAccount,
    to_address: &Address,
    from_and_lamports: Option<(IndexOfAccount, u64)>,
    space: u64,
    owner: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
    if let Some((from_account_index, lamports)) = from_and_lamports
        && lamports > 0
    {
        transfer(
            from_account_index,
            to_account_index,
            lamports,
            invoke_context,
            instruction_context,
        )?;
    }
    Ok(())
```

**File:** feature-set/src/lib.rs (L2463-2466)
```rust
        (
            create_account_allow_prefund::id(),
            "SIMD-0312: Enable CreateAccountAllowPrefund system program instruction",
        ),
```
