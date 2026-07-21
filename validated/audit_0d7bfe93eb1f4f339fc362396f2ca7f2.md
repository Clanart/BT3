Based on my analysis of the full code path, this is a valid finding.

---

### Title
Unvalidated Peer-Supplied `transaction_hash` in P2P Sync Stored Directly to Body Storage — (`crates/apollo_p2p_sync/src/client/transaction.rs`)

### Summary
`TransactionStreamFactory::parse_data_for_block` accepts `transaction_hash` from an untrusted P2P peer and stores it verbatim into body storage without computing or verifying `H(tx_body_fields, chain_id)`. The developer acknowledged this with an explicit TODO. Any P2P peer serving block N's transactions can inject an arbitrary `TransactionHash` value that diverges from the header's committed `transaction_commitment`.

### Finding Description

In `parse_data_for_block`, the peer-supplied `transaction_hash` from `FullTransaction` is pushed directly into `block_body.transaction_hashes` at line 89 with no validation:

```rust
// TODO(eitan): Validate transaction hash from untrusted sources
block_body.transaction_hashes.push(transaction_hash);
``` [1](#0-0) 

`write_to_storage` then calls `append_body`, which calls `write_transactions`, which stores the attacker-chosen hash in two tables:

- `transaction_hash_to_idx_table.insert(txn, tx_hash, &transaction_index)` — maps the attacker hash to the transaction index
- `TransactionMetadata { tx_location, tx_output_location, tx_hash: *tx_hash }` — stores the attacker hash as the canonical hash for that transaction [2](#0-1) 

The function `validate_transaction_hash` exists in `transaction_hash.rs` and is capable of checking `H(tx_body, chain_id)` against an expected hash, but it is never called in this sync path. [3](#0-2) 

The header's `transaction_commitment` (a Patricia root of `H(tx_hash, tx_signature)` leaves) is stored separately via the header sync protocol and is not cross-checked against the body hashes at write time. [4](#0-3) 

### Impact Explanation

**Concrete corrupted values:**
- `transaction_metadata.tx_hash` for every transaction in block N is the attacker-chosen value, not `H(tx_body, chain_id)`.
- `transaction_hash_to_idx_table[attacker_hash]` → valid index; `transaction_hash_to_idx_table[real_hash]` → not found.
- `get_block_transaction_hashes(N)` returns attacker-chosen hashes.

**RPC impact:** `starknet_getTransactionByHash(real_hash)` returns `TRANSACTION_HASH_NOT_FOUND`. `starknet_getTransactionByHash(attacker_hash)` returns the transaction body paired with the wrong hash — an authoritative-looking wrong value served to clients. [5](#0-4) 

**Commitment divergence:** The stored body hashes diverge from the header's `transaction_commitment`. Any downstream system that recomputes `calculate_transaction_commitment` over the stored hashes will produce a root that does not match the header's committed root. [6](#0-5) 

The SNOS/proof-manager Critical claim in the question is overstated — the transaction prover (`VirtualSnosProver`) operates on RPC-submitted transactions, not on P2P-synced body storage. The actual impact is **High**: wrong hash stored, RPC serves wrong authoritative values, and the commitment invariant is broken for the affected node.

### Likelihood Explanation

Any P2P peer serving transactions for a block where the syncing node has no other source (e.g., a new block not yet widely propagated) can inject this. No special privileges are required. The syncing node does not disconnect the peer for providing a wrong hash — it only disconnects for count mismatches (`NotEnoughTransactions`). [7](#0-6) 

### Recommendation

After receiving `FullTransaction { transaction, transaction_output, transaction_hash }`, compute the expected hash using `get_transaction_hash(&transaction, chain_id, &TransactionOptions::default())` (or `validate_transaction_hash` for historical blocks) and return `ParseDataError::BadPeer` if the peer-supplied hash does not match. The `chain_id` is available from the node's configuration and should be threaded into `parse_data_for_block`. [8](#0-7) 

### Proof of Concept

1. Stand up a syncing node with an empty storage.
2. Serve a valid `SignedBlockHeader` for block 0 with `n_transactions = 1` and a correct `transaction_commitment` computed from the real hash `Y = H(tx_body, chain_id)`.
3. Serve a `FullTransaction` for that block where `transaction_hash = X` (attacker-chosen, `X ≠ Y`) but `transaction` and `transaction_output` are valid.
4. After sync, call `get_block_transaction_hashes(BlockNumber(0))` — it returns `[X]`.
5. Recompute `calculate_transaction_commitment` over `[X]` — the result diverges from the header's stored `transaction_commitment` (which commits to `Y`).
6. Call `starknet_getTransactionByHash(Y)` — returns `TRANSACTION_HASH_NOT_FOUND`.
7. Call `starknet_getTransactionByHash(X)` — returns the transaction body paired with the wrong hash `X`.

### Citations

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L67-91)
```rust
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

**File:** crates/apollo_storage/src/body/mod.rs (L507-511)
```rust
        transaction_hash_to_idx_table.insert(txn, tx_hash, &transaction_index)?;
        transaction_metadata_table.append(
            txn,
            &transaction_index,
            &TransactionMetadata { tx_location, tx_output_location, tx_hash: *tx_hash },
```

**File:** crates/starknet_api/src/transaction_hash.rs (L68-123)
```rust
pub fn get_transaction_hash(
    transaction: &Transaction,
    chain_id: &ChainId,
    transaction_options: &TransactionOptions,
) -> Result<TransactionHash, StarknetApiError> {
    let transaction_version = &signed_tx_version_from_tx(transaction, transaction_options);
    match transaction {
        Transaction::Declare(declare) => match declare {
            DeclareTransaction::V0(declare_v0) => {
                get_declare_transaction_v0_hash(declare_v0, chain_id, transaction_version)
            }
            DeclareTransaction::V1(declare_v1) => {
                get_declare_transaction_v1_hash(declare_v1, chain_id, transaction_version)
            }
            DeclareTransaction::V2(declare_v2) => {
                get_declare_transaction_v2_hash(declare_v2, chain_id, transaction_version)
            }
            DeclareTransaction::V3(declare_v3) => {
                get_declare_transaction_v3_hash(declare_v3, chain_id, transaction_version)
            }
        },
        Transaction::Deploy(deploy) => {
            get_deploy_transaction_hash(deploy, chain_id, transaction_version)
        }
        Transaction::DeployAccount(deploy_account) => match deploy_account {
            DeployAccountTransaction::V1(deploy_account_v1) => {
                get_deploy_account_transaction_v1_hash(
                    deploy_account_v1,
                    chain_id,
                    transaction_version,
                )
            }
            DeployAccountTransaction::V3(deploy_account_v3) => {
                get_deploy_account_transaction_v3_hash(
                    deploy_account_v3,
                    chain_id,
                    transaction_version,
                )
            }
        },
        Transaction::Invoke(invoke) => match invoke {
            InvokeTransaction::V0(invoke_v0) => {
                get_invoke_transaction_v0_hash(invoke_v0, chain_id, transaction_version)
            }
            InvokeTransaction::V1(invoke_v1) => {
                get_invoke_transaction_v1_hash(invoke_v1, chain_id, transaction_version)
            }
            InvokeTransaction::V3(invoke_v3) => {
                get_invoke_transaction_v3_hash(invoke_v3, chain_id, transaction_version)
            }
        },
        Transaction::L1Handler(l1_handler) => {
            get_l1_handler_transaction_hash(l1_handler, chain_id, transaction_version)
        }
    }
}
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

**File:** crates/apollo_storage/src/header.rs (L229-230)
```rust
            state_diff_commitment: block_header.state_diff_commitment,
            transaction_commitment: block_header.transaction_commitment,
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

**File:** crates/starknet_api/src/block_hash/transaction_commitment.rs (L34-39)
```rust
pub fn calculate_transaction_commitment<H: CoreStarkHash>(
    transaction_leaf_elements: &[TransactionLeafElement],
) -> TransactionCommitment {
    let transaction_leaves =
        transaction_leaf_elements.iter().map(calculate_transaction_leaf).collect();
    TransactionCommitment(calculate_root::<H>(transaction_leaves))
```
