## Title
`CreateAccount` griefing: an attacker can pre-fund a soon-to-be-created address to permanently block legitimate account creation — (`programs/system/src/system_processor.rs`)

### Summary
The External Report describes a griefing pattern in Locke.sol: an attacker can pre-populate a piece of state that a privileged operation asserts must be zero/unused, and that state can only be reset by a party who is not the one performing the privileged action, making the operation permanently unusable. The direct analog in Agave's System Program is the classic "address pre-funding" griefing vector against `SystemInstruction::CreateAccount`.

### Finding Description
`create_account()` in the System Program requires the destination account to be completely unused before it will create it — it checks `to.get_lamports() > 0` and errors with `SystemError::AccountAlreadyInUse` if it is not zero, in addition to requiring the data to be empty and owned by the system program via `allocate`/`assign`: [1](#0-0) 

Because any transfer of lamports to an address is permissionless (a plain `SystemInstruction::Transfer`, or even `SystemInstruction::CreateAccount` failing mid-way leaving lamports, etc. — any deposit works), an attacker who knows a target address in advance (e.g., a deterministic PDA, a nonce/vote/stake account address, or any keypair address whose public key is known before the create transaction lands) can send 1 lamport to that address before the legitimate `CreateAccount` transaction executes. This makes `to.get_lamports() > 0` true, so every subsequent `CreateAccount` attempt against that address fails with `AccountAlreadyInUse`: [2](#0-1) 

This mirrors exactly the Locke.sol bug class: a value that a privileged/expected operation requires to be at a "clean" state (`incentives[who] == 0` in Locke.sol; `lamports == 0` here) can be corrupted by an unprivileged third party before the legitimate actor calls the function, and there is no way for the legitimate creator to reset it (no analog of `claimIncentive()` exists for lamports sitting in an as-yet-unowned address — the funds are simply stuck under system-program ownership at that address).

Agave developers were clearly aware of exactly this griefing pattern: they added a new instruction, `SystemInstruction::CreateAccountAllowPrefund`, gated behind the `create_account_allow_prefund` feature, whose entire purpose is to allow account creation to succeed even when the target address already has a nonzero lamport balance: [3](#0-2) [4](#0-3) 

However, the original `SystemInstruction::CreateAccount` path is not deprecated or patched — it is still fully supported, still used pervasively by existing tooling, CLIs, and on-chain programs (wallets, stake account creation, vote account creation, nonce account creation flows, and any program that calls `system_instruction::create_account` via CPI), and it still contains the unconditional `AccountAlreadyInUse` check shown above. `CreateAccountAllowPrefund` only helps callers who explicitly opt into the new instruction; it does nothing to protect the huge existing surface area that still issues the legacy `CreateAccount` instruction.

### Impact Explanation
An attacker who can predict a target address before it is created (this is trivial for PDAs derived via `create_with_seed`, for freshly generated keypairs whose pubkey is published/broadcast before the creation transaction lands, or for addresses derived by other programs/wallets in a deterministic way) can front-run the legitimate creation with a 1-lamport transfer. The result:
- The legitimate `CreateAccount` transaction permanently fails with `AccountAlreadyInUse` for as long as the griefed lamports remain and no compatible workaround is used.
- Funds intended for the new account (rent-exempt minimum, initial balance) cannot be deposited into a live account — this is a denial-of-service against account creation, and depending on the caller's control flow, can strand user funds in the `from` account's failed transaction or block on-chain protocol logic that assumes `create_account` calls succeed (e.g., escrow/vesting/airdrop-style programs built as CPI callers of the system program), directly matching the "unprivileged issue causing fund loss / false execution acceptance" impact class.
- The only officially provided remedy (`CreateAccountAllowPrefund`) requires the calling program/wallet to be rewritten to use the new instruction and for the feature to be active on the cluster; it is not a fix for the legacy instruction itself.

### Likelihood Explanation
Likelihood is high for any workflow that (a) still uses the legacy `CreateAccount` instruction (which is the overwhelming majority of existing tooling/programs today) and (b) has a predictable destination address prior to the creating transaction landing. The attack requires only a single unprivileged `Transfer` instruction sent by anyone, with no special privileges, no validator/leader assumptions, and negligible cost (1 lamport plus the transaction fee). This matches the "unprivileged Agave issue in transactions" category and does not rely on malicious validators, leaked keys, or trusted-integration assumptions.

### Recommendation
- Encourage/require migration of all account-creation call sites (CLI, SDKs, on-chain programs using CPI to the system program) to `SystemInstruction::CreateAccountAllowPrefund` once the `create_account_allow_prefund` feature is active, since that instruction already implements the correct fix (create account regardless of pre-existing lamport balance).
- Consider deprecating or hardening the legacy `CreateAccount` check so that it treats a "system-owned, empty-data" account with stray lamports as fundable rather than "already in use," matching `create_account_allow_prefund`'s semantics, to close the gap for legacy callers that cannot easily be updated.
- Document the pre-funding griefing risk clearly for developers/integrators still using `CreateAccount`, similar to how the Locke.sol report recommends the trade-off be explicitly communicated rather than "fixed," since removing the `lamports == 0` check outright from the legacy path could have compatibility implications.

### Proof of Concept
1. Attacker learns/derives a target address `T` that a victim intends to create via `SystemInstruction::CreateAccount` (e.g., a `create_with_seed` derived address, or a freshly generated keypair pubkey observed in a pending/queued transaction).
2. Attacker submits `SystemInstruction::Transfer` sending 1 lamport from any funded account to `T` before the victim's `CreateAccount` transaction lands. This succeeds unconditionally via `transfer_verified`/`transfer` in `system_processor.rs` since `T` need not exist and receiving lamports has no gating.
3. Victim's `SystemInstruction::CreateAccount` transaction targeting `T` now hits the check `to.get_lamports() > 0` in `create_account()` and fails with `SystemError::AccountAlreadyInUse`, exactly reproduced by the existing test `test_create_already_in_use` in this file (the "Attempt to create an account that already has lamports" case): [5](#0-4) 
4. The victim has no built-in way to recover — the only remedy is to reissue the creation via `CreateAccountAllowPrefund` (a different instruction the victim's software may not support), or to abandon the address entirely, leaving the 1 lamport stranded and the intended account permanently uncreated via the original code path.

### Citations

**File:** programs/system/src/system_processor.rs (L160-182)
```rust
) -> Result<(), InstructionError> {
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
    transfer(
        from_account_index,
        to_account_index,
        lamports,
        invoke_context,
        instruction_context,
    )
}
```

**File:** programs/system/src/system_processor.rs (L184-214)
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
}
```

**File:** programs/system/src/system_processor.rs (L530-563)
```rust
        SystemInstruction::CreateAccountAllowPrefund {
            lamports,
            space,
            owner,
        } => {
            if !invoke_context
                .get_feature_set()
                .create_account_allow_prefund
            {
                return Err(InstructionError::InvalidInstructionData);
            }
            let from_and_lamports = if lamports > 0 {
                instruction_context.check_number_of_instruction_accounts(2)?;
                Some((1, lamports))
            } else {
                instruction_context.check_number_of_instruction_accounts(1)?;
                None
            };
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(0)?,
                None,
                invoke_context,
            )?;
            create_account_allow_prefund(
                0,
                &to_address,
                from_and_lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }
```

**File:** programs/system/src/system_processor.rs (L950-1013)
```rust
    #[test]
    fn test_create_already_in_use() {
        let new_owner = Pubkey::from([9; 32]);
        let from = Pubkey::new_unique();
        let from_account = AccountSharedData::new(100, 0, &system_program::id());
        let owned_key = Pubkey::new_unique();

        // Attempt to create system account in account already owned by another program
        let original_program_owner = Pubkey::from([5; 32]);
        let owned_account = AccountSharedData::new(0, 0, &original_program_owner);
        let unchanged_account = owned_account.clone();
        let accounts = process_instruction(
            &bincode::serialize(&SystemInstruction::CreateAccount {
                lamports: 50,
                space: 2,
                owner: new_owner,
            })
            .unwrap(),
            vec![(from, from_account.clone()), (owned_key, owned_account)],
            vec![
                AccountMeta {
                    pubkey: from,
                    is_signer: true,
                    is_writable: false,
                },
                AccountMeta {
                    pubkey: owned_key,
                    is_signer: true,
                    is_writable: false,
                },
            ],
            Err(SystemError::AccountAlreadyInUse.into()),
        );
        assert_eq!(accounts[0].lamports(), 100);
        assert_eq!(accounts[1], unchanged_account);

        // Attempt to create system account in account that already has data
        let owned_account = AccountSharedData::new(0, 1, &Pubkey::default());
        let unchanged_account = owned_account.clone();
        let accounts = process_instruction(
            &bincode::serialize(&SystemInstruction::CreateAccount {
                lamports: 50,
                space: 2,
                owner: new_owner,
            })
            .unwrap(),
            vec![(from, from_account.clone()), (owned_key, owned_account)],
            vec![
                AccountMeta {
                    pubkey: from,
                    is_signer: true,
                    is_writable: false,
                },
                AccountMeta {
                    pubkey: owned_key,
                    is_signer: true,
                    is_writable: false,
                },
            ],
            Err(SystemError::AccountAlreadyInUse.into()),
        );
        assert_eq!(accounts[0].lamports(), 100);
        assert_eq!(accounts[1], unchanged_account);

```

**File:** programs/system/src/system_processor.rs (L1014-1040)
```rust
        // Attempt to create an account that already has lamports
        let owned_account = AccountSharedData::new(1, 0, &Pubkey::default());
        let unchanged_account = owned_account.clone();
        let accounts = process_instruction(
            &bincode::serialize(&SystemInstruction::CreateAccount {
                lamports: 50,
                space: 2,
                owner: new_owner,
            })
            .unwrap(),
            vec![(from, from_account), (owned_key, owned_account)],
            vec![
                AccountMeta {
                    pubkey: from,
                    is_signer: true,
                    is_writable: false,
                },
                AccountMeta {
                    pubkey: owned_key,
                    is_signer: true,
                    is_writable: false,
                },
            ],
            Err(SystemError::AccountAlreadyInUse.into()),
        );
        assert_eq!(accounts[0].lamports(), 100);
        assert_eq!(accounts[1], unchanged_account);
```
