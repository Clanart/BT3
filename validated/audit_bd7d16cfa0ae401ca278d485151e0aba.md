### Title
P2P Sync Stores Peer-Supplied Transactions Without Commitment Verification, Allowing Permuted Transaction Order — (`crates/apollo_p2p_sync/src/client/transaction.rs`)

### Summary

`TransactionStreamFactory::parse_data_for_block` collects exactly `n_transactions` items from a p2p peer and writes them to storage in arrival order with no verification against the `transaction_commitment` Patricia root stored in the block header. A malicious peer can send the same N transactions in any permutation and the local node will store that permuted body as authoritative.

### Finding Description

The loop in `parse_data_for_block` (lines 67–91) has exactly one guard: it stops when `current_transaction_len == target_transaction_len`. Each `FullTransaction` received from the peer is unconditionally appended:

```rust
block_body.transactions.push(transaction);
block_body.transaction_outputs.push(transaction_output);
// TODO(eitan): Validate transaction hash from untrusted sources
block_body.transaction_hashes.push(transaction_hash);
``` [1](#0-0) 

After the loop, `(block_body, block_number)` is returned and written to storage via `append_body` with no post-collection commitment check. [2](#0-1) 

The `transaction_commitment` is a Poseidon Patricia root where leaf `i = H(tx_hash_i, sig_i)`, computed in positional order: [3](#0-2) 

Permuting the transactions changes every non-trivial leaf position and therefore the root. Because no recomputation and comparison against the stored header commitment is performed, the mismatch is never detected.

The header sync path compounds this: `HeaderStreamBuilder::parse_data_for_block` stores the peer-supplied `SignedBlockHeader` verbatim, including its `transaction_commitment` field, with no block-hash or parent-hash verification (the TODO at line 102 of `header.rs` acknowledges this gap). [4](#0-3) 

This means a malicious peer can also supply a forged header whose `transaction_commitment` matches the permuted order, making the stored commitment internally consistent with the wrong body.

### Impact Explanation

The stored `BlockBody` is the authoritative source for RPC responses (`get_block_transactions`, `get_block_with_tx_hashes`, etc.) and for any execution-replay, tracing, or fee-estimation path that re-executes transactions in stored order. A permuted body causes those paths to return authoritative-looking wrong values — wrong nonce sequencing for dependent transactions, wrong fee results, wrong traces. This fits the **High** impact criterion: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

The question labels this Critical (wrong blockifier state), but that requires the node to re-execute from the stored body. On a pure sync node the blockifier is not re-run, so the direct impact is High-level RPC corruption rather than Critical-level state-root divergence.

### Likelihood Explanation

Any peer the local node connects to for transaction sync can exploit this. No operator privilege is required; the attacker only needs to respond to a standard p2p transaction query with a permuted payload of the correct length.

### Recommendation

After the collection loop completes, recompute the transaction commitment from the collected `block_body.transaction_hashes` and signatures, compare it against `header.transaction_commitment`, and return `ParseDataError::BadPeer` on mismatch — mirroring the existing `state_diff_length` guard pattern. The `calculate_transaction_commitment` function already exists for this purpose. [3](#0-2) 

### Proof of Concept

1. Run a local node in p2p sync mode against a controlled peer.
2. For a target block with N ≥ 2 transactions, have the peer respond with the transactions in reverse order (same set, different sequence), sending exactly `n_transactions` items followed by `Fin`.
3. The local node accepts the response (count matches, no commitment check).
4. Query `get_block_transactions` for that block — the returned order is the reversed (permuted) order, not the commitment-implied canonical order.
5. The stored `transaction_commitment` in the header (if the peer also forged it) matches the permuted root, so no internal consistency check catches the corruption.

### Citations

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L36-36)
```rust
            storage_writer.begin_rw_txn()?.append_body(self.1, self.0)?.commit()?;
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L86-90)
```rust
                block_body.transactions.push(transaction);
                block_body.transaction_outputs.push(transaction_output);
                // TODO(eitan): Validate transaction hash from untrusted sources
                block_body.transaction_hashes.push(transaction_hash);
                current_transaction_len += 1;
```

**File:** crates/starknet_api/src/block_hash/transaction_commitment.rs (L34-40)
```rust
pub fn calculate_transaction_commitment<H: CoreStarkHash>(
    transaction_leaf_elements: &[TransactionLeafElement],
) -> TransactionCommitment {
    let transaction_leaves =
        transaction_leaf_elements.iter().map(calculate_transaction_leaf).collect();
    TransactionCommitment(calculate_root::<H>(transaction_leaves))
}
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L102-114)
```rust
            // TODO(shahak): Check that parent_hash is the same as the previous block's hash
            // and handle reverts.
            if block_number
                != signed_block_header.block_header.block_header_without_hash.block_number
            {
                return Err(ParseDataError::BadPeer(BadPeerError::HeadersUnordered {
                    expected_block_number: block_number,
                    actual_block_number: signed_block_header
                        .block_header
                        .block_header_without_hash
                        .block_number,
                }));
            }
```
