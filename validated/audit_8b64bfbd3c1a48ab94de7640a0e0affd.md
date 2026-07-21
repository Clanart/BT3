Now I have enough context to evaluate this fully. Let me trace the complete attack path through the storage layer.

The attack path is fully traceable. Here is the evaluation:

---

### Title
Unauthenticated P2P Peer Can Corrupt Stored Block Body by Supplying Mismatched `Transaction`/`transaction_hash` in `parse_data_for_block` — (`crates/apollo_p2p_sync/src/client/transaction.rs`)

### Summary

`TransactionStreamFactory::parse_data_for_block` accepts `FullTransaction` structs from untrusted p2p peers and pushes `transaction` and `transaction_hash` into `BlockBody` independently, with no cross-validation between the two fields. An explicit TODO at line 88 acknowledges this gap. A malicious peer can supply `FullTransaction { transaction: Declare(...), transaction_hash: <invoke_hash> }`, causing the node to permanently store a `Declare` transaction body indexed under an Invoke hash. All downstream RPC paths that look up by hash and then re-execute the stored body — `starknet_traceTransaction`, `starknet_traceBlockTransactions` — will execute the wrong transaction type and return authoritative-looking wrong traces and fee estimations.

### Finding Description

In `parse_data_for_block`:

```rust
block_body.transactions.push(transaction);
block_body.transaction_outputs.push(transaction_output);
// TODO(eitan): Validate transaction hash from untrusted sources
block_body.transaction_hashes.push(transaction_hash);
``` [1](#0-0) 

The three fields are pushed independently. No check verifies that `transaction_hash` was actually computed from `transaction`. The TODO at line 88 is the only guard — and it is absent.

`write_to_storage` calls `append_body`, which calls `write_transactions`:

```rust
transaction_hash_to_idx_table.insert(txn, tx_hash, &transaction_index)?;
transaction_metadata_table.append(
    txn,
    &transaction_index,
    &TransactionMetadata { tx_location, tx_output_location, tx_hash: *tx_hash },
)?;
``` [2](#0-1) 

The `Transaction` (Declare) is written to the file store at `tx_location`, while `invoke_hash` is written as the lookup key and stored in `TransactionMetadata.tx_hash`. The two are permanently decoupled in storage.

### Impact Explanation

**`starknet_getTransactionByHash(invoke_hash)`** path: [3](#0-2) 

`get_transaction_idx_by_hash(invoke_hash)` returns index `i`, then `get_transaction(i)` returns `Declare(...)`. The RPC returns `TransactionWithHash { transaction: Declare(...), transaction_hash: invoke_hash }` — a concrete wrong value.

**`starknet_traceTransaction(invoke_hash)`** path: [4](#0-3) 

`get_transaction_idx_by_hash(invoke_hash)` → index `i`; `get_block_transactions(block_number)` → `[..., Declare(...), ...]`; `stored_txn_to_executable_txn(Declare(...), ...)` converts to `ExecutableTransactionInput::DeclareV*`; `exec_simulate_transactions` re-executes it. The result is a `TransactionTrace::Declare` returned for a hash that should produce an `InvokeTransactionTrace`. Fee estimation, gas accounting, and the execution trace are all wrong. [5](#0-4) 

### Likelihood Explanation

Any peer that the p2p sync client connects to can trigger this. The client sends a query, the peer responds with `FullTransaction` messages, and there is zero validation of the hash-to-transaction binding before storage write. The node has no way to detect the corruption after the fact because no code ever cross-checks stored transaction hashes against the header's `transaction_commitment`. [6](#0-5) 

### Recommendation

Before pushing `transaction_hash` into `block_body`, recompute the hash from `transaction` using the appropriate versioned hash function and reject (report peer + retry) if it does not match the peer-supplied value. This is exactly what the TODO at line 88 calls for. [7](#0-6) 

### Proof of Concept

Concrete storage state after the attack:

| Storage table | Key | Value |
|---|---|---|
| `transaction_hash_to_idx` | `invoke_hash` | `TransactionIndex(B, 0)` |
| `transaction_metadata` | `TransactionIndex(B, 0)` | `{ tx_location: L, tx_hash: invoke_hash }` |
| file store at `L` | — | `Transaction::Declare(...)` |

Assertions that would hold after feeding `FullTransaction { transaction: Declare(...), transaction_hash: invoke_hash }` through `parse_data_for_block` and `write_to_storage`:

```rust
// Hash lookup finds the index
let idx = txn.get_transaction_idx_by_hash(&invoke_hash).unwrap().unwrap();
// But the stored transaction is Declare, not Invoke
let tx = txn.get_transaction(idx).unwrap().unwrap();
assert!(matches!(tx, Transaction::Declare(_)));
// And the stored hash is the invoke hash
let hash = txn.get_transaction_hash_by_idx(&idx).unwrap().unwrap();
assert_eq!(hash, invoke_hash);
// Type and hash are permanently mismatched
```

The `starknet_traceTransaction(invoke_hash)` RPC call then re-executes the `Declare` body and returns a `DeclareTransactionTrace` for a hash that the network knows as an Invoke transaction — an authoritative-looking wrong value served to all RPC consumers.

### Citations

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

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1214-1244)
```rust
            let TransactionIndex(block_number, tx_offset) = storage_txn
                .get_transaction_idx_by_hash(&transaction_hash)
                .map_err(internal_server_error)?
                .ok_or(TRANSACTION_HASH_NOT_FOUND)?;

            let block_transactions = storage_txn
                .get_block_transactions(block_number)
                .map_err(internal_server_error)?
                .ok_or_else(|| {
                    internal_server_error(StorageError::DBInconsistency {
                        msg: format!("Missing block {block_number} transactions"),
                    })
                })?;

            let transaction_hashes = storage_txn
                .get_block_transaction_hashes(block_number)
                .map_err(internal_server_error)?
                .ok_or_else(|| {
                    internal_server_error(StorageError::DBInconsistency {
                        msg: format!("Missing block {block_number} transactions"),
                    })
                })?;

            let state_number = StateNumber::right_before_block(block_number);
            let executable_transactions = block_transactions
                .into_iter()
                .take(tx_offset.0 + 1)
                .map(|tx| stored_txn_to_executable_txn(tx, &storage_txn, state_number))
                .collect::<Result<_, _>>()?;

            (None, executable_transactions, transaction_hashes, block_number, state_number)
```

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L341-431)
```rust
pub(crate) fn stored_txn_to_executable_txn(
    stored_txn: starknet_api::transaction::Transaction,
    storage_txn: &StorageTxn<'_, RO>,
    state_number: StateNumber,
) -> Result<ExecutableTransactionInput, ErrorObjectOwned> {
    match stored_txn {
        starknet_api::transaction::Transaction::Declare(
            starknet_api::transaction::DeclareTransaction::V0(value),
        ) => {
            // Copy the class hash before the value moves.
            let class_hash = value.class_hash;
            let deprecated_class =
                get_deprecated_class_for_re_execution(storage_txn, state_number, class_hash)?;
            let abi_length = calculate_deprecated_class_abi_length(&deprecated_class)
                .map_err(internal_server_error)?;
            Ok(ExecutableTransactionInput::DeclareV0(value, deprecated_class, abi_length, false))
        }
        starknet_api::transaction::Transaction::Declare(
            starknet_api::transaction::DeclareTransaction::V1(value),
        ) => {
            // Copy the class hash before the value moves.
            let class_hash = value.class_hash;
            let deprecated_class =
                get_deprecated_class_for_re_execution(storage_txn, state_number, class_hash)?;
            let abi_length = calculate_deprecated_class_abi_length(&deprecated_class)
                .map_err(internal_server_error)?;
            Ok(ExecutableTransactionInput::DeclareV1(value, deprecated_class, abi_length, false))
        }
        starknet_api::transaction::Transaction::Declare(
            starknet_api::transaction::DeclareTransaction::V2(value),
        ) => {
            let casm = storage_txn
                .get_casm(&value.class_hash)
                .map_err(internal_server_error)?
                .ok_or_else(|| {
                    internal_server_error(format!(
                        "Missing casm of class hash {}.",
                        value.class_hash
                    ))
                })?;
            let (sierra_program_length, abi_length, sierra_version) =
                get_class_lengths(storage_txn, state_number, value.class_hash)?;
            Ok(ExecutableTransactionInput::DeclareV2(
                value,
                casm,
                sierra_program_length,
                abi_length,
                false,
                sierra_version,
            ))
        }
        starknet_api::transaction::Transaction::Declare(
            starknet_api::transaction::DeclareTransaction::V3(value),
        ) => {
            let casm = storage_txn
                .get_casm(&value.class_hash)
                .map_err(internal_server_error)?
                .ok_or_else(|| {
                    internal_server_error(format!(
                        "Missing casm of class hash {}.",
                        value.class_hash
                    ))
                })?;
            let (sierra_program_length, abi_length, sierra_version) =
                get_class_lengths(storage_txn, state_number, value.class_hash)?;
            Ok(ExecutableTransactionInput::DeclareV3(
                value,
                casm,
                sierra_program_length,
                abi_length,
                false,
                sierra_version,
            ))
        }
        starknet_api::transaction::Transaction::Deploy(_) => {
            Err(internal_server_error("Deploy txns not supported in execution"))
        }
        starknet_api::transaction::Transaction::DeployAccount(deploy_account_tx) => {
            Ok(ExecutableTransactionInput::DeployAccount(deploy_account_tx, false))
        }
        starknet_api::transaction::Transaction::Invoke(value) => {
            Ok(ExecutableTransactionInput::Invoke(value, false))
        }
        starknet_api::transaction::Transaction::L1Handler(value) => {
            // todo(yair): This is a temporary solution until we have a better way to get the l1
            // fee.
            let paid_fee_on_l1 = Fee(1);
            Ok(ExecutableTransactionInput::L1Handler(value, paid_fee_on_l1, false))
        }
    }
}
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L82-123)
```rust
    fn parse_data_for_block<'a>(
        signed_headers_response_manager: &'a mut ClientResponsesManager<
            DataOrFin<SignedBlockHeader>,
        >,
        block_number: BlockNumber,
        _storage_reader: &'a StorageReader,
    ) -> BoxFuture<'a, Result<Option<Self::Output>, ParseDataError>> {
        async move {
            // TODO(noamsp): investigate and remove this timeout.
            let maybe_signed_header =
                timeout(Duration::from_secs(15), signed_headers_response_manager.next())
                    .await
                    .ok()
                    .flatten()
                    .ok_or(ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }))?;
            let Some(signed_block_header) = maybe_signed_header?.0 else {
                return Ok(None);
            };
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
            if signed_block_header.signatures.len() != ALLOWED_SIGNATURES_LENGTH {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongSignaturesLength {
                    signatures: signed_block_header.signatures,
                }));
            }
            Ok(Some(signed_block_header))
        }
        .boxed()
    }
```
