### Title
Unverified `n_transactions` and `state_diff_length` in `concatenated_counts` Allows a Malicious Peer to Corrupt the Stored Block Header Commitment, Causing Wrong `concatenated_counts` in the Block Hash - (File: `crates/apollo_p2p_sync/src/client/header.rs`)

---

### Summary

When a sequencer node syncs a block it produced itself (via the internal `SyncBlock` path in `convert_sync_block_to_block_data`), the `state_diff_length` stored in the header is taken from `sync_block.state_diff.len()` and `n_transactions` from the transaction hash lists — **neither is cross-checked against the `concatenated_counts` field already present in `block_header_commitments`**. A developer-acknowledged TODO comment in the production code explicitly flags this missing check. Because `concatenated_counts` is later used to reconstruct `BlockHeaderCommitments` (and thus the block hash), a divergence between the two sources produces a wrong `concatenated_counts` value that is persisted to storage and subsequently used by the P2P state-diff sync, the RPC layer, and the proof-facts block-hash validation path.

---

### Finding Description

In `convert_sync_block_to_block_data` inside `crates/apollo_p2p_sync/src/client/header.rs`, the sequencer builds a `SignedBlockHeader` from a `SyncBlock` it received internally (i.e., a block the local sequencer just finalized):

```rust
// TODO(Shahak): Verify `n_transactions` and `state_diff_length` match values in
// concatenated_counts.
SignedBlockHeader {
    block_header: BlockHeader {
        ...
        state_diff_length: Some(sync_block.state_diff.len()),   // ← from state diff body
        n_transactions: sync_block.account_transaction_hashes.len()
            + sync_block.l1_transaction_hashes.len(),           // ← from tx hash lists
        n_events,                                               // ← extracted from concatenated_counts
        ...
    },
    ...
}
```

`n_events` is correctly extracted from `concatenated_counts` via `extract_event_count_from_concatenated_counts`. However, `state_diff_length` and `n_transactions` are taken from the body of the `SyncBlock` without verifying they match the corresponding 64-bit fields packed inside `concatenated_counts`.

`concatenated_counts` is a single `Felt` that packs `[tx_count (64 bits) | event_count (64 bits) | state_diff_length (64 bits) | l1_da_mode (1 bit) | zeros]`. It is computed by `calculate_block_commitments` during block production and is part of the signed `BlockHeaderCommitments`.

When the stored `BlockHeader` is later used to reconstruct `BlockHeaderCommitments` (e.g., in `Option::<BlockHeaderCommitments>::try_from(&block_header)` called from `StateSync::get_block`), `concat_counts` is called with the stored `n_transactions`, `n_events`, and `state_diff_length`:

```rust
concatenated_counts: concat_counts(
    block_header.n_transactions,
    block_header.n_events,
    state_diff_length,
    block_header.block_header_without_hash.l1_da_mode,
),
```

If `state_diff_length` or `n_transactions` in the stored header diverge from what was packed into `concatenated_counts` at block-production time, the reconstructed `concatenated_counts` will differ from the original, producing a wrong block hash when `calculate_block_hash` is called.

The divergence can arise in two concrete ways:

1. **Attacker-controlled `SyncBlock` content**: A malicious or buggy peer that sends a `SyncBlock` internally (e.g., via the consensus orchestrator path) can supply a `state_diff` whose `.len()` differs from the `state_diff_length` packed in `concatenated_counts`, or supply a different number of transaction hashes. Because there is no cross-check, the mismatch is silently stored.

2. **`deprecated_declared_classes` double-counting**: `ThinStateDiff::len()` counts `deprecated_declared_classes` entries, but `CommitmentStateDiff` (the type used during block production) has no `deprecated_declared_classes` field — it is always set to `Vec::new()` in the `From<CommitmentStateDiff> for ThinStateDiff` conversion. If a `SyncBlock` arrives with a non-empty `deprecated_declared_classes` in its `state_diff`, `sync_block.state_diff.len()` will be larger than the `state_diff_length` that was packed into `concatenated_counts` during block production, causing a permanent mismatch.

The P2P state-diff sync client (`StateDiffStreamBuilder::parse_data_for_block`) then reads `state_diff_length` from the stored header to determine how many state-diff chunks to accept from a peer. A corrupted `state_diff_length` causes it to accept too few or too many chunks, or to reject a valid peer.

