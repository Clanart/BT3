## Title
Deterministic-address account creation can be permanently blocked by front-running lamport transfers, causing `AccountAlreadyInUse` denial-of-service - (File: `programs/system/src/system_processor.rs`)

### Summary
The Uniswap-v2-nft report's core primitive is: an attacker can precompute a deterministic contract address before it is legitimately initialized, then use a state-changing call at that address to make the real initialization permanently fail, causing denial of service for the rightful pair. Agave's System program `CreateAccount`/`CreateAccountWithSeed` handler exhibits the same broken invariant: account addresses derived deterministically (via `create_with_seed`, PDAs via `find_program_address`, etc.) can be pre-funded by anyone with a plain lamport `Transfer` before the legitimate creator submits their `CreateAccount` instruction, and the legitimate creation call then fails hard, permanently, with `AccountAlreadyInUse`.

### Finding Description
`create_account` in `programs/system/src/system_processor.rs` guards the "to" account with a strict pre-condition: [1](#0-0) 

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

This check is on `lamports > 0`, not on data/owner state. Because `SystemInstruction::Transfer` lets *anyone* send lamports to *any* pubkey without that pubkey ever signing [2](#0-1) , an attacker who can predict a target address — e.g. a seed-derived address from `Pubkey::create_with_seed(base, seed, owner)` used for nonce accounts [3](#0-2) , or a PDA such as the address-lookup-table account derived from `(authority, recent_slot, bump_seed)` [4](#0-3)  — can send 1 lamport to that address before the legitimate owner's `CreateAccount`/`CreateAccountWithSeed` transaction lands. The subsequent legitimate creation attempt then hits `to.get_lamports() > 0` and fails permanently with `SystemError::AccountAlreadyInUse`, exactly mirroring the report's "precompute address → front-run → block legitimate use" attack workflow.

The system program is aware of this exact class of griefing: it recently added a parallel instruction, `CreateAccountAllowPrefund`, whose comment states it exists explicitly "for use where account has already had rent paid in whole or in part before creation," and whose implementation deliberately *removes* the `lamports > 0` guard, checking only that the account is empty/system-owned via `allocate`: [5](#0-4) 

The mitigating instruction is feature-gated: [6](#0-5) 

Since `create_account_allow_prefund` is opt-in per-instruction and the legacy `CreateAccount`/`CreateAccountWithSeed` path (used by essentially all existing on-chain integrations, the CLI's nonce-account creation, and the address-lookup-table program) still enforces the strict `lamports > 0` guard, any protocol that relies on a deterministic address computed off-chain before submission — the accounts-cluster-bench tool builds exactly such addresses via `create_with_seed` [7](#0-6) , and CLI nonce-account creation does the same [3](#0-2)  — remains exposed to this pre-funding griefing today.

### Impact Explanation
This is a denial-of-service primitive against unprivileged, deterministic account creation. It does not steal funds directly, but it can:
- Permanently block creation of a specific nonce account, lookup table, or seed-derived account at a chosen address, forcing the victim to abandon that address (loss of the deterministic-address guarantee that many protocols/tools rely on).
- Grief automated systems (e.g., `accounts-cluster-bench`-style bulk account creators, or any program that CPIs into `create_account`/`create_account_with_seed` for a caller-supplied deterministic address) by causing their `CreateAccount` calls to fail with `AccountAlreadyInUse`, since the check triggers on lamports alone, irrespective of data or owner.
- Cause any transferred lamports that accompanied the failed creation attempt to be returned/rolled back (transaction fails atomically), but the *address itself* becomes unusable via the standard `CreateAccount` path until manually swept by whoever controls the account (nobody, if it was never truly owned) or until `CreateAccountAllowPrefund` is activated and adopted by the caller.

This matches the "Medium" risk classification of the original report: it is a griefing/DoS vector against legitimate account-creation flows, not a direct fund-theft bug, but it is real, unprivileged, and requires no colluding validator or node.

### Likelihood Explanation
High likelihood of triggering, low cost to the attacker: a plain `SystemInstruction::Transfer` of 1 lamport to a precomputed address is trivially cheap and requires no special privilege — it is a completely ordinary user transaction. The precondition (predictability of the target address) is satisfied for any deterministic-address scheme: `create_with_seed` addresses are pure functions of public inputs (base pubkey, seed string, owner), and PDA addresses (`find_program_address`) are likewise fully computable off-chain, as demonstrated in the address-lookup-table `create_lookup_table` client code. The attack does not need to win a literal race against the exact transaction — the attacker can pre-fund *any time before* the legitimate creation transaction lands, since a stray lamport sitting on that address is a permanent state that survives across slots until someone consumes it.

### Recommendation
- For any protocol/tool that creates accounts at addresses computable in advance by third parties, migrate to `SystemInstruction::CreateAccountAllowPrefund` (once the corresponding feature is activated) instead of `CreateAccount`/`CreateAccountWithSeed`, so that pre-existing lamports at the target address do not block initialization.
- Alternatively, harden the legacy path: instead of failing on `lamports > 0`, `create_account` could check only that the account is uninitialized (`data.is_empty() && system_program::check_id(owner)`, as `allocate` already does) and merge any pre-existing lamports into the transfer, removing the separate stricter check that is exploitable via unsolicited transfers.
- Documentation/guidance for developers who compute addresses via `create_with_seed`/PDAs before submitting `CreateAccount` should explicitly warn that such addresses can be grief-funded and that the caller should treat `AccountAlreadyInUse` on a fresh address as a possible griefing signal rather than an application error.

### Proof of Concept
1. Compute a deterministic address off-chain, e.g. `let addr = Pubkey::create_with_seed(&base, "my-seed", &system_program::id())` — the exact scheme used in `accounts-cluster-bench` [7](#0-6)  or CLI nonce creation [3](#0-2) .
2. Attacker submits `SystemInstruction::Transfer { lamports: 1 }` from any funded keypair to `addr` before the legitimate owner's transaction lands. This requires no signature from `addr` and succeeds unconditionally.
3. Legitimate owner submits `SystemInstruction::CreateAccountWithSeed { base, seed, lamports, space, owner }` targeting `addr`.
4. In `create_account` (`programs/system/src/system_processor.rs:160-174`), `to.get_lamports() > 0` is now true (from step 2), so the call returns `Err(SystemError::AccountAlreadyInUse)`, and the address is permanently unusable via this path — mirroring `test_create_already_in_use`'s "already has lamports" branch which is asserted to fail with `AccountAlreadyInUse` [8](#0-7) .

### Citations

**File:** programs/system/src/system_processor.rs (L160-174)
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

**File:** programs/system/src/system_processor.rs (L389-389)
```rust
        SystemInstruction::Transfer { lamports } => {
```

**File:** programs/system/src/system_processor.rs (L530-541)
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

**File:** cli/src/nonce.rs (L463-467)
```rust
    let nonce_account_address = if let Some(ref seed) = seed {
        Pubkey::create_with_seed(&nonce_account_pubkey, seed, &system_program::id())?
    } else {
        nonce_account_pubkey
    };
```

**File:** cli/src/address_lookup_table.rs (L564-565)
```rust
    let (create_lookup_table_ix, lookup_table_address) =
        create_lookup_table(authority_address, payer_address, clock.slot);
```

**File:** accounts-cluster-bench/src/main.rs (L208-219)
```rust
            let seed = max_created_seed.fetch_add(1, Ordering::Relaxed).to_string();
            let to_pubkey =
                Pubkey::create_with_seed(&base_keypair.pubkey(), &seed, &program_id).unwrap();
            let mut instructions = vec![system_instruction::create_account_with_seed(
                &keypair.pubkey(),
                &to_pubkey,
                &base_keypair.pubkey(),
                &seed,
                balance,
                space,
                &program_id,
            )];
```
