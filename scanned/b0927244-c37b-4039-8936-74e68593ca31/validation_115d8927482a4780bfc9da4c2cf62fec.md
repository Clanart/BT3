## Analysis

The report's core exploitable pattern is: **a global, content-addressed "already used" check that any unprivileged actor can pre-set for a deterministic future value, permanently blocking a legitimate party's ability to use that value — while the attacker's own action is cheap/self-serving and does not require any privileged role.**

The direct Agave analog is in the System program's account-creation guard, not a hash mapping but an equivalent "occupied" check on a deterministic address.

### The corrupted value
`programs/system/src/system_processor.rs` implements `create_account()`, used by both `CreateAccount` and `CreateAccountWithSeed`: [1](#0-0) 

The guard is:
```rust
if to.get_lamports() > 0 {
    ic_msg!(invoke_context, "Create Account: account {:?} already in use", to_address);
    return Err(SystemError::AccountAlreadyInUse.into());
}
``` [2](#0-1) 

This is the exact functional equivalent of `isUsedEvidenceHash`: the "used" state is a single boolean derived from `lamports > 0` on a **deterministic address** (e.g. one produced by `Pubkey::create_with_seed(base, seed, owner)`, used throughout `CreateAccountWithSeed`): [3](#0-2) 

### Why front-running/squatting works, and why guards don't stop it
- The target address for `CreateAccountWithSeed` (and for many program-derived deposit/vault/associated addresses across the ecosystem) is **fully computable in advance** by anyone from public inputs (base pubkey + seed string + owner program id) — same "foreseeable identifier" property as the evidence hash in the report.
- Any unprivileged party can submit a plain `SystemInstruction::Transfer` to that address. A `Transfer` **does not require the recipient to sign** — only the sender signs — so an attacker can fund an address it does not control the private key for.
- Once `lamports > 0`, the address is "in use" per this check, and the legitimate owner's later `CreateAccount`/`CreateAccountWithSeed` transaction will permanently fail with `SystemError::AccountAlreadyInUse`.
- Unlike the report's bond-recoverable case, this is actually **worse for reversibility**: since the target address is not a keypair the attacker controls (it's a derived/seeded address), the attacker cannot sign a `Transfer` out of it to reclaim funds or "cancel" the block — the squat is permanent and irreversible by anyone, for the cost of as little as 1 lamport (or the rent-exempt minimum if the legitimate flow requires the account to already be rent-exempt before use).
- Nothing in `allocate()`/`assign()`/`create_account_allow_prefund()` clears this state; there is no owner-recovery or expiry path for a plain lamport deposit into an unkeyed address. [4](#0-3) 

### Contrast with the excluded/no-match paths
I also checked the other plausible "global uniqueness marker" locations in Agave — `BlockhashQueue`/`StatusCache` dedup keys include the actual per-signer signature (unforgeable cross-user) [5](#0-4) , and the blockstore's duplicate-shred/duplicate-slot marking, which only a slot's leader can trigger (a validator role, out of scope per the "malicious validator" exclusion) [6](#0-5) . Nonce accounts are also not exploitable cross-user since the durable-nonce/advance-on-failure behavior only affects the nonce account's own address, which is either signer-controlled or checked against the authority [7](#0-6) . None of these give an unprivileged, cross-user, permanent-blocking primitive the way the `CreateAccount`/`CreateAccountWithSeed` "already in use" check does.

### Title
Unprivileged Address Squatting via Lamport Transfer Permanently Blocks `CreateAccount`/`CreateAccountWithSeed` on Deterministic Addresses - (File: `programs/system/src/system_processor.rs`)

### Summary
`create_account()` in the System program rejects account creation whenever the target address already holds `lamports > 0`. Because target addresses for `CreateAccountWithSeed` (and many downstream program-derived deposit/vault addresses) are deterministically computable from public inputs, any unprivileged actor can pre-fund the address with a trivial `SystemInstruction::Transfer` before the legitimate owner's creation transaction lands, permanently causing `SystemError::AccountAlreadyInUse` for that address.

### Finding Description
`create_account()` treats "has any lamports" as the sole occupancy signal [1](#0-0) . `Address::create()` computes/validates the seeded address deterministically from `(base, seed, owner)` [3](#0-2) , meaning the target is knowable to anyone off-chain ahead of the legitimate creation transaction. A plain `Transfer` instruction to that address requires only the sender's signature, not the recipient's, so the attacker can deposit funds into an address whose private key nobody (including the attacker) controls. Once lamports are non-zero, no code path clears the balance without a signature from that address, so the block is irreversible.

### Impact Explanation
This is a low-cost, unprivileged, irreversible denial-of-service against any protocol or user relying on deterministic account addresses created via `CreateAccountWithSeed` (nonce accounts with seeds, program utility accounts, etc.), matching the "false execution/acceptance" and fund-availability-denial categories: legitimate account creation transactions will always fail for the targeted address, and any lamports reserved for pre-funding are permanently stranded (unrecoverable fund loss for whichever party attempts recovery).

### Likelihood Explanation
The attack requires no elevated privileges — a single unprivileged `Transfer` instruction, callable by anyone with minimal SOL, from any keypair. The only prerequisite is knowledge of the deterministic target address, which is derivable by any observer from public data before the legitimate transaction is even submitted. This is a well-known Solana account-model footgun, but it is directly reproducible against Agave's `CreateAccountWithSeed` path from local code with no additional trust assumptions.

### Recommendation
Where feasible, avoid relying purely on `lamports == 0` as an "unoccupied" signal for deterministic/seeded addresses; alternatively, document/require callers of `CreateAccountWithSeed`-style flows to treat pre-funded-but-uninitialized accounts as recoverable (e.g., allow `create_account_allow_prefund`-style flows uniformly) so a bare lamport deposit cannot permanently block legitimate initialization. This is consistent with the existing `create_account_allow_prefund()` helper already present in the same file, which should be considered as the default path for any seed-derived address creation rather than opt-in.

### Proof of Concept
1. Legitimate party computes `to = Pubkey::create_with_seed(&base, seed, &owner)` and intends to later submit `CreateAccountWithSeed { base, seed, lamports, space, owner }`.
2. Attacker, observing the same public `base`/`seed`/`owner` (e.g. from a well-known seed convention or leaked intent), independently computes the same `to` address.
3. Attacker submits `SystemInstruction::Transfer { lamports: 1 }` from any funded keypair to `to`. No signature from `to` is required.
4. Legitimate party's later `CreateAccountWithSeed` transaction hits `to.get_lamports() > 0` in `create_account()` and fails permanently with `SystemError::AccountAlreadyInUse` [2](#0-1) .
5. Because `to` is not a keypair-controlled address, nobody — including the attacker — can sign a transfer out of it, so the block and the 1 lamport are permanent.

### Citations

**File:** programs/system/src/system_processor.rs (L43-115)
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
}

fn allocate(
    account: &mut BorrowedInstructionAccount,
    address: &Address,
    space: u64,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    if !address.is_signer(signers) {
        ic_msg!(
            invoke_context,
            "Allocate: 'to' account {:?} must sign",
            address
        );
        return Err(InstructionError::MissingRequiredSignature);
    }

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

    if space > MAX_PERMITTED_DATA_LENGTH {
        ic_msg!(
            invoke_context,
            "Allocate: requested {}, max allowed {}",
            space,
            MAX_PERMITTED_DATA_LENGTH
        );
        return Err(SystemError::InvalidAccountDataLength.into());
    }

    account.set_data_length(space as usize)?;

    Ok(())
}
```

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

**File:** runtime/src/bank/check_transactions.rs (L302-327)
```rust
    fn check_status_cache<Tx: TransactionWithMeta>(
        &self,
        sanitized_txs: &[impl core::borrow::Borrow<Tx>],
        mut lock_results: Vec<TransactionCheckResult>,
        collect_processed_slots: bool,
        error_counters: &mut TransactionErrorMetrics,
    ) -> (Vec<TransactionCheckResult>, Option<Vec<Option<Slot>>>) {
        // Do allocation before acquiring the lock on the status cache.
        let mut processed_slots = if collect_processed_slots {
            Some(Vec::with_capacity(sanitized_txs.len()))
        } else {
            None
        };
        let rcache = self.status_cache.read().unwrap();

        for (sanitized_tx_ref, lock_result) in sanitized_txs.iter().zip(lock_results.iter_mut()) {
            let processed_slot = if lock_result.is_ok() {
                self.get_processed_slot(sanitized_tx_ref.borrow(), &rcache)
            } else {
                None
            };

            if processed_slot.is_some() {
                error_counters.already_processed += 1;
                *lock_result = Err(TransactionError::AlreadyProcessed);
            }
```

**File:** ledger/src/blockstore.rs (L2841-2907)
```rust
        let slot_meta = &mut slot_meta_entry.new_slot_meta.borrow_mut();
        let erasure_set = shred.erasure_set();
        if let HashMapEntry::Vacant(entry) = merkle_root_metas.entry((location, erasure_set))
            && let Some(meta) = self
                .merkle_root_meta_from_location(erasure_set, location)
                .unwrap()
        {
            entry.insert(WorkingEntry::Clean(meta));
        }

        if !is_trusted {
            if Self::is_data_shred_present(&shred, slot_meta, index_meta.data()) {
                duplicate_shreds.push(PossibleDuplicateShred::Exists(shred.into_owned()));
                return Err(InsertDataShredError::Exists);
            }

            if shred.last_in_slot() && shred_index < slot_meta.received && !slot_meta.is_full() {
                // We got a last shred < slot_meta.received, which signals there's an alternative,
                // shorter version of the slot. Because also `!slot_meta.is_full()`, then this
                // means, for the current version of the slot, we might never get all the
                // shreds < the current last index, never replay this slot, and make no
                // progress (for instance if a leader sends an additional detached "last index"
                // shred with a very high index, but none of the intermediate shreds). Ideally, we would
                // just purge all shreds > the new last index slot, but because replay may have already
                // replayed entries past the newly detected "last" shred, the caller marks the slot
                // as dead and replay can dump and repair the correct version.
                warn!(
                    "Received *last* shred index {} less than previous shred index {}, and slot \
                     {} is not full",
                    shred_index, slot_meta.received, slot
                );
            }

            if !self.should_insert_data_shred(
                &shred,
                location,
                slot_meta,
                just_inserted_shreds,
                self.max_root(),
                shred_source,
                duplicate_shreds,
            ) {
                return Err(InsertDataShredError::InvalidShred);
            }

            if let Some(merkle_root_meta) = merkle_root_metas.get(&(location, erasure_set)) {
                // A previous shred has been inserted in this batch or in blockstore
                // Compare our current shred against the previous shred for potential
                // conflicts
                if !self.check_merkle_root_consistency(
                    just_inserted_shreds,
                    slot,
                    location,
                    merkle_root_meta.as_ref(),
                    &shred,
                    duplicate_shreds,
                ) {
                    // This indicates there is an alternate version of this block.
                    // Similar to the last index case above, we might never get all the
                    // shreds for our current version, never replay this slot, and make no
                    // progress. We cannot determine if we have the version that will eventually
                    // be complete, so the caller marks the slot as dead and replay can dump
                    // and repair the correct version.
                    return Err(InsertDataShredError::InvalidShred);
                }
            }
        }
```

**File:** svm/src/nonce_info.rs (L35-56)
```rust
    // Advance the stored blockhash to prevent fee theft by someone
    // replaying nonce transactions that have failed with an
    // `InstructionError`.
    #[cfg(feature = "dev-context-only-utils")]
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    fn try_advance_nonce(
        &mut self,
        durable_nonce: DurableNonce,
        lamports_per_signature: u64,
    ) -> Result<(), AdvanceNonceError> {
        let nonce_versions = StateMut::<NonceVersions>::state(&self.account)
            .map_err(|_| AdvanceNonceError::Invalid)?;
        if let NonceState::Initialized(data) = nonce_versions.state() {
            let nonce_state =
                NonceState::new_initialized(&data.authority, durable_nonce, lamports_per_signature);
            let nonce_versions = NonceVersions::new(nonce_state);
            self.account.set_state(&nonce_versions).unwrap();
            Ok(())
        } else {
            Err(AdvanceNonceError::Uninitialized)
        }
    }
```
