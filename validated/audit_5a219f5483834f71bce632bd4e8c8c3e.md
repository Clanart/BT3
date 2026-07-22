### Title
Gateway `validate_proof_facts` uses advanced block number N+1 against state from block N, causing systematic false rejection of valid proof-carrying transactions — (File: `crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

`run_validate_entry_point` in the gateway builds a `BlockContext` with `block_number = N+1` (the simulated next block) but supplies state read from the last committed block N. When `validate_proof_facts` is called inside that context, the recency check uses the N+1 block number while the block-hash lookup reads from the N-state. This creates a one-block window where a transaction whose `proof_block_number` equals `N − STORED_BLOCK_HASH_BUFFER + 1` passes the recency check but fails the hash lookup with "Block hash mismatch", even though the same transaction would be accepted without error during actual execution in block N+1.

---

### Finding Description

**Root cause — context mismatch between block number and state.**

In `run_validate_entry_point` the gateway deliberately advances the block number by one to simulate the pending block:

```rust
// crates/apollo_gateway/src/stateful_transaction_validator.rs  L323-L330
let mut block_info = self.gateway_fixed_block_state_reader.get_block_info().await?;
block_info.block_number = block_info.block_number.unchecked_next();   // N → N+1
let block_context = BlockContext::new(block_info, ...);

let state_reader_and_contract_manager = self.take_state_reader_and_contract_manager();
// state is still anchored at block N
let state = CachedState::new(state_reader_and_contract_manager);
let mut blockifier_validator = StatefulValidator::create(state, block_context);
blockifier_validator.validate(account_tx)
``` [1](#0-0) 

`StatefulValidator::validate` calls `perform_pre_validation_stage`, which calls `validate_proof_facts`. Inside that function two independent checks are performed:

1. **Recency check** — uses `block_context.block_info.block_number` (= N+1):

```rust
// crates/blockifier/src/transaction/account_transaction.rs  L334-L337
Self::validate_proof_block_number(
    proof_block_number,
    block_context.block_info.block_number,   // N+1
)?;
``` [2](#0-1) 

`validate_proof_block_number` computes `max_allowed = (N+1) − STORED_BLOCK_HASH_BUFFER`, so it permits `proof_block_number` up to `N − STORED_BLOCK_HASH_BUFFER + 1`. [3](#0-2) 

2. **Hash lookup** — reads from the state anchored at block N:

```rust
// crates/blockifier/src/transaction/account_transaction.rs  L278-L286
let stored_block_hash = state
    .get_storage_at(block_hash_contract_address, StorageKey::from(proof_block_number))?;

if stored_block_hash != proof_block_hash {
    return Err(TransactionPreValidationError::InvalidProofFacts(format!(
        "Block hash mismatch ..."
    )));
}
``` [4](#0-3) 

**The invariant that is broken.** Block hashes are written into the block-hash contract during block execution: when block N is executed it stores `hash(N − STORED_BLOCK_HASH_BUFFER)`. Therefore the state at block N contains hashes for blocks `0 … N − STORED_BLOCK_HASH_BUFFER`. The hash for block `N − STORED_BLOCK_HASH_BUFFER + 1` is only written when block N+1 is executed.

**Consequence for `proof_block_number = N − STORED_BLOCK_HASH_BUFFER + 1`:**

| Step | Block number used | State used | Result |
|---|---|---|---|
| Gateway recency check | N+1 | — | ✓ passes (`≤ N − STORED_BLOCK_HASH_BUFFER + 1`) |
| Gateway hash lookup | — | block N | ✗ fails (`stored = 0 ≠ proof_block_hash`) |
| Actual execution in block N+1 | N+1 | block N+1 | ✓ passes (hash now stored) |

The gateway emits `InvalidProofFacts("Block hash mismatch …")` and the transaction is dropped. The same transaction would be accepted by the blockifier when block N+1 is actually built.

The Cairo OS `check_proof_facts` in `execution_constraints.cairo` performs the same two checks in the same order and would accept the transaction:

```cairo
// crates/apollo_starknet_os_program/.../execution_constraints.cairo  L68-L78
assert_nn_le(
    os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
);
read_block_hash_from_storage(
    block_number=os_output_header.base_block_number,
    expected_block_hash=os_output_header.base_block_hash,
);
``` [5](#0-4) 

The OS runs against the real N+1 state, so the hash is present. The gateway runs against the N state, so it is absent.

---

### Impact Explanation

**High — gateway rejects valid transactions before sequencing.**

Any user who generates a SNOS proof anchored to block `N − STORED_BLOCK_HASH_BUFFER + 1` (the most recent block whose hash is permitted by the N+1 recency window) will have their invoke transaction rejected at the gateway with `InvalidProofFacts`. Because the proof is cryptographically bound to that specific block hash, the user cannot reuse it; they must generate an entirely new proof for an older block. Proof generation is computationally expensive. The false rejection is deterministic and repeatable: every submission of such a transaction against the same committed tip N will fail. This constitutes a systematic denial-of-service against the client-side proving (SNIP-36) feature for the one-block edge case.

---

### Likelihood Explanation

**High.** The condition is deterministic: it fires whenever a user constructs a proof for the most recent block that the N+1 recency window admits. Clients that always anchor to the freshest allowed block (a natural optimisation to minimise proof staleness) will hit this on every submission attempt. No special privileges or network position are required; a normal RPC `add_invoke_transaction` call is sufficient.

---

### Recommendation

Align the block number used for the recency check with the block whose state is actually available. The simplest fix is to use the last committed block number N (not N+1) when calling `validate_proof_facts` inside the gateway, so that `max_allowed = N − STORED_BLOCK_HASH_BUFFER` and the hash for that block is guaranteed to be present in the state. Alternatively, if the N+1 simulation is required for other validation purposes, the proof-facts check should be performed against the N-anchored block number only, or the gateway should treat a zero stored hash as "not yet available" and accept the transaction rather than rejecting it.

---

### Proof of Concept

Let `N` = last committed block number, `STORED_BLOCK_HASH_BUFFER` = 10 (the constant used in `validate_proof_block_number`).

1. Generate a SNOS proof whose `base_block_number` = `N − 9` and `base_block_hash` = the real hash of block `N − 9`.
2. Submit an `invoke_v3` transaction carrying those `proof_facts` to the gateway RPC endpoint.
3. The gateway calls `run_validate_entry_point` → `validate_proof_facts`:
   - Recency check: `N − 9 ≤ (N+1) − 10 = N − 9` → **passes**.
   - Hash lookup in state@N: `get_storage_at(block_hash_contract, N−9)` returns `Felt::ZERO` (not yet stored) → `0 ≠ real_hash` → **fails** with `InvalidProofFacts("Block hash mismatch …")`.
4. Gateway returns `ValidateFailure`; transaction is not admitted to the mempool.
5. Wait for block N+1 to be committed (state@N+1 now contains `hash(N−9)`).
6. Resubmit the **identical** transaction. The gateway now uses block number N+2 and state@N+1; the hash is present → transaction is accepted.

The transaction was valid throughout; only the gateway's context mismatch caused the rejection in step 4.

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L323-341)
```rust
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
        #[allow(clippy::result_large_err)]
        tokio::task::spawn_blocking(move || {
            cur_span.in_scope(|| {
                let state = CachedState::new(state_reader_and_contract_manager);
                let mut blockifier_validator = StatefulValidator::create(state, block_context);
                blockifier_validator.validate(account_tx)
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L238-260)
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

        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L278-286)
```rust
        let stored_block_hash = state
            .get_storage_at(block_hash_contract_address, StorageKey::from(proof_block_number))?;

        if stored_block_hash != proof_block_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Block hash mismatch for block {proof_block_number}. Proof block hash: \
                 {proof_block_hash}, stored block hash: {stored_block_hash}."
            )));
        }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L334-337)
```rust
        Self::validate_proof_block_number(
            proof_block_number,
            block_context.block_info.block_number,
        )?;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L68-78)
```text
    assert_nn_le(
        os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
    );
    // Not all block hashes are stored in the contract; Make sure the requested one is not trivial.
    assert_not_zero(os_output_header.base_block_hash);

    // validate that the proof facts block hash is the true hash of the proof facts block number.
    read_block_hash_from_storage(
        block_number=os_output_header.base_block_number,
        expected_block_hash=os_output_header.base_block_hash,
    );
```
