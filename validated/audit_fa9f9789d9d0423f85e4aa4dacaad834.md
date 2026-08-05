This is a solid match. Agave's system program has this exact pattern, and notably, a newer instruction (`CreateAccountAllowPrefund`) was added specifically to work around it—confirming this is a real, currently-live gap for the original instructions.

### Title
Deterministic/observable target address pre-funding griefing on `CreateAccount`/`CreateAccountWithSeed` - ([File: programs/system/src/system_processor.rs])

### Summary
The System program's `create_account` path rejects account creation whenever the target `to` account already holds any lamports, treating it as "already in use." [1](#0-0)  Because Solana's `Transfer` instruction lets any signer send lamports to *any* pubkey with no ownership or signature check on the recipient, an attacker who learns the intended `to` address before it lands on-chain can pre-fund it with a trivial amount of lamports, causing the legitimate `CreateAccount`/`CreateAccountWithSeed` transaction to fail deterministically with `SystemError::AccountAlreadyInUse`. This is the direct on-chain analog of the reported `create2`/`salt` front-running griefing: instead of pre-occupying a deterministic contract address, the attacker pre-occupies (funds) a deterministic or otherwise-known account address.

### Finding Description
Two address-derivation paths make the target predictable/observable ahead of confirmation:

1. `CreateAccountWithSeed`/`AllocateWithSeed`/`AssignWithSeed` derive the target address purely from `(base, seed, owner)` via `Pubkey::create_with_seed`, all of which are public inputs known to anyone who can observe the seed/base convention (e.g. protocol-standardized seeds, or values visible once the transaction is gossiped/simulated). `Address::create` re-derives and verifies this address but performs no anti-squatting protection. [2](#0-1) 
2. Plain `CreateAccount` uses a fresh keypair chosen by the client, but that pubkey becomes visible as soon as the unconfirmed transaction is observable (e.g. in the network before landing, or via RPC simulation), giving a would-be griefer a window to act first.

In both cases, the actual enforcement point is in `create_account`, which unconditionally fails if `to.get_lamports() > 0`: [3](#0-2)  This differs from EVM's `create2`, where an attacker needs to guess/reuse a salt to collide with a contract address; here, no signature or ownership is required to write lamports to an arbitrary destination account, so "pre-occupying" an address only costs a `Transfer` instruction with 1 lamport.

The `AllocateWithSeed`/`AssignWithSeed` handlers reachable from the same `Address::create` derivation similarly don't defend against being pre-loaded, though `allocate` only blocks on non-empty *data* or foreign owner, not lamports: [4](#0-3) 

Notably, Agave has already partially acknowledged this exact griefing class: `CreateAccountAllowPrefund` was introduced explicitly to support accounts that were "already had rent paid in whole or in part before creation," and its handler deliberately omits the lamports-already-present check that `create_account` enforces. [5](#0-4)  This confirms the underlying gap is real, but it is opt-in (feature-gated, and callers must switch instructions) — see the feature-gate check in the dispatcher: [6](#0-5)  Any caller still using the original `CreateAccount`/`CreateAccountWithSeed` instructions (the vast majority of existing on-chain programs and client tooling, e.g. CLI nonce-account creation) remains exposed. [7](#0-6) 

### Impact Explanation
A griefer can cause repeated, deterministic transaction failures for any protocol or user relying on `CreateAccount`/`CreateAccountWithSeed` for account initialization (e.g. nonce accounts, seed-derived program accounts, first-time account setup flows). Where the target address is derived from a fixed `base`/`seed`/`owner` convention that the application cannot freely change without breaking its own address-lookup logic, the DoS is effectively persistent rather than a one-time retry, unlike the original EVM report where simply adding `msg.sender` to the salt resolves the issue for future attempts.

### Likelihood Explanation
Likelihood is medium: exploitation requires no privileged access, only knowledge of the `(base, seed, owner)` tuple (often standardized/public) or timing visibility into an unconfirmed transaction's fresh keypair, plus the cost of a single 1-lamport `Transfer`. As in the original report, the attacker has limited direct financial incentive beyond griefing, which keeps likelihood at medium rather than high.

### Recommendation
For call sites still using `CreateAccount`/`CreateAccountWithSeed`, migrate to `CreateAccountAllowPrefund` (already merged) wherever pre-funding is a plausible operational scenario, and audit whether the "already in use" check in `create_account` [3](#0-2)  should be relaxed by default (or made configurable) for `CreateAccountWithSeed`, since its address is deterministic and non-adversarially-reusable pre-funding should not need to fail creation outright, only the surplus lamports should be added at creation time as `create_account_allow_prefund` already does. [8](#0-7) 

### Proof of Concept
1. Identify a pending/soon-to-be-submitted `CreateAccountWithSeed { base, seed, owner, .. }` (or observe a fresh `CreateAccount` target pubkey in an unconfirmed transaction).
2. Compute `to = Pubkey::create_with_seed(&base, &seed, &owner)` (public computation, no keys needed), matching the derivation in `Address::create`. [9](#0-8) 
3. Submit a plain `SystemInstruction::Transfer` sending 1 lamport from any attacker-controlled account to `to`. This succeeds because `Transfer` requires only that the sender signs; it performs no check on the recipient's ownership. [10](#0-9) 
4. When the legitimate `CreateAccount`/`CreateAccountWithSeed` transaction lands, `to.get_lamports() > 0` is now true, and the instruction fails with `SystemError::AccountAlreadyInUse`, mirrored by the existing test `test_create_already_in_use`. [11](#0-10)

### Citations

**File:** programs/system/src/system_processor.rs (L43-72)
```rust
    fn create(
        address: &Pubkey,
        with_seed: Option<(&Pubkey, &str, &Pubkey)>,
        invoke_context: &InvokeContext,
    ) -> Result<Self, InstructionError> {
        let base = if let Some((base, seed, owner)) = with_seed {
            // The conversion from `PubkeyError` to `InstructionError` through
            // num-traits is incorrect, but it's the existing behavior.
            let address_with_seed =
                Pubkey::create_with_seed(base, seed, owner).map_err(|e| e as u64)?;
            // re-derive the address, must match the supplied address
            if *address != address_with_seed {
                ic_msg!(
                    invoke_context,
                    "Create: address {} does not match derived address {}",
                    address,
                    address_with_seed
                );
                return Err(SystemError::AddressWithSeedMismatch.into());
            }
            Some(*base)
        } else {
            None
        };

        Ok(Self {
            address: *address,
            base,
        })
    }
```

**File:** programs/system/src/system_processor.rs (L91-100)
```rust
    // if it looks like the `to` account is already in use, bail
    //   (note that the id check is also enforced by message_processor)
    if !account.get_data().is_empty() || !system_program::check_id(account.get_owner()) {
        ic_msg!(
            invoke_context,
            "Allocate: account {:?} already in use",
            address
        );
        return Err(SystemError::AccountAlreadyInUse.into());
    }
```

**File:** programs/system/src/system_processor.rs (L161-171)
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

**File:** programs/system/src/system_processor.rs (L245-268)
```rust
fn transfer(
    from_account_index: IndexOfAccount,
    to_account_index: IndexOfAccount,
    lamports: u64,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    if !instruction_context.is_instruction_account_signer(from_account_index)? {
        ic_msg!(
            invoke_context,
            "Transfer: `from` account {} must sign",
            instruction_context.get_key_of_instruction_account(from_account_index)?,
        );
        return Err(InstructionError::MissingRequiredSignature);
    }

    transfer_verified(
        from_account_index,
        to_account_index,
        lamports,
        invoke_context,
        instruction_context,
    )
}
```

**File:** programs/system/src/system_processor.rs (L530-547)
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
```

**File:** programs/system/src/system_processor.rs (L1014-1041)
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
    }
```

**File:** cli/src/nonce.rs (L462-538)
```rust
    let nonce_account_pubkey = config.signers[nonce_account].pubkey();
    let nonce_account_address = if let Some(ref seed) = seed {
        Pubkey::create_with_seed(&nonce_account_pubkey, seed, &system_program::id())?
    } else {
        nonce_account_pubkey
    };

    check_unique_pubkeys(
        (&config.signers[0].pubkey(), "cli keypair".to_string()),
        (&nonce_account_address, "nonce_account".to_string()),
    )?;

    let minimum_balance = rpc_client
        .get_minimum_balance_for_rent_exemption(State::size())
        .await?;
    if amount == SpendAmount::All {
        amount = SpendAmount::AllForAccountCreation {
            create_account_min_balance: minimum_balance,
        };
    }

    let nonce_authority = nonce_authority.unwrap_or_else(|| config.signers[0].pubkey());

    let compute_unit_limit = ComputeUnitLimit::Simulated;
    let build_message = |lamports| {
        let ixs = if let Some(seed) = seed.clone() {
            create_nonce_account_with_seed(
                &config.signers[0].pubkey(), // from
                &nonce_account_address,      // to
                &nonce_account_pubkey,       // base
                &seed,                       // seed
                &nonce_authority,
                lamports,
            )
            .with_memo(memo)
            .with_compute_unit_config(&ComputeUnitConfig {
                compute_unit_price,
                compute_unit_limit,
            })
        } else {
            create_nonce_account(
                &config.signers[0].pubkey(),
                &nonce_account_pubkey,
                &nonce_authority,
                lamports,
            )
            .with_memo(memo)
            .with_compute_unit_config(&ComputeUnitConfig {
                compute_unit_price,
                compute_unit_limit,
            })
        };
        Message::new(&ixs, Some(&config.signers[0].pubkey()))
    };

    let latest_blockhash = rpc_client.get_latest_blockhash().await?;

    let (message, lamports) = resolve_spend_tx_and_check_account_balance(
        rpc_client,
        false,
        amount,
        &latest_blockhash,
        &config.signers[0].pubkey(),
        compute_unit_limit,
        build_message,
        config.commitment,
    )
    .await?;

    if let Ok(nonce_account) = get_account(rpc_client, &nonce_account_address).await {
        let err_msg = if state_from_account(&nonce_account).is_ok() {
            format!("Nonce account {nonce_account_address} already exists")
        } else {
            format!("Account {nonce_account_address} already exists and is not a nonce account")
        };
        return Err(CliError::BadParameter(err_msg).into());
    }
```
