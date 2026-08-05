## Analysis

I found a real Agave analog. The Salty bug's broken invariant is: *an unprivileged actor can pre-occupy a deterministically-derivable identifier/slot before the legitimate/privileged flow tries to use it, permanently blocking that legitimate flow.* In Agave's System Program, the equivalent invariant is broken for account creation: an unprivileged actor can pre-fund a deterministic address before the legitimate creator submits their `CreateAccount`/`CreateAccountWithSeed` instruction, permanently blocking that creation.

### Title
Unprivileged account pre-funding permanently blocks deterministic `CreateAccount`/`CreateAccountWithSeed` (System Program) - (`programs/system/src/system_processor.rs`)

### Summary
`SystemInstruction::CreateAccount` and `SystemInstruction::CreateAccountWithSeed` both route through `create_account()`, which unconditionally rejects creation if the destination account already holds any lamports > 0, regardless of who put them there.

### Finding Description
`create_account()` performs the following check before allocating/assigning the target account: [1](#0-0) 
```
    // if it looks like the `to` account is already in use, bail
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        if to.get_lamports() > 0 {
            ...
            return Err(SystemError::AccountAlreadyInUse.into());
        }
        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
```
This is the codepath used by both `CreateAccount` and `CreateAccountWithSeed`: [2](#0-1) 

Since a plain `SystemInstruction::Transfer` to *any* address requires no signature or permission from the recipient, any unprivileged actor can send 1 lamport to a target address before the legitimate owner submits their `CreateAccount`/`CreateAccountWithSeed` transaction. For `CreateAccountWithSeed`, the destination address is fully deterministic — `Pubkey::create_with_seed(base, seed, owner)` — and `base`/`seed`/`owner` are frequently public or predictable ahead of time (e.g. CLI tools deriving nonce/stake/vote account addresses). Once funded, the account permanently fails the `lamports > 0` check and `CreateAccount(WithSeed)` can never succeed for that address — there is no way to "unfund" an account owned by the System Program from outside, since removing lamports requires the account itself to sign a transfer, and an account with 0 keys controlling it (only reachable via `base`+`seed`+`owner` derivation, not a real keypair) can never sign.

This is not a hypothetical: Agave's own developers already fixed exactly this class of bug by introducing a new instruction, `SystemInstruction::CreateAccountAllowPrefund`, gated behind the `create_account_allow_prefund` feature: [3](#0-2) [4](#0-3) 

The existence of a feature-gated fix confirms the underlying flaw in the legacy `CreateAccount`/`CreateAccountWithSeed` path: it is not activated by default and does not replace the old instructions, which remain the ones used throughout the CLI (`cli/src/nonce.rs`, `cli/src/stake.rs`, `cli/src/vote.rs`) for nonce/stake/vote account creation: [5](#0-4) [6](#0-5) [7](#0-6) 

These CLI paths only detect the poisoning after the fact ("already exists" error) — they provide no bypass for the underlying protocol-level guard, so the user must choose a brand-new seed/keypair, and any lamports already deposited by the attacker are stuck at the poisoned address.

### Impact Explanation
This causes denial-of-service against creation of nonce accounts, stake accounts, vote accounts, and any program's PDAs/derived accounts created via `create_account_with_seed`, wherever the base+seed+owner triple (or a raw pre-generated pubkey used in `CreateAccount`) is predictable by an attacker before the creation transaction lands (e.g., visible in a pending/broadcast but not-yet-confirmed transaction, mempool-less but still observable via gossip/QUIC ingestion prior to inclusion, or simply pre-announced addresses). It falls under "non-RPC remote... false execution/acceptance" style disruption of a core, unprivileged, permissionless System Program instruction — an attacker with no privileges beyond being able to send a lamport transfer can deny normal users the ability to create accounts at a specific, otherwise-legitimate address.

### Likelihood Explanation
Likelihood is moderate-to-high in scenarios where the destination address is derived (`CreateAccountWithSeed`) from a known base and seed (common for exchanges/wallets generating deterministic nonce/stake accounts, or dApps generating deterministic PDAs via the System Program's seed mechanism) or where a plain `CreateAccount` destination pubkey is shared or observed before the transaction confirms. The attacker only needs to send a `SystemInstruction::Transfer` of 1 lamport, an extremely cheap, fully permissionless action requiring no coordination with block producers, and can be repeated for many addresses at negligible cost.

### Recommendation
Since Agave has already engineered a fix (`CreateAccountAllowPrefund`), the outstanding gap is that the legacy `CreateAccount`/`CreateAccountWithSeed` instructions retain the `lamports > 0` == "AccountAlreadyInUse" check unconditionally, and most callers/tooling in this codebase (nonce/stake/vote CLI flows) have not migrated to the prefund-tolerant path. Recommend auditing all callers that create deterministic accounts (nonce, stake, vote, PDAs) to adopt `CreateAccountAllowPrefund` once the `create_account_allow_prefund` feature is cluster-activated, and/or relaxing the legacy check to only reject accounts that already have `owner != system_program::id() || data.len() > 0`, using the prefund-aware "lamports may pre-exist" semantics as the default rather than an opt-in feature.

### Proof of Concept
1. Attacker observes/predicts a `base` pubkey and `seed` string that a victim intends to use for `create_account_with_seed` (e.g., a wallet's documented nonce-account derivation scheme), and computes `to = Pubkey::create_with_seed(&base, seed, &owner)`.
2. Attacker submits `SystemInstruction::Transfer { lamports: 1 }` from any funded account to `to`. No signature from `to` is required since `to` is the transfer destination, not source.
3. Victim later submits `SystemInstruction::CreateAccountWithSeed { base, seed, lamports, space, owner }` targeting `to`.
4. `create_account()` observes `to.get_lamports() > 0` (from step 2) and returns `Err(SystemError::AccountAlreadyInUse)`, exactly as exercised by the existing test `test_create_already_in_use`: [8](#0-7) 
5. The victim's account creation permanently fails for that `base`/`seed`/`owner` combination; the CLI reports "already exists and is not a [nonce/stake/vote] account", as seen in `cli/src/nonce.rs:531-538`, `cli/src/stake.rs:1495-1503`, `cli/src/vote.rs:1113-1125`, forcing the victim to pick a new seed and abandoning the poisoned address with the attacker's lamports stuck there.

### Citations

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

**File:** programs/system/src/system_processor.rs (L330-378)
```rust
        SystemInstruction::CreateAccount {
            lamports,
            space,
            owner,
        } => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(1)?,
                None,
                invoke_context,
            )?;
            create_account(
                0,
                1,
                &to_address,
                lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }

        SystemInstruction::CreateAccountWithSeed {
            base,
            seed,
            lamports,
            space,
            owner,
        } => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(1)?,
                Some((&base, &seed, &owner)),
                invoke_context,
            )?;
            create_account(
                0,
                1,
                &to_address,
                lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }
```

**File:** programs/system/src/system_processor.rs (L530-530)
```rust
        SystemInstruction::CreateAccountAllowPrefund {
```

**File:** programs/system/src/system_processor.rs (L950-984)
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
```

**File:** cli/src/nonce.rs (L531-538)
```rust
    if let Ok(nonce_account) = get_account(rpc_client, &nonce_account_address).await {
        let err_msg = if state_from_account(&nonce_account).is_ok() {
            format!("Nonce account {nonce_account_address} already exists")
        } else {
            format!("Account {nonce_account_address} already exists and is not a nonce account")
        };
        return Err(CliError::BadParameter(err_msg).into());
    }
```

**File:** cli/src/stake.rs (L1495-1503)
```rust
    if !sign_only {
        if let Ok(stake_account) = rpc_client.get_account(&stake_account_address).await {
            let err_msg = if stake_account.owner == stake::program::id() {
                format!("Stake account {stake_account_address} already exists")
            } else {
                format!("Account {stake_account_address} already exists and is not a stake account")
            };
            return Err(CliError::BadParameter(err_msg).into());
        }
```

**File:** cli/src/vote.rs (L1113-1125)
```rust
    if !sign_only {
        if let Ok(response) = rpc_client
            .get_account_with_commitment(&vote_account_address, config.commitment)
            .await
            && let Some(vote_account) = response.value
        {
            let err_msg = if vote_account.owner == solana_vote_program::id() {
                format!("Vote account {vote_account_address} already exists")
            } else {
                format!("Account {vote_account_address} already exists and is not a vote account")
            };
            return Err(CliError::BadParameter(err_msg).into());
        }
```
