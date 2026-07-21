### Title
Gateway `validate_proof_facts` uses pre-`pre_process_block` state while batcher uses post-`pre_process_block` state, causing valid client-side-proving transactions to be rejected at admission — (`crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

The gateway's `run_validate_entry_point` simulates block N+1 by advancing the block number with `unchecked_next()`, but reads from the committed state at block N — before `pre_process_block` has written the retrospective block hash for block `N+1 − STORED_BLOCK_HASH_BUFFER`. The `validate_proof_facts` pre-validation check therefore allows `proof_block_number` up to `N − STORED_BLOCK_HASH_BUFFER + 1` (based on the N+1 block-number context), yet the stored block hash for that block is zero in the state the gateway actually reads. The batcher, which calls `pre_process_block` before executing any transaction, has that hash available and accepts the same transaction. The result is a systematic, deterministic gateway rejection of every valid Invoke V3 transaction whose `proof_block_number` equals exactly `N − STORED_BLOCK_HASH_BUFFER + 1`.

---

### Finding Description

**Root cause — context mismatch between block-number and state epoch**

In `run_validate_entry_point` the gateway advances the block number by one:

```rust
block_info.block_number = block_info.block_number.unchecked_next();   // → N+1
``` [1](#0-0) 

This `BlockContext` (with `block_number = N+1`) is handed to `StatefulValidator::perform_validations`, which calls `perform_pre_validation_stage` → `validate_proof_facts`.

Inside `validate_proof_facts`, the upper bound for an acceptable proof block number is computed as:

```rust
let max_allowed =
    current_block_number.0.checked_sub(STORED_BLOCK_HASH_BUFFER)…;
// With current_block_number = N+1:  max_allowed = N+1 − 10 = N−9
``` [2](#0-1) 

So the gateway *permits* `proof_block_number = N − STORED_BLOCK_HASH_BUFFER + 1`.

It then tries to verify the hash:

```rust
let stored_block_hash = state
    .get_storage_at(block_hash_contract_address, StorageKey::from(proof_block_number))?;
if stored_block_hash != proof_block_hash { return Err(…"Block hash mismatch"…); }
``` [3](#0-2) 

The `state` here is the committed state at block N. The OS writes the hash for block `K` into the block-hash contract when processing block `K + STORED_BLOCK_HASH_BUFFER`. Therefore, in state at block N, the highest stored entry is for block `N − STORED_BLOCK_HASH_BUFFER`. The entry for block `N − STORED_BLOCK_HASH_BUFFER + 1` is **zero** — it will only be written when block N+1 is processed.

**What the batcher does differently**

Before executing any transaction in block N+1, the batcher calls `pre_process_block`:

```rust
pub fn pre_process_block(
    state: &mut dyn State,
    old_block_number_and_hash: Option<BlockHashAndNumber>,
    next_block_number: BlockNumber,
    …
) -> StateResult<()> {
    if let Some(BlockHashAndNumber { number, hash }) = old_block_number_and_hash {
        state.set_storage_at(block_hash_contract_address,
                             StorageKey::from(number.0), hash.0)?;
    }
    …
}
``` [4](#0-3) 

The `retrospective_block_hash` helper supplies `number = N+1 − STORED_BLOCK_HASH_BUFFER = N − STORED_BLOCK_HASH_BUFFER + 1`:

```rust
let retrospective_block_number = init.height.0.checked_sub(STORED_BLOCK_HASH_BUFFER);
``` [5](#0-4) 

After `pre_process_block` the state contains the hash for block `N − STORED_BLOCK_HASH_BUFFER + 1`, so `validate_proof_block_hash` succeeds and the transaction is accepted.

**The divergence**

| Stage | Block-number context | State epoch | Hash for `N−buf+1` present? | Outcome |
|---|---|---|---|---|
| Gateway `run_validate_entry_point` | N+1 | Block N (no `pre_process_block`) | **No** (zero) | **Rejected** |
| Batcher block N+1 execution | N+1 | Block N+1 (after `pre_process_block`) | **Yes** | **Accepted** |

This is the direct Sequencer analog of the ERC-4337 caller-context mismatch: the gateway advances the block-number context to N+1 (analogous to `validateUserOp` enforcing `msg.sender == entrypoint`) but then reads from a state that has not yet been updated to match that context (analogous to the external self-call reverting `msg.sender` to the wallet). The proof-block-hash check therefore compares against a zero value instead of the real hash, and the transaction is rejected.

---

### Impact Explanation

**High — Mempool/gateway admission rejects valid transactions before sequencing.**

Every Invoke V3 transaction that carries SNOS proof facts referencing `proof_block_number = N − STORED_BLOCK_HASH_BUFFER + 1` (the freshest block number the protocol permits) is unconditionally rejected by the gateway with `InvalidProofFacts("Block hash mismatch …")`. The same transaction would be accepted by the batcher. Because the off-by-one is structural and deterministic, any user or application that constructs proof facts against the most recent eligible block will be permanently blocked from submitting through the gateway. This constitutes a denial-of-service on the client-side-proving admission path.

---

### Likelihood Explanation

**High.** The condition is triggered deterministically whenever a transaction sets `proof_block_number` to the maximum value the protocol allows for the next block. Client-side proving applications naturally want to reference the most recent available block hash to minimise proof staleness, so this boundary value is the common case. No special attacker capability is required — any ordinary user constructing a valid proof against block `N − STORED_BLOCK_HASH_BUFFER + 1` will hit the rejection.

---

### Recommendation

The gateway's `run_validate_entry_point` must either:

1. **Call `pre_process_block` on the cached state before running `perform_validations`**, supplying the retrospective `BlockHashAndNumber` for block `N − STORED_BLOCK_HASH_BUFFER + 1`, so the state matches the N+1 block-number context; or

2. **Clamp `max_allowed` to `N − STORED_BLOCK_HASH_BUFFER`** (i.e., use the committed block number N, not N+1, when computing the proof-block-number upper bound inside `validate_proof_block_number`), so the block-number context and the state epoch are consistent.

Option 1 is the correct fix because it makes the gateway faithfully replicate the batcher's execution environment. Option 2 is a conservative workaround that avoids the mismatch by tightening the allowed range by one block.

---

### Proof of Concept

```
Let N = latest committed block number (e.g. 1000).
Let STORED_BLOCK_HASH_BUFFER = 10.

1. Obtain the real block hash for block N − 9 = 991 from any RPC node.

2. Construct an Invoke V3 transaction with:
     proof_facts.block_number = 991
     proof_facts.block_hash  = <real hash of block 991>
     proof_facts.program_hash = <any allowed virtual OS program hash>
     proof_facts.config_hash  = <correct virtual OS config hash>

3. Submit to the gateway (starknet_addInvokeTransaction).

Expected gateway response:
  InvalidProofFacts: "Block hash mismatch for block 991.
    Proof block hash: 0x<real>, stored block hash: 0x0."

Reason: the gateway reads from state at block 1000; block 991's hash
is written into the block-hash contract only when block 1001 is
processed (pre_process_block writes hash for 1001 − 10 = 991).

4. Confirm the batcher would accept it by submitting the same
   transaction after block 1001 is committed — the gateway now
   accepts it because the state at block 1001 contains the hash
   for block 991.
``` [6](#0-5) [7](#0-6) [4](#0-3)

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L289-327)
```rust
        // Build block context.
        let mut versioned_constants = VersionedConstants::get_versioned_constants(
            self.config.versioned_constants_overrides.clone(),
        );
        // The validation of a transaction is not affected by the casm hash migration.
        versioned_constants.disable_casm_hash_migration();

        let mut block_info = self.gateway_fixed_block_state_reader.get_block_info().await?;
        block_info.block_number = block_info.block_number.unchecked_next();
        let block_context = BlockContext::new(
            block_info,
            self.chain_info.clone(),
            versioned_constants,
            BouncerConfig::max(),
        );

        // Move state into the blocking task and run CPU-heavy validation.
        let state_reader_and_contract_manager = self.take_state_reader_and_contract_manager();

        let cur_span = Span::current();
        tokio::task::spawn_blocking(move || {
            cur_span.in_scope(|| {
                let state = CachedState::new(state_reader_and_contract_manager);
                let mut blockifier_validator = StatefulValidator::create(state, block_context);
                blockifier_validator.validate(account_tx)
            })
        })
        .await
        .map_err(|e| StarknetError {
            code: StarknetErrorCode::UnknownErrorCode(
                "StarknetErrorCode.InternalError".to_string(),
            ),
            message: format!("Blocking task join error when running the validate entry point: {e}"),
        })?
        .map_err(|e| StarknetError {
            code: StarknetErrorCode::KnownErrorCode(KnownStarknetErrorCode::ValidateFailure),
            message: e.to_string(),
        })?;
        Ok(())
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L238-257)
```rust
    fn validate_proof_block_number(
        proof_block_number: u64,
        current_block_number: BlockNumber,
    ) -> TransactionPreValidationResult<()> {
        // Proof block must be old enough to have a stored block hash.
        // Stored block hashes are guaranteed only up to: current - STORED_BLOCK_HASH_BUFFER.
        let max_allowed =
            current_block_number.0.checked_sub(STORED_BLOCK_HASH_BUFFER).ok_or_else(|| {
                TransactionPreValidationError::InvalidProofFacts(format!(
                    "The current block number {current_block_number} is below the required \
                     block-hash retention buffer: {STORED_BLOCK_HASH_BUFFER}."
                ))
            })?;

        if proof_block_number > max_allowed {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "The proof block number {proof_block_number} is too recent. The maximum allowed \
                 block number is {max_allowed}."
            )));
        }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L278-285)
```rust
        let stored_block_hash = state
            .get_storage_at(block_hash_contract_address, StorageKey::from(proof_block_number))?;

        if stored_block_hash != proof_block_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Block hash mismatch for block {proof_block_number}. Proof block hash: \
                 {proof_block_hash}, stored block hash: {stored_block_hash}."
            )));
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L291-330)
```rust
    fn validate_proof_facts(
        &self,
        block_context: &BlockContext,
        state: &mut dyn State,
    ) -> TransactionPreValidationResult<()> {
        // Only Invoke V3 transactions can carry proof facts.
        let Transaction::Invoke(invoke_tx) = &self.tx else {
            return Ok(());
        };
        if invoke_tx.version() < TransactionVersion::THREE {
            return Ok(());
        }

        // Parse proof facts.
        let proof_facts = invoke_tx.proof_facts();
        let snos_proof_facts = match ProofFactsVariant::try_from(&proof_facts)
            .map_err(|e| TransactionPreValidationError::InvalidProofFacts(e.to_string()))?
        {
            ProofFactsVariant::Empty => return Ok(()),
            ProofFactsVariant::Snos(snos_proof_facts) => snos_proof_facts,
        };
        let os_constants = &block_context.versioned_constants.os_constants;

        // Validate the program hash.
        let allowed = &os_constants.allowed_virtual_os_program_hashes;
        if !allowed.contains(&snos_proof_facts.program_hash) {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS program hash {} is not allowed",
                snos_proof_facts.program_hash
            )));
        }

        // Validate the block hash and block number.
        let proof_block_hash = snos_proof_facts.block_hash.0;
        let proof_block_number = snos_proof_facts.block_number.0;
        Self::validate_proof_block_number(
            proof_block_number,
            block_context.block_info.block_number,
        )?;
        Self::validate_proof_block_hash(proof_block_hash, proof_block_number, os_constants, state)?;
```

**File:** crates/blockifier/src/blockifier/block.rs (L18-35)
```rust
pub fn pre_process_block(
    state: &mut dyn State,
    old_block_number_and_hash: Option<BlockHashAndNumber>,
    next_block_number: BlockNumber,
    os_constants: &OsConstants,
) -> StateResult<()> {
    let should_block_hash_be_provided =
        next_block_number >= BlockNumber(constants::STORED_BLOCK_HASH_BUFFER);
    if let Some(BlockHashAndNumber { number, hash }) = old_block_number_and_hash {
        let block_hash_contract_address =
            os_constants.os_contract_addresses.block_hash_contract_address();
        let block_number_as_storage_key = StorageKey::from(number.0);
        state.set_storage_at(block_hash_contract_address, block_number_as_storage_key, hash.0)?;
    } else if should_block_hash_be_provided {
        return Err(StateError::OldBlockHashNotProvided);
    }

    Ok(())
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L352-352)
```rust
    let retrospective_block_number = init.height.0.checked_sub(STORED_BLOCK_HASH_BUFFER);
```
