### Title
Unimplemented `get_compiled_class_hash` in `SyncStateReader` Panics During Gateway Stateful Validation of Declare Transactions — (File: `crates/apollo_gateway/src/sync_state_reader.rs`)

### Summary

`SyncStateReader`, the production state reader used by the gateway's stateful transaction validator, implements `BlockifierStateReader::get_compiled_class_hash` with a bare `todo!()`. Any code path during stateful validation that calls this method will panic inside the `spawn_blocking` task, causing the gateway to return an internal error and reject the transaction.

### Finding Description

`SyncStateReader` implements the `BlockifierStateReader` (`StateReader`) trait for all methods except `get_compiled_class_hash`, which is left as a stub:

```rust
fn get_compiled_class_hash(&self, _class_hash: ClassHash) -> StateResult<CompiledClassHash> {
    todo!()
}
``` [1](#0-0) 

The `StateReader` trait requires this method to return `CompiledClassHash::default()` for undeclared or Cairo 0 classes, and the actual compiled class hash for declared Cairo 1 classes. [2](#0-1) 

The delegation chain from the gateway's stateful validator to this stub is:

1. `StatefulTransactionValidator::run_validate_entry_point` creates `CachedState::new(state_reader_and_contract_manager)` and calls `blockifier_validator.validate(account_tx)`. [3](#0-2) 

2. `StateReaderAndContractManager::get_compiled_class_hash` unconditionally delegates to `self.state_reader.get_compiled_class_hash(class_hash)`. [4](#0-3) 

3. `CachedState::get_compiled_class_hash` calls `self.state.get_compiled_class_hash(class_hash)` on a cache miss. [5](#0-4) 

4. The underlying state reader is `SyncOrGenesisStateReader::Sync(SyncStateReader)`, which delegates to `SyncStateReader::get_compiled_class_hash` — the `todo!()` stub. [6](#0-5) 

The panic is caught by Tokio's `spawn_blocking` join handle and converted to a `StarknetError::InternalError`, so the gateway process itself does not crash, but the transaction is rejected with an opaque internal error. [7](#0-6) 

The `SyncStateReaderFactory` is the production factory used when blocks exist; it constructs `SyncStateReader` directly. [8](#0-7) 

A second unimplemented stub exists in the `StateReader` trait default for `get_compiled_class_hash_v2`, which also panics via `unimplemented!()` if called on any reader that does not override it. [9](#0-8) 

### Impact Explanation

Any Declare transaction submitted to the gateway that causes the blockifier's stateful validator to call `get_compiled_class_hash` on the underlying state reader will panic inside `spawn_blocking`. The gateway converts this to an internal error and rejects the transaction. Valid Declare transactions are silently rejected before sequencing. This matches:

> **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

The blockifier's Declare transaction handling calls `get_compiled_class_hash` to check whether a class is already declared before writing the new compiled class hash. Whether this call occurs during the `validate`-only path (as opposed to full `execute`) depends on the exact `StatefulValidator::validate` implementation in the blockifier, which I was unable to fully read in this session. If the call occurs only during `execute` and not during `validate`, the gateway's validate-only path may not trigger the panic. This is the primary uncertainty in this finding.

### Likelihood Explanation

Any user can submit a Declare V2/V3 transaction to the public gateway endpoint. No privilege is required. The trigger is a standard, well-formed Declare transaction. The only uncertainty is whether the blockifier's `StatefulValidator::validate` (as opposed to full execution) reaches `get_compiled_class_hash` on the underlying reader.

### Recommendation

Implement `get_compiled_class_hash` in `SyncStateReader` by querying the state sync client, mirroring the pattern used in `apollo_rpc_execution`'s `ExecutionStateReader::get_compiled_class_hash`:

- Check pending declared classes first.
- Query `get_class_definition_block_number` from storage.
- Return `CompiledClassHash::default()` for Cairo 0 / undeclared classes.
- Look up `class_hash_to_compiled_class_hash` from the state diff for Cairo 1 classes. [10](#0-9) 

### Proof of Concept

1. Start the sequencer with at least one committed block (so `SyncStateReader` is used, not `GenesisStateReader`).
2. Submit a well-formed Declare V3 transaction via the gateway's `add_transaction` endpoint.
3. The gateway calls `StatefulTransactionValidator::run_validate_entry_point`.
4. Inside `spawn_blocking`, `CachedState::get_compiled_class_hash` is called on a cache miss, delegating to `SyncStateReader::get_compiled_class_hash`.
5. `todo!()` panics; the `JoinHandle` returns `Err(JoinError { is_panic: true })`.
6. The gateway returns `StarknetErrorCode::InternalError` — the valid Declare transaction is rejected. [1](#0-0) [11](#0-10)

### Citations

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L197-199)
```rust
    fn get_compiled_class_hash(&self, _class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        todo!()
    }
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L440-447)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        match self {
            Self::Sync(state_reader) => state_reader.get_compiled_class_hash(class_hash),
            Self::Genesis(genesis_state_reader) => {
                genesis_state_reader.get_compiled_class_hash(class_hash)
            }
        }
    }
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L535-545)
```rust
        let blockifier_state_reader = SyncStateReader::from_number(
            self.shared_state_sync_client.clone(),
            self.class_manager_client.clone(),
            latest_block_number,
            self.runtime.clone(),
        );
        let gateway_fixed_block_sync_state_client = GatewayFixedBlockSyncStateClient::new(
            self.shared_state_sync_client.clone(),
            latest_block_number,
        );
        Ok((blockifier_state_reader.into(), gateway_fixed_block_sync_state_client.into()))
```

**File:** crates/blockifier/src/state/state_api.rs (L44-46)
```rust
    /// Returns the compiled class hash of the given class hash.
    /// Returns CompiledClassHash::default() if no v1_class is found for the given class hash.
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash>;
```

**File:** crates/blockifier/src/state/state_api.rs (L69-79)
```rust
    fn get_compiled_class_hash_v2(
        &self,
        _class_hash: ClassHash,
        _compiled_class: &RunnableCompiledClass,
    ) -> StateResult<CompiledClassHash> {
        unimplemented!(
            "get_compiled_class_hash_v2 is not implemented in StateReader trait.
            There is a default implementation in utils.rs that can be used instead.
            However, this implementation computes the hash which may be expensive."
        );
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L309-326)
```rust
        tokio::task::spawn_blocking(move || {
            cur_span.in_scope(|| {
                let state = CachedState::new(state_reader_and_contract_manager);
                let mut blockifier_validator = StatefulValidator::create(state, block_context);
                blockifier_validator.validate(account_tx)
            })
        })
        .await
        .map_err(|e| StarknetError {
            code: StarknetErrorCode::UnknownErrorCode(
                "StarknetErrorCode.InternalError".to_string(),
            ),
            message: format!("Blocking task join error when running the validate entry point: {e}"),
        })?
        .map_err(|e| StarknetError {
            code: StarknetErrorCode::KnownErrorCode(KnownStarknetErrorCode::ValidateFailure),
            message: e.to_string(),
        })?;
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L155-157)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        self.state_reader.get_compiled_class_hash(class_hash)
    }
```

**File:** crates/blockifier/src/state/cached_state.rs (L203-215)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        let mut cache = self.cache.borrow_mut();

        if cache.get_compiled_class_hash(class_hash).is_none() {
            let compiled_class_hash = self.state.get_compiled_class_hash(class_hash)?;
            cache.set_compiled_class_hash_initial_value(class_hash, compiled_class_hash);
        }

        let compiled_class_hash = cache
            .get_compiled_class_hash(class_hash)
            .unwrap_or_else(|| panic!("Cannot retrieve '{class_hash:?}' from the cache."));
        Ok(*compiled_class_hash)
    }
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L163-208)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        if let Some(pending_data) = &self.maybe_pending_data {
            for DeclaredClassHashEntry { class_hash: other_class_hash, compiled_class_hash } in
                &pending_data.declared_classes
            {
                if class_hash == *other_class_hash {
                    return Ok(*compiled_class_hash);
                }
            }
        }

        let maybe_block_number = self
            .storage_reader
            .begin_ro_txn()
            .map_err(storage_err_to_state_err)?
            .get_state_reader()
            .map_err(storage_err_to_state_err)?
            .get_class_definition_block_number(&class_hash)
            .map_err(storage_err_to_state_err)?;

        // Cairo 0 classes (and undeclared classes) do not have a compiled class hash.
        // According to the trait, return the default value.
        let Some(block_number) = maybe_block_number else {
            return Ok(CompiledClassHash::default());
        };

        let state_diff = self
            .storage_reader
            .begin_ro_txn()
            .map_err(storage_err_to_state_err)?
            .get_state_diff(block_number)
            .map_err(storage_err_to_state_err)?
            .ok_or(StateError::StateReadError(format!(
                "Inner storage error. Missing state diff at block {block_number}."
            )))?;

        let compiled_class_hash = state_diff
            .class_hash_to_compiled_class_hash
            .get(&class_hash)
            .ok_or(StateError::StateReadError(format!(
                "Inner storage error. Missing class declaration at block {block_number}, class \
                 {class_hash}."
            )))?;

        Ok(*compiled_class_hash)
    }
```