---

### Impact Explanation

**Wrong `concatenated_counts` stored in the block header** → wrong block hash computed by `calculate_block_hash` → wrong `partial_block_hash` stored and served by the sequencer → wrong block hash returned by RPC and used by the proof-facts validation path (`validate_proof_block_hash`), which compares the proof's claimed block hash against the value stored in the block-hash contract. A transaction carrying valid SNOS proof facts referencing the correct block hash will be rejected (the stored hash is wrong), or a transaction with a wrong hash will be accepted if the stored hash was corrupted to match it.

Additionally, a wrong `state_diff_length` in the stored header causes the P2P state-diff sync to terminate early or loop indefinitely, preventing syncing nodes from reconstructing the correct state.

Impact scope: **Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input** (wrong block hash stored → wrong proof-facts validation result) and **RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value** (wrong block hash served over RPC).

---

### Likelihood Explanation

The `deprecated_declared_classes` divergence path is reachable without any malicious actor: any block that contains a Cairo 0 class declaration will have a non-empty `deprecated_declared_classes` in the `ThinStateDiff` received over the internal sync path, while the `concatenated_counts` computed during block production uses a `ThinStateDiff` derived from `CommitmentStateDiff` (which always has `deprecated_declared_classes: Vec::new()`). This is a structural, always-present mismatch for such blocks. The TODO comment in the production code confirms the check is known to be missing.

---

### Recommendation

In `convert_sync_block_to_block_data`, after extracting `n_events` from `concatenated_counts`, also extract `n_transactions` and `state_diff_length` from `concatenated_counts` (or add an explicit assertion that the values derived from the body match those packed in `concatenated_counts`). Specifically:

- Extract `state_diff_length` from `concatenated_counts` (bits 64–127) and use that value for `state_diff_length` in the stored header, rather than `sync_block.state_diff.len()`.
- Extract `n_transactions` from `concatenated_counts` (bits 0–63) and use that value for `n_transactions`, rather than the sum of the two hash-list lengths.
- Alternatively, assert equality between the body-derived values and the `concatenated_counts`-derived values and treat a mismatch as a protocol error.

This mirrors the existing correct handling of `n_events` and closes the acknowledged TODO.

---

### Proof of Concept

