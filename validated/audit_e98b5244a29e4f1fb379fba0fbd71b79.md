### Title
Hardcoded `Felt::ZERO` for L2 Gas in Receipt Commitment Produces Wrong Block Hash for v0.14.x Blocks - (`File: crates/starknet_api/src/block_hash/receipt_commitment.rs`)

### Summary

`chain_gas_consumed` in `receipt_commitment.rs` hardcodes `Felt::ZERO` for the L2 gas consumed field of every receipt hash, ignoring the actual `gas_consumed.l2_gas` value. For Starknet v0.14.x blocks where transactions consume non-zero L2 gas, this produces a wrong `receipt_commitment`, which propagates into a wrong `block_hash` and a wrong block signature.

### Finding Description

The receipt hash for each transaction is computed in `calculate_receipt_hash`:

```rust
// crates/starknet_api/src/block_hash/receipt_commitment.rs
fn chain_gas_consumed(hash_chain: HashChain, gas_consumed: &GasVector) -> HashChain {
    hash_chain
        .chain(&Felt::ZERO) // L2 gas consumed  <-- hardcoded zero
        .chain(&gas_consumed.l1_gas.into())
        .chain(&gas_consumed.l1_data_gas.into())
}
``` [1](#0-0) 

The `GasVector` struct has three fields — `l1_gas`, `l1_data_gas`, and `l2_gas` — all of which are populated from actual execution: [2](#0-1) 

The `gas_consumed` field in `TransactionOutputForHash` carries the real execution gas vector: [3](#0-2) 

For Starknet v0.14.x, transactions consume significant L2 gas. The test fixture `block_post_0_14_2.json` shows transactions with `"l2_gas": 76014735` in `total_gas_consumed` and a block-level `"l2_gas_consumed": 988191555`. The comment "In the current RPC: always 0" is stale — it was true for pre-v0.14 blocks but not for current production blocks.

The wrong receipt hash propagates upward through the full commitment chain:

1. `calculate_receipt_commitment` builds a Patricia root over wrong per-receipt hashes.
2. The wrong `ReceiptCommitment` is stored in `BlockHeaderCommitments`.
3. `calculate_block_commitments` (called from `BlockExecutionArtifacts::new`) embeds this wrong commitment.
4. `calculate_block_hash` chains the wrong `receipt_commitment` into the final block hash.
5. The block signature covers `block_hash` and `state_diff_commitment`, so the signature is over a wrong hash. [4](#0-3) [5](#0-4) 

### Impact Explanation

Any node that independently recomputes the receipt commitment from the actual receipts (e.g., a verifier, a syncing peer, or the SNOS prover) will derive a different value than what the sequencer stored. This produces:

- A wrong `receipt_commitment` in the stored block header.
- A wrong `block_hash` returned by RPC (`starknet_getBlockWithTxHashes`, etc.).
- A wrong block signature, since `verify_block_signature` hashes `block_hash` with `state_diff_commitment`.
- Wrong proof inputs/outputs for any prover that verifies the receipt commitment.

This matches: **Critical — Wrong receipt/state value from execution logic for accepted input**, and **High — RPC returns an authoritative-looking wrong value**.

### Likelihood Explanation

This is triggered by any v0.14.x block containing at least one transaction with non-zero L2 gas consumption, which is the normal case for all current Starknet production blocks. No special attacker action is required; the sequencer itself produces the wrong commitment during normal block building.

### Recommendation

Replace the hardcoded `Felt::ZERO` with the actual `gas_consumed.l2_gas` value:

```rust
fn chain_gas_consumed(hash_chain: HashChain, gas_consumed: &GasVector) -> HashChain {
    hash_chain
        .chain(&gas_consumed.l2_gas.into()) // actual L2 gas consumed
        .chain(&gas_consumed.l1_gas.into())
        .chain(&gas_consumed.l1_data_gas.into())
}
``` [1](#0-0) 

Update the regression test in `receipt_commitment_test.rs` to use a non-zero `l2_gas` value and verify the expected hash changes accordingly.

### Proof of Concept

1. Execute any v0.14.x Invoke V3 transaction that consumes L2 gas (e.g., any transaction in `block_post_0_14_2.json` where `total_gas_consumed.l2_gas = 76014735`).
2. The sequencer calls `BlockExecutionArtifacts::new`, which calls `calculate_block_commitments` with the real `gas_consumed` vector.
3. `chain_gas_consumed` chains `Felt::ZERO` instead of `Felt::from(76014735)` for the L2 gas field.
4. The resulting `receipt_commitment` differs from what any independent verifier computes using the actual L2 gas value.
5. `calculate_block_hash` embeds this wrong `receipt_commitment`, producing a wrong `block_hash`.
6. `verify_block_signature` called by a syncing peer will verify a signature over the wrong hash, causing a mismatch with any correctly-computed hash. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/starknet_api/src/block_hash/receipt_commitment.rs (L45-53)
```rust
fn calculate_receipt_hash(receipt_element: &ReceiptElement) -> Felt {
    let hash_chain = HashChain::new()
        .chain(&receipt_element.transaction_hash)
        .chain(&receipt_element.transaction_output.actual_fee.0.into())
        .chain(&calculate_messages_sent_hash(&receipt_element.transaction_output.messages_sent))
        .chain(&get_revert_reason_hash(&receipt_element.transaction_output.execution_status));
    chain_gas_consumed(hash_chain, &receipt_element.transaction_output.gas_consumed)
        .get_poseidon_hash()
}
```

**File:** crates/starknet_api/src/block_hash/receipt_commitment.rs (L86-91)
```rust
fn chain_gas_consumed(hash_chain: HashChain, gas_consumed: &GasVector) -> HashChain {
    hash_chain
        .chain(&Felt::ZERO) // L2 gas consumed
        .chain(&gas_consumed.l1_gas.into())
        .chain(&gas_consumed.l1_data_gas.into())
}
```

**File:** crates/starknet_api/src/execution_resources.rs (L106-111)
```rust
pub struct GasVector {
    pub l1_gas: GasAmount,
    pub l1_data_gas: GasAmount,
    #[serde(default)]
    pub l2_gas: GasAmount,
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L99-105)
```rust
pub struct TransactionOutputForHash {
    pub actual_fee: Fee,
    pub events: Vec<Event>,
    pub execution_status: TransactionExecutionStatus,
    pub gas_consumed: GasVector,
    pub messages_sent: Vec<MessageToL1>,
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-265)
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
```

**File:** crates/apollo_batcher/src/block_builder.rs (L160-169)
```rust
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
        record_and_log_block_commitment_measurements(block_info.block_number, measurements);
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
```

**File:** crates/starknet_api/src/block.rs (L716-730)
```rust
/// Verifies that the the block header was signed by the expected sequencer.
pub fn verify_block_signature(
    sequencer_pub_key: &SequencerPublicKey,
    signature: &BlockSignature,
    state_diff_commitment: &GlobalRoot,
    block_hash: &BlockHash,
) -> Result<bool, BlockVerificationError> {
    let message_hash = Poseidon::hash_array(&[block_hash.0, state_diff_commitment.0]);
    verify_message_hash_signature(&message_hash, &signature.0, &sequencer_pub_key.0).map_err(
        |err| BlockVerificationError::BlockSignatureVerificationFailed {
            block_hash: *block_hash,
            error: err,
        },
    )
}
```
