Let me trace the code path carefully.

I have the key evidence. Let me check if there's any commitment verification after storage, and what the header's `transaction_commitment` validation looks like.

Let me check the header validation and whether there's any post-storage commitment verification in the P2P sync path.

### Title
Missing Transaction Hash Validation in P2P Sync Allows Malicious Peer to Corrupt `transaction_hash_to_idx` Table and Poison `starknet_getTransactionByHash` — (`crates/apollo_p2p_sync/src/client/transaction.rs`)

---

### Summary

`TransactionStreamFactory::parse_data_for_block` accepts `FullTransaction.transaction_hash` from an untrusted P2P peer and pushes it directly into `BlockBody.transaction_hashes` without recomputing or verifying the hash against the transaction content and `chain_id`. The unvalidated hash is then persisted verbatim by `write_transactions` into both `transaction_hash_to_idx_table` and `TransactionMetadata.tx_hash`. Any subsequent `starknet_getTransactionByHash` RPC call uses that table as its sole lookup, so the full node will serve the wrong transaction for the forged hash and return `TXN_HASH_NOT_FOUND` for the real hash.

---

### Finding Description

**Step 1 — Unvalidated hash accepted from peer.**

In `parse_data_for_block`, after destructuring the `FullTransaction` received from the network, the code pushes `transaction_hash` directly with an explicit TODO acknowledging the missing check:

```rust
// TODO(eitan): Validate transaction hash from untrusted sources
block_body.transaction_hashes.push(transaction_hash);
``` [1](#0-0) 

No call to `get_transaction_hash(tx, chain_id)` or any equivalent is made before or after this line. The only checks performed are count-based (ensuring exactly `n_transactions` messages arrive). [2](#0-1) 

**Step 2 — Forged hash written to storage without recomputation.**

`write_to_storage` calls `append_body` unconditionally: [3](#0-2) 

`append_body` delegates to `write_transactions`, which inserts the attacker-supplied `tx_hash` as the key in `transaction_hash_to_idx_table` and as the `tx_hash` field of `TransactionMetadata`: [4](#0-3) 

No hash recomputation or comparison against the stored header's `transaction_commitment` occurs at any point in this path.

**Step 3 — RPC serves the corrupted table.**

`get_transaction_by_hash` resolves a transaction exclusively through `get_transaction_idx_by_hash`, which reads `transaction_hash_to_idx_table`: [5](#0-4) 

Because the table now maps `forged_hash → tx_index` instead of `real_hash → tx_index`, the RPC endpoint returns the transaction body for the forged hash and `TXN_HASH_NOT_FOUND` for the real hash.

---

### Impact Explanation

A full node syncing via P2P will store and serve permanently corrupted transaction-hash mappings for every block whose transactions were received from the malicious peer. Concretely:

- `starknet_getTransactionByHash(forged_hash)` returns the transaction body — an authoritative-looking wrong value.
- `starknet_getTransactionByHash(real_hash)` returns `TXN_HASH_NOT_FOUND` — a false negative for a legitimately included transaction.
- `TransactionMetadata.tx_hash` is permanently wrong, affecting any downstream consumer that reads it (e.g., `get_transaction_hash_by_idx`).
- The stored `transaction_commitment` in the header (also peer-supplied, with no post-storage cross-check) is inconsistent with the stored hashes, so the full node cannot self-verify block integrity.

This matches the allowed High impact: **"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."**

---

### Likelihood Explanation

Any peer that the full node connects to can trigger this. The P2P sync client connects to peers from the network without requiring any privileged role. The attack requires only that the peer be selected as the data source for a transaction query, which is the normal operating mode. The TODO comment confirms the developers are aware the validation is absent.

---

### Recommendation

After receiving `FullTransaction { transaction, transaction_hash, .. }` from the peer, recompute the expected hash using the chain-specific hash function (e.g., `get_transaction_hash(&transaction, chain_id)`) and compare it to the peer-supplied `transaction_hash`. If they differ, return `ParseDataError::BadPeer` and report the peer, consistent with how other protocol violations are handled in this file. The `chain_id` is available from the storage reader or can be passed as a parameter to `parse_data_for_block`.

Additionally, after all transactions for a block are collected, recompute `transaction_commitment` from the validated hashes and compare it against the value stored in the block header to detect any remaining inconsistency.

---

### Proof of Concept

```rust
// Pseudocode for a Rust integration test
let forged_hash = TransactionHash(StarkHash::from(0xdeadbeef_u64));
let real_tx = some_invoke_transaction();
let real_hash = get_transaction_hash(&real_tx, chain_id); // != forged_hash

// Peer sends FullTransaction with forged hash
let full_tx = FullTransaction {
    transaction: real_tx,
    transaction_output: Default::default(),
    transaction_hash: forged_hash,  // attacker-chosen
};

// After parse_data_for_block + write_to_storage + append_body + write_transactions:
let txn = storage_reader.begin_ro_txn().unwrap();

// Forged hash resolves to the transaction index
assert!(txn.get_transaction_idx_by_hash(&forged_hash).unwrap().is_some());

// Real hash is not found
assert!(txn.get_transaction_idx_by_hash(&real_hash).unwrap().is_none());
```

The `// TODO(eitan): Validate transaction hash from untrusted sources` comment at line 88 of `transaction.rs` is the direct code-level confirmation that this guard is intentionally absent and known to be missing. [1](#0-0)

### Citations

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L36-36)
```rust
            storage_writer.begin_rw_txn()?.append_body(self.1, self.0)?.commit()?;
```

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

**File:** crates/apollo_storage/src/body/mod.rs (L622-627)
```rust
        transaction_hash_to_idx_table.insert(txn, tx_hash, &transaction_index)?;
        transaction_metadata_table.append(
            txn,
            &transaction_index,
            &TransactionMetadata { tx_location, tx_output_location, tx_hash: *tx_hash },
        )?;
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L395-403)
```rust
        if let Some(transaction_index) =
            txn.get_transaction_idx_by_hash(&transaction_hash).map_err(internal_server_error)?
        {
            let transaction = txn
                .get_transaction(transaction_index)
                .map_err(internal_server_error)?
                .ok_or_else(|| ErrorObjectOwned::from(TRANSACTION_HASH_NOT_FOUND))?;

            Ok(TransactionWithHash { transaction: transaction.try_into()?, transaction_hash })
```