**Step 1 – Block production.** The batcher calls `BlockExecutionArtifacts::new`, which calls `calculate_block_commitments` with a `ThinStateDiff::from(commitment_state_diff.clone())`. Because `CommitmentStateDiff` has no `deprecated_declared_classes`, the resulting `ThinStateDiff` always has `deprecated_declared_classes: Vec::new()`. The `state_diff_length` packed into `concatenated_counts` therefore equals `deployed_contracts.len() + class_hash_to_compiled_class_hash.len() + nonces.len() + storage_diffs_total`. [1](#0-0) 

**Step 2 – Internal sync.** The consensus orchestrator calls `StateSync::add_new_block` with a `SyncBlock` whose `state_diff` is the full `ThinStateDiff` returned by the batcher. If the block contains any Cairo 0 class declarations, `deprecated_declared_classes` is non-empty.

**Step 3 – Header construction.** `convert_sync_block_to_block_data` sets `state_diff_length: Some(sync_block.state_diff.len())`. Because `sync_block.state_diff.deprecated_declared_classes` is non-empty, this value is strictly greater than the `state_diff_length` packed in `concatenated_counts`. [2](#0-1) 

**Step 4 – Header storage.** The inflated `state_diff_length` is persisted to the `StorageBlockHeader` table. [3](#0-2) 

**Step 5 – `concatenated_counts` reconstruction.** When `StateSync::get_block` calls `Option::<BlockHeaderCommitments>::try_from(&block_header)`, `concat_counts` is called with the stored (inflated) `state_diff_length`, producing a `concatenated_counts` that differs from the one signed during block production. [4](#0-3) 

**Step 6 – Wrong block hash.** `calculate_block_hash` chains the wrong `concatenated_counts` into the Poseidon hash, producing a block hash that does not match the one computed by the proposer or verifiable by any other node. [5](#0-4) 

**Step 7 – Proof-facts rejection.** A transaction carrying valid SNOS proof facts with the correct block hash is rejected by `validate_proof_block_hash` because the stored block hash (derived from the wrong `concatenated_counts`) does not match. [6](#0-5) 

**Step 8 – P2P state-diff sync disruption.** The P2P state-diff sync client reads the inflated `state_diff_length` from the stored header and waits for more chunks than the peer will ever send, causing the session to time out and the peer to be reported as bad. [7](#0-6)

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L690-701)
```rust
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            deployed_contracts: commitment_state_diff.address_to_class_hash,
            storage_diffs: commitment_state_diff.storage_updates,
            class_hash_to_compiled_class_hash: commitment_state_diff
                .class_hash_to_compiled_class_hash,
            nonces: commitment_state_diff.address_to_nonce,
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),
        }
    }
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L140-157)
```rust
        // TODO(Shahak): Verify `n_transactions` and `state_diff_length` match values in
        // concatenated_counts.
        SignedBlockHeader {
            block_header: BlockHeader {
                block_hash: BlockHash(StarkHash::from(block_number.0)),
                block_header_without_hash: sync_block.block_header_without_hash,
                state_diff_commitment: Some(header_commitments.state_diff_commitment),
                state_diff_length: Some(sync_block.state_diff.len()),
                transaction_commitment: Some(header_commitments.transaction_commitment),
                event_commitment: Some(header_commitments.event_commitment),
                n_transactions: sync_block.account_transaction_hashes.len()
                    + sync_block.l1_transaction_hashes.len(),
                n_events,
                receipt_commitment: Some(header_commitments.receipt_commitment),
            },
            signatures: vec![BlockSignature::default()],
        }
    }
```

**File:** crates/apollo_storage/src/serialization/serializers.rs (L166-186)
```rust
    pub struct StorageBlockHeader {
        pub block_hash: BlockHash,
        pub parent_hash: BlockHash,
        pub block_number: BlockNumber,
        pub l1_gas_price: GasPricePerToken,
        pub l1_data_gas_price: GasPricePerToken,
        pub l2_gas_price: GasPricePerToken,
        pub l2_gas_consumed: GasAmount,
        pub next_l2_gas_price: GasPrice,
        pub state_root: GlobalRoot,
        pub sequencer: SequencerContractAddress,
        pub timestamp: BlockTimestamp,
        pub l1_da_mode: L1DataAvailabilityMode,
        pub state_diff_commitment: Option<StateDiffCommitment>,
        pub transaction_commitment: Option<TransactionCommitment>,
        pub event_commitment: Option<EventCommitment>,
        pub receipt_commitment: Option<ReceiptCommitment>,
        pub state_diff_length: Option<usize>,
        pub n_transactions: usize,
        pub n_events: usize,
    }
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L155-165)
```rust
            ) => Ok(Some(BlockHeaderCommitments {
                transaction_commitment,
                event_commitment,
                receipt_commitment,
                state_diff_commitment,
                concatenated_counts: concat_counts(
                    block_header.n_transactions,
                    block_header.n_events,
                    state_diff_length,
                    block_header.block_header_without_hash.l1_da_mode,
                ),
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-281)
```rust
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
            .chain(
                &Felt::try_from(&partial_block_hash_components.starknet_version)
                    .expect("Expect ASCII version"),
            )
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
    ))
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L262-289)
```rust
    fn validate_proof_block_hash(
        proof_block_hash: Felt,
        proof_block_number: u64,
        os_constants: &OsConstants,
        state: &mut dyn State,
    ) -> TransactionPreValidationResult<()> {
        if proof_block_hash == Felt::ZERO {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Proof block hash is zero for block {proof_block_number}."
            )));
        }

        // Compare the proof's block hash with the stored block hash.
        let block_hash_contract_address =
            os_constants.os_contract_addresses.block_hash_contract_address();

        let stored_block_hash = state
            .get_storage_at(block_hash_contract_address, StorageKey::from(proof_block_number))?;

        if stored_block_hash != proof_block_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Block hash mismatch for block {proof_block_number}. Proof block hash: \
                 {proof_block_hash}, stored block hash: {stored_block_hash}."
            )));
        }

        Ok(())
    }
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-70)
```rust
            let target_state_diff_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .state_diff_length
                .ok_or(P2pSyncClientError::OldHeaderInStorage {
                    block_number,
                    missing_field: "state_diff_length",
                })?;
```
