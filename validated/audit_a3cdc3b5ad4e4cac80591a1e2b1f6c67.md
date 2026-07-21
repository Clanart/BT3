### Title
Missing Transaction Hash Validation in P2P Sync Allows Malicious Peer to Corrupt Hash-to-Transaction Binding — (`crates/apollo_p2p_sync/src/client/transaction.rs`)

---

### Summary

A malicious p2p peer can respond to a `TransactionQuery` with `FullTransaction { transaction: tx_A, transaction_hash: h_B }` where `h_B ≠ hash(tx_A)`. Because `parse_data_for_block` accepts the peer-supplied `transaction_hash` without any verification, the storage layer permanently records `h_B → index(tx_A)` in the `transaction_hash_to_idx` table. The RPC handler for `starknet_getTransactionByHash` then serves `tx_A` in response to a query for `h_B`, and `hash(tx_A)` becomes permanently unfindable.

---

### Finding Description

**Step 1 — No validation in `parse_data_for_block`** [1](#0-0) 

The peer-supplied `transaction_hash` is pushed directly into `block_body.transaction_hashes` with no recomputation or comparison against `hash(transaction)`. The developer-acknowledged TODO at line 88 confirms this is a known gap:

```
// TODO(eitan): Validate transaction hash from untrusted sources
block_body.transaction_hashes.push(transaction_hash);
```

**Step 2 — `write_transactions` stores the fake hash verbatim** [2](#0-1) 

`write_transactions` zips `block_body.transaction_hashes` with `block_body.transactions` and writes both the `transaction_hash_to_idx` entry and the `TransactionMetadata.tx_hash` field using the peer-supplied value. No hash is recomputed at any point in this path.

**Step 3 — RPC serves the corrupted binding** [3](#0-2) 

`get_transaction_by_hash(h_B)` resolves `h_B` to the stored index, fetches `tx_A` from that index, and returns `TransactionWithHash { transaction: tx_A, transaction_hash: h_B }`. The real hash `hash(tx_A)` was never inserted into `transaction_hash_to_idx`, so `get_transaction_by_hash(hash(tx_A))` returns `TRANSACTION_HASH_NOT_FOUND`.

---

### Impact Explanation

The impact is **High**:

- `starknet_getTransactionByHash(h_B)` returns `tx_A` — an authoritative-looking wrong value binding a transaction to a hash it does not own.
- `starknet_getTransactionByHash(hash(tx_A))` returns `TRANSACTION_HASH_NOT_FOUND` — the real transaction is permanently unfindable by its correct hash.
- Any downstream consumer (explorer, bridge, relayer, receipt verifier) that trusts the RPC response receives a wrong hash-to-body binding.
- The corruption is permanent for the lifetime of the stored block body; it cannot be corrected without a re-sync.

This fits the allowed impact: **"High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."** and **"High. RPC execution... returns an authoritative-looking wrong value."**

---

### Likelihood Explanation

Any node that connects to a malicious p2p peer during body sync is affected. No special privileges are required — the attacker only needs to be a peer that the syncing node queries. The `BLOCK_NUMBER_LIMIT::HeaderMarker` constraint only ensures headers exist first; it does not verify transaction hashes against any commitment in the header. [4](#0-3) 

---

### Recommendation

In `parse_data_for_block`, after receiving each `FullTransaction`, recompute the transaction hash and compare it against the peer-supplied value. Reject the peer (return `ParseDataError::BadPeer`) if they differ:

```rust
let computed_hash = transaction.calculate_hash(); // or equivalent poseidon hash
if computed_hash != transaction_hash {
    return Err(ParseDataError::BadPeer(BadPeerError::InvalidTransactionHash { ... }));
}
```

Additionally, verify the resulting transaction commitment (Merkle/Poseidon root over all transaction hashes) against the value committed in the already-stored block header before calling `append_body`.

---

### Proof of Concept

The following Rust pseudocode demonstrates the corruption:

```rust
// Attacker peer sends:
let tx_A = some_valid_transaction();
let h_B = TransactionHash(Felt::from(0xdeadbeef)); // h_B != hash(tx_A)
let full_tx = FullTransaction { transaction: tx_A.clone(), transaction_output: ..., transaction_hash: h_B };

// parse_data_for_block accepts it without validation (line 88-89 of transaction.rs)
// write_transactions stores h_B -> index, TransactionMetadata { tx_hash: h_B, tx_location: loc(tx_A) }

// After sync:
let idx = storage.get_transaction_idx_by_hash(&h_B).unwrap(); // Some(index)
let stored_tx = storage.get_transaction(idx).unwrap();        // tx_A
assert_eq!(stored_tx, tx_A);                                  // PASSES — tx_A stored at h_B's index

let real_hash = hash(tx_A);
let not_found = storage.get_transaction_idx_by_hash(&real_hash); // None — real hash missing
assert!(not_found.is_none());                                     // PASSES — real tx unfindable
```

This proves the hash-to-body binding is broken: `transaction_hash_to_idx[h_B]` points to `tx_A`, while `hash(tx_A)` is absent from the index entirely.

### Citations

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L52-52)
```rust
    const BLOCK_NUMBER_LIMIT: BlockNumberLimit = BlockNumberLimit::HeaderMarker;
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L86-89)
```rust
                block_body.transactions.push(transaction);
                block_body.transaction_outputs.push(transaction_output);
                // TODO(eitan): Validate transaction hash from untrusted sources
                block_body.transaction_hashes.push(transaction_hash);
```

**File:** crates/apollo_storage/src/body/mod.rs (L507-512)
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
