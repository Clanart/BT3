## Analysis

The DEPU-1 report describes a griefing primitive: an unprivileged attacker sends a trivial amount of an asset (WETH) to a shared "deposit store" so that a strict-equality balance check performed by a *different, legitimate* user's `createDeposit` call no longer matches the expected `executionFee`, causing that legitimate deposit to permanently revert. The broken invariant is: *"an account's pre-state balance is assumed to be attacker-uncontrolled, but it isn't, and a strict check on that balance is used as a gate for whether the operation is allowed to proceed."*

The closest real analog in Agave is the System Program's `create_account` instruction handler.

### Title
Unprivileged lamport-griefing of `SystemInstruction::CreateAccount` via pre-funding the target address - (`programs/system/src/system_processor.rs`)

### Summary
`create_account` in the System builtin program rejects the instruction outright if the target `to` account already holds any lamports, treating any nonzero balance as "already in use." Because any unprivileged actor can transfer an arbitrary (even 1-lamport) amount of SOL to *any* not-yet-created address before its intended owner submits their `CreateAccount` transaction, an attacker can permanently grief legitimate account creation for a target address, mirroring the GMX `executionFee` exact-match griefing bug class.

### Finding Description
`create_account` explicitly checks the target account's pre-existing lamport balance and fails with `SystemError::AccountAlreadyInUse` if it is nonzero: [1](#0-0) 

There is no mechanism preventing an arbitrary third party from sending lamports to a not-yet-created `Pubkey` via a plain `system_instruction::transfer` before the legitimate party executes `CreateAccount` for that same address — Solana addresses have no "reservation"/ownership before creation. This is functionally identical to the reported invariant break: a legitimate operation depends on an *exact* pre-state condition (`lamports == 0` here, `balance == executionFee` in the report) that any unprivileged actor can corrupt by sending value to the target account ahead of time.

The codebase does contain a fix path — `create_account_allow_prefund`, which permits creation even when the account already holds lamports — but this is gated behind a feature flag (`create_account_allow_prefund` / `CreateAccountAllowPrefund` referenced throughout `feature-set/src/lib.rs` and `programs/system/src/system_processor.rs`), and existing tests confirm the un-guarded legacy `CreateAccount` path still enforces the strict "must be exactly zero lamports" rule: [2](#0-1) 

The regular `allocate` path (used by `CreateAccount`, which calls `allocate_and_assign` then `transfer`) also strictly requires `account.get_data().is_empty()` and system-program ownership — a nonzero-lamports, no-data account owned by the system program is exactly the state an attacker produces with a trivial transfer, and it deterministically causes `AccountAlreadyInUse`: [3](#0-2) 

### Impact Explanation
This is a targeted, unprivileged DoS: any application, wallet, or protocol that derives a deterministic address for a user (e.g., a PDA-adjacent system account, an ephemeral or vanity address, an address-with-seed account, or any address whose public key is known/predictable ahead of time) and later calls `SystemInstruction::CreateAccount` against it can be griefed for essentially free — the attacker only spends a few lamports (which they can even reclaim, since the target account is never actually created and remains owned by/controllable via System). This blocks legitimate account/deposit-style creation flows exactly as in the GMX report, without requiring a malicious validator, leaked keys, or any trusted role — it is a pure unprivileged transaction-level griefing primitive.

### Likelihood Explanation
The likelihood is high for any address that is predictable before creation (a common pattern for on-chain protocols that pre-compute a user's account address off-chain and only create it on first deposit/interaction). The attack costs a single low-fee transfer transaction and is fully permissionless. It is mitigated only where callers use the newer `CreateAccountAllowPrefund` instruction, which is feature-gated and not universally available/adopted, and where callers instead use `CreateAccountWithSeed`/PDAs invoked by a program using `invoke_signed`, which sidesteps the issue because the address can't be independently funded-then-claimed by a race in the same way (still, direct `SystemInstruction::CreateAccount` usage remains exposed).

### Recommendation
- Ensure `CreateAccountAllowPrefund` (or an equivalent lamports-tolerant creation path) is broadly activated and encourage/require its use for any deterministic-address account-creation flow instead of the legacy strict-zero-lamports `CreateAccount`.
- Where the strict check must remain for legacy compatibility, document explicitly that callers relying on `CreateAccount` for predictable addresses are exposed to griefing, and provide guidance to always use `allocate`/`assign` idioms tolerant of pre-funding, or a `try_create` pattern that treats "already has the exact expected lamports and no data" as success rather than failure.

### Proof of Concept
1. Off-chain, an application computes a deterministic target `Pubkey` (e.g., via `create_with_seed`, or simply a keypair whose address will be used before it's on-chain) and informs the user it will call `SystemInstruction::CreateAccount { lamports, space, owner }` for that address.
2. Before the legitimate transaction lands, an attacker submits any ordinary `SystemInstruction::Transfer` sending as little as 1 lamport to that same target `Pubkey`.
3. When the legitimate `CreateAccount` instruction executes, `create_account` (`programs/system/src/system_processor.rs:150-182`) observes `to.get_lamports() > 0` and returns `SystemError::AccountAlreadyInUse`, permanently failing account creation for that address unless the caller happens to use the feature-gated `CreateAccountAllowPrefund` path instead. [4](#0-3) 

---

**Note on confidence:** I could not fully verify which `feature-set` flag activates `CreateAccountAllowPrefund` by default on mainnet-beta/testnet, nor the exact adoption rate of this newer instruction across the ecosystem — this affects how broadly the legacy strict-zero-lamports path is still exercised in practice. This limits certainty on precise real-world likelihood, though the code path itself and its behavior are clearly confirmed in the source.

### Citations

**File:** programs/system/src/system_processor.rs (L149-182)
```rust
#[allow(clippy::too_many_arguments)]
fn create_account(
    from_account_index: IndexOfAccount,
    to_account_index: IndexOfAccount,
    to_address: &Address,
    lamports: u64,
    space: u64,
    owner: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
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
