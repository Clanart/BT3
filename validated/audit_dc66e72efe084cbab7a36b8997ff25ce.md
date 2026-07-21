The code path is clear and the analysis is straightforward. Let me confirm there are no downstream guards I might have missed.

The grep confirms `receipt_commitment` is referenced in `crates/apollo_p2p_sync/src/client/` only in `header.rs` (where it is stored) and `test.rs` (where tests assert it was stored correctly). It is **never referenced in `transaction.rs`** — there is no guard anywhere in the transaction sync path.

---

### Title
Missing `receipt_commitment` Validation in P2P Transaction Sync Allows Malicious Peer to Corrupt Stored `TransactionOutput` Fields — (`crates/apollo_p2p_sync/src/client/transaction.rs`)

### Summary
`TransactionStreamFactory::parse_data_for_block` accepts `transaction_output` data from an untrusted P2P peer and writes it directly to storage without verifying it against the `receipt_commitment` already stored in the block header. A malicious peer can supply arbitrary `actual_fee`, `execution_resources`, and `execution_status` values, causing the node to persist and serve receipt data that diverges from the Poseidon root committed in the header.

### Finding Description

The header sync path stores the full `BlockHeader` including `receipt_commitment` (a Poseidon root over per-transaction `actual_fee`, `execution_resources`, `revert_reason`, and `l2_to_l1_messages`): [1](#0-0) 

The subsequent transaction sync path in `parse_data_for_block` reads only `n_transactions` from the stored header to know how many items to collect: [2](#0-1) 

Each `FullTransaction` received from the peer is destructured and its `transaction_output` is pushed directly into `block_body` with no commitment check: [3](#0-2) 

`write_to_storage` then commits the entire `BlockBody` — including the unvalidated outputs — to disk: [4](#0-3) 

Inside `write_transactions`, each output is serialized and appended to the file store with no cross-check against the header's `receipt_commitment`: [5](#0-4) 

The `receipt_commitment` binding is defined as a Poseidon root over per-receipt leaves, each covering `actual_fee`, `messages_sent`, `execution_status`, and `gas_consumed`: [6](#0-5) 

There is no call to `calculate_receipt_commitment` anywhere in the P2P transaction sync path, and no `BadPeerError` variant for a commitment mismatch exists in `block_data_stream_builder.rs`. [7](#0-6) 

### Impact Explanation
Any node syncing transaction bodies via P2P will store and subsequently serve attacker-chosen `actual_fee`, `execution_resources`, and `execution_status` values. The stored `TransactionOutput` diverges from the `receipt_commitment` in the header. RPC calls such as `starknet_getTransactionReceipt` read directly from this storage and return the corrupted values as authoritative. Any downstream logic that reads fee or gas data from storage (fee accounting, gas refund calculations, tracing) will operate on the wrong values. This matches the allowed impact: **High — RPC returns an authoritative-looking wrong value**, and potentially **Critical — incorrect fee/gas accounting with economic impact**.

### Likelihood Explanation
Any P2P peer the syncing node connects to can trigger this. No special privileges are required. The peer is not disconnected or penalized for sending tampered `transaction_output` fields because `parse_data_for_block` returns `Ok(Some(...))` for any structurally valid `FullTransaction` regardless of whether its outputs match the committed root.

### Recommendation
After collecting all `transaction_output` values for a block in `parse_data_for_block`, compute `calculate_receipt_commitment::<Poseidon>` over the collected outputs (using `TransactionHashingData` / `ReceiptElement`) and compare the result against `header.receipt_commitment`. If they diverge, return `Err(ParseDataError::BadPeer(...))` so the peer is reported and the query is retried from a different peer. The validation point should be before `Ok(Some((block_body, block_number)))` is returned.

### Proof of Concept

```
1. Syncing node has stored a BlockHeader for block N with a known receipt_commitment R
   (Poseidon root over the real transaction outputs).

2. Malicious peer responds to the transaction query for block N with FullTransaction objects
   where transaction_output.actual_fee is set to an attacker-chosen value (e.g., u128::MAX).

3. parse_data_for_block collects exactly n_transactions items (count check passes),
   pushes the tampered transaction_output into block_body, and returns Ok(Some(...)).

4. write_to_storage calls append_body, which calls write_transactions, which calls
   file_handlers.append_transaction_output(tx_output) — persisting the tampered output.

5. A subsequent call to get_block_transaction_outputs(N) returns the tampered outputs.

6. Recalculating calculate_receipt_commitment::<Poseidon> over the stored outputs
   produces R' ≠ R (the header's receipt_commitment), proving the invariant is broken.

7. starknet_getTransactionReceipt for any transaction in block N returns the attacker's
   actual_fee value.
```

### Citations

**File:** crates/apollo_p2p_sync/src/client/header.rs (L153-153)
```rust
                receipt_commitment: Some(header_commitments.receipt_commitment),
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L36-36)
```rust
            storage_writer.begin_rw_txn()?.append_body(self.1, self.0)?.commit()?;
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L62-66)
```rust
            let target_transaction_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .n_transactions;
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L73-90)
```rust
                let Some(FullTransaction { transaction, transaction_output, transaction_hash }) =
                    maybe_transaction?.0
                else {
                    if current_transaction_len == 0 {
                        return Ok(None);
                    } else {
                        return Err(ParseDataError::BadPeer(BadPeerError::NotEnoughTransactions {
                            expected: target_transaction_len,
                            actual: current_transaction_len,
                            block_number: block_number.0,
                        }));
                    }
                };
                block_body.transactions.push(transaction);
                block_body.transaction_outputs.push(transaction_output);
                // TODO(eitan): Validate transaction hash from untrusted sources
                block_body.transaction_hashes.push(transaction_hash);
                current_transaction_len += 1;
```

**File:** crates/apollo_storage/src/body/mod.rs (L504-505)
```rust
        let tx_location = file_handlers.append_transaction(tx);
        let tx_output_location = file_handlers.append_transaction_output(tx_output);
```

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

**File:** crates/apollo_p2p_sync/src/client/block_data_stream_builder.rs (L236-274)
```rust
#[derive(thiserror::Error, Debug)]
pub(crate) enum BadPeerError {
    #[error("The sender end of the response receivers for {type_description:?} was closed.")]
    SessionEndedWithoutFin { type_description: &'static str },
    #[error(
        "Blocks returned unordered from the network. Expected header with \
         {expected_block_number}, got {actual_block_number}."
    )]
    HeadersUnordered { expected_block_number: BlockNumber, actual_block_number: BlockNumber },
    #[error(
        "Expected to receive {expected} transactions for {block_number} from the network. Got \
         {actual} instead."
    )]
    NotEnoughTransactions { expected: usize, actual: usize, block_number: u64 },
    #[error("Expected to receive one signature from the network. got {signatures:?} instead.")]
    WrongSignaturesLength { signatures: Vec<BlockSignature> },
    #[error(
        "The header says that the block's state diff should be of length {expected_length}. Can \
         only divide the state diff parts into the following lengths: {possible_lengths:?}."
    )]
    WrongStateDiffLength { expected_length: usize, possible_lengths: Vec<usize> },
    #[error("Two state diff parts for the same state diff are conflicting.")]
    ConflictingStateDiffParts,
    #[error(
        "Received an empty state diff part from the network (this is a potential DDoS vector)."
    )]
    EmptyStateDiffPart,
    #[error(transparent)]
    ProtobufConversionError(#[from] ProtobufConversionError),
    #[error(
        "Expected to receive {expected} classes for {block_number} from the network. Got {actual} \
         classes instead"
    )]
    NotEnoughClasses { expected: usize, actual: usize, block_number: u64 },
    #[error("The class with hash {class_hash} was not found in the state diff.")]
    ClassNotInStateDiff { class_hash: ClassHash },
    #[error("Received two classes with the same hash: {class_hash}.")]
    DuplicateClass { class_hash: ClassHash },
}
```
