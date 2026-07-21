The code confirms this is a real, concrete gap. Here is the complete analysis:

---

### Title
Missing Transaction Hash Validation in P2P Sync Allows Malicious Peer to Corrupt Stored Transaction Hashes — (`crates/apollo_p2p_sync/src/client/transaction.rs`)

### Summary

`TransactionStreamFactory::parse_data_for_block` accepts the `transaction_hash` field from a peer-supplied `FullTransaction` and pushes it directly into `block_body.transaction_hashes` without calling `validate_transaction_hash` or verifying it against the already-stored header's `transaction_commitment` Merkle root. The corrupted hash is then permanently written to storage via `append_body`, indexed in `transaction_hash_to_idx`, and served by RPC as authoritative.

### Finding Description

In `parse_data_for_block`, the code explicitly acknowledges the missing guard with a TODO:

```rust
// TODO(eitan): Validate transaction hash from untrusted sources
block_body.transaction_hashes.push(transaction_hash);
``` [1](#0-0) 

The function reads `n_transactions` from the stored header to bound the loop count, but performs no hash integrity check: [2](#0-1) 

After `parse_data_for_block` returns `Ok(Some(output))`, `block_data_stream_builder` immediately yields the data for storage with no post-parse commitment verification: [3](#0-2) 

`write_to_storage` calls `append_body`, which calls `write_transactions`. That function stores the attacker-supplied hash in two places with no validation:

1. `transaction_hash_to_idx_table.insert(txn, tx_hash, &transaction_index)` — the fake hash is indexed as the lookup key.
2. `TransactionMetadata { tx_hash: *tx_hash }` — the fake hash is stored as the canonical hash for that index. [4](#0-3) 

The `validate_transaction_hash` function exists and is capable of checking `hash(Transaction, chain_id) == expected_hash`, but is never called in this path: [5](#0-4) 

The block header, already stored before body sync, contains a `transaction_commitment` (Merkle root over real transaction hashes). The body sync reads `n_transactions` from the header but never reads or checks `transaction_commitment`: [6](#0-5) 

There is no post-write reconciliation step anywhere in `P2pSyncClient::run` that would detect the mismatch: [7](#0-6) 

### Impact Explanation

A malicious peer (the only available sync source, or one that wins a race) supplies `FullTransaction { transaction: InvokeV1{...}, transaction_hash: felt!(0xdeadbeef), ... }`. After `write_to_storage`:

- `get_block_transaction_hashes(N)` returns `[0xdeadbeef]` — wrong authoritative value served to RPC and downstream consumers.
- `get_transaction_idx_by_hash(0xdeadbeef)` succeeds; `get_transaction_idx_by_hash(real_hash)` returns `None` — the real hash is not indexed.
- The stored hash diverges from the `transaction_commitment` Merkle root in the header, creating a permanent inconsistency between header commitments and body storage.
- Any downstream system (proof production, state sync re-serving, RPC `starknet_getTransactionByHash`) treats the fake hash as the protocol-authoritative value.

### Likelihood Explanation

Exploitable by any peer that can respond to a transaction query before a legitimate peer. The precondition (peer is the only available source, or node has no prior body for block N) is realistic during initial sync or network partition. The TODO comment confirms the developers are aware this validation is absent.

### Recommendation

In `parse_data_for_block`, after destructuring `FullTransaction`, call `validate_transaction_hash(&transaction, &block_number, &chain_id, transaction_hash, &TransactionOptions::default())` and return `ParseDataError::BadPeer` if it returns `false`. Additionally, after collecting all hashes for a block, recompute `calculate_transaction_commitment` over the collected `(hash, signature)` pairs and compare it against the `transaction_commitment` stored in the block header, rejecting and reporting the peer if they differ.

### Proof of Concept

The proof idea in the question is mechanically sound. Concretely:

1. Store a block header for `BlockNumber(0)` with `n_transactions = 1` and a correct `transaction_commitment` (computed from the real hash).
2. Feed `parse_data_for_block` a `FullTransaction` where `transaction_hash = felt!(0xdeadbeef)` but `transaction` is a valid `InvokeV1`.
3. Call `write_to_storage`.
4. Assert `get_block_transaction_hashes(BlockNumber(0)) == [felt!(0xdeadbeef)]` — this will pass.
5. Assert `validate_transaction_hash(&transaction, &BlockNumber(0), &chain_id, felt!(0xdeadbeef), &Default::default()) == Ok(false)` — this will also pass, confirming the stored hash is protocol-invalid.

The stored value is concrete (`0xdeadbeef`), the path is unprivileged (any p2p peer), and the corrupted hash is permanently served by storage and RPC.

### Citations

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L62-91)
```rust
            let target_transaction_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .n_transactions;
            while current_transaction_len < target_transaction_len {
                let maybe_transaction = transactions_response_manager.next().await.ok_or(
                    ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }),
                )?;
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
            }
```

**File:** crates/apollo_p2p_sync/src/client/block_data_stream_builder.rs (L163-171)
```rust
                        res = Self::parse_data_for_block(
                            &mut client_response_manager, current_block_number, &storage_reader
                        ) => {
                            match res {
                                Ok(Some(output)) => {
                                    info!("Added {:?} for block {}.", Self::TYPE_DESCRIPTION, current_block_number);
                                    current_block_number = current_block_number.unchecked_next();
                                    yield Ok(Box::<dyn BlockData>::from(Box::new(output)));
                                }
```

**File:** crates/apollo_storage/src/body/mod.rs (L622-627)
```rust
        transaction_hash_to_idx_table.insert(txn, tx_hash, &transaction_index)?;
        transaction_metadata_table.append(
            txn,
            &transaction_index,
            &TransactionMetadata { tx_location, tx_output_location, tx_hash: *tx_hash },
        )?;
```

**File:** crates/starknet_api/src/transaction_hash.rs (L170-185)
```rust
pub fn validate_transaction_hash(
    transaction: &Transaction,
    block_number: &BlockNumber,
    chain_id: &ChainId,
    expected_hash: TransactionHash,
    transaction_options: &TransactionOptions,
) -> Result<bool, StarknetApiError> {
    let mut possible_hashes = get_deprecated_transaction_hashes(
        chain_id,
        block_number,
        transaction,
        transaction_options,
    )?;
    possible_hashes.push(get_transaction_hash(transaction, chain_id, transaction_options)?);
    Ok(possible_hashes.contains(&expected_hash))
}
```

**File:** crates/apollo_p2p_sync/src/client/mod.rs (L183-194)
```rust
        loop {
            tokio::select! {
                maybe_internal_block = internal_blocks_receiver.next() => {
                    let sync_block = maybe_internal_block.expect("Internal blocks stream should never end");
                    internal_blocks_senders.send(sync_block).await?;
                }
                data = data_stream.next() => {
                    let data = data.expect("Sync data stream should never end")?;
                    data.write_to_storage(&mut storage_writer, &mut class_manager_client).await?;
                }
            }
        }
```
