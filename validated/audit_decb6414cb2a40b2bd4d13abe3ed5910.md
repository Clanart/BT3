### Title
P2P State Diff Sync Validates Length But Not Commitment Hash, Allowing Malicious Peer to Inject Wrong State — (`File: crates/apollo_p2p_sync/src/client/state_diff.rs`)

### Summary

`parse_data_for_block` in the P2P state diff sync validates the assembled `ThinStateDiff` against the stored header's `state_diff_length` but never verifies it against the stored `state_diff_commitment`. A malicious peer can send state diff chunks whose element count matches the header's length field but whose content (storage values, class hashes, nonces) differs from what was committed. The wrong diff is written to storage, the committer derives a wrong global root, and every downstream consumer — RPC, proof inputs, block hash — operates on corrupted state.

### Finding Description

`parse_data_for_block` reads the block header once to obtain `target_state_diff_len` and uses it as the sole integrity gate:

```rust
let target_state_diff_len = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("A header with number lower than the header marker is missing")
    .state_diff_length          // ← only this field is used
    .ok_or(P2pSyncClientError::OldHeaderInStorage { ... })?;
``` [1](#0-0) 

After assembling all chunks the function calls `validate_deprecated_declared_classes_non_conflicting` (a structural check) and returns the diff:

```rust
validate_deprecated_declared_classes_non_conflicting(&result)?;
Ok(Some((result, block_number)))
``` [2](#0-1) 

The same header also carries `state_diff_commitment` — a Poseidon hash over the full diff content — but it is never read or compared:

```rust
pub fn state_diff_commitment(&self) -> Option<StateDiffCommitment> {
    match self {
        Block::PostV0_13_1(block) => block.state_diff_commitment,
    }
}
``` [3](#0-2) 

The central sync path carries the same gap, acknowledged by a TODO:

```rust
// TODO(dan): verifications - verify state diff against stored header.
``` [4](#0-3) 

The assembled diff is written directly to storage without any hash check:

```rust
storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
``` [5](#0-4) 

The committer's `verify_state_diff_hash` flag is a separate, opt-in config that is off by default (tests must explicitly set it to `true`), so it does not close the gap in the sync path:

```rust
committer.config.verify_state_diff_hash = true;
``` [6](#0-5) 

The commitment hash is computed by `calculate_state_diff_hash`, which covers deployed contracts, declared classes, deprecated declared classes, storage diffs, and nonces — exactly the fields a peer can manipulate:

```rust
pub fn calculate_state_diff_hash(state_diff: &ThinStateDiff) -> StateDiffCommitment {
    let mut hash_chain = HashChain::new();
    hash_chain = hash_chain.chain(&STARKNET_STATE_DIFF0);
    hash_chain = chain_deployed_contracts(&state_diff.deployed_contracts, hash_chain);
    hash_chain = chain_declared_classes(&state_diff.class_hash_to_compiled_class_hash, hash_chain);
    hash_chain = chain_deprecated_declared_classes(&state_diff.deprecated_declared_classes, hash_chain);
    ...
``` [7](#0-6) 

### Impact Explanation

A wrong `ThinStateDiff` stored in MDBX propagates through the entire stack:

1. **Wrong global root** — `commit_state_diff` applies the injected diff to the Patricia trie, producing a `GlobalRoot` that does not match the canonical chain. [8](#0-7) 

2. **Wrong block hash** — `calculate_block_hash` chains the corrupted `state_diff_commitment` and the wrong `global_root` into the block hash. [9](#0-8) 

3. **Wrong RPC responses** — storage reads, nonce queries, class hash lookups, and `starknet_getStateUpdate` all return values derived from the corrupted trie.

4. **Wrong proof inputs** — SNOS and the transaction prover read state roots and storage proofs from the same corrupted storage. [10](#0-9) 

Matching impacts: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value**; and **Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result**.

### Likelihood Explanation

Any node that participates in P2P sync is exposed. The attacker needs only to be a reachable peer; no privileged role, no key material, and no on-chain transaction is required. The attack is silent: the length check passes, no error is returned, and the corrupted diff is committed atomically.

### Recommendation

After assembling the final `ThinStateDiff` in `parse_data_for_block`, compute its Poseidon commitment and compare it against the value stored in the block header:

```rust
// After validate_deprecated_declared_classes_non_conflicting(&result)?;
let header = storage_reader.begin_ro_txn()?.get_block_header(block_number)? ...;
if let Some(expected_commitment) = header.state_diff_commitment {
    let actual_commitment = calculate_state_diff_hash(&result);
    if actual_commitment != expected_commitment {
        return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffCommitment {
            expected: expected_commitment,
            actual: actual_commitment,
        }));
    }
}
```

Apply the same check in `apollo_central_sync::store_state_diff` (removing the existing TODO).

### Proof of Concept

1. Sync node A connects to malicious peer M via P2P.
2. Node A has already synced block header N, which stores `state_diff_length = 3` and `state_diff_commitment = H_correct`.
3. M sends three `StateDiffChunk` items whose combined `len()` equals 3 but whose storage values differ from the canonical block (e.g., a balance slot is set to an attacker-controlled value).
4. `parse_data_for_block` accepts the chunks: `current_state_diff_len (3) == target_state_diff_len (3)`, `validate_deprecated_declared_classes_non_conflicting` passes, and `Ok(Some((result, block_number)))` is returned. [11](#0-10) 
5. `write_to_storage` commits the corrupted diff to MDBX. [5](#0-4) 
6. The committer applies the diff to the Patricia trie, producing a wrong `GlobalRoot`, which is stored under `ForestMetadataType::StateRoot(DbBlockNumber(height))`. [12](#0-11) 
7. All subsequent RPC calls to `starknet_getStorageAt` for the manipulated slot return the attacker-injected value.

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L34-34)
```rust
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L99-107)
```rust
            if current_state_diff_len != target_state_diff_len {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                    expected_length: target_state_diff_len,
                    possible_lengths: vec![prev_result_len, current_state_diff_len],
                }));
            }

            validate_deprecated_declared_classes_non_conflicting(&result)?;
            Ok(Some((result, block_number)))
```

**File:** crates/apollo_starknet_client/src/reader/objects/block.rs (L264-270)
```rust
    pub fn state_diff_commitment(&self) -> Option<StateDiffCommitment> {
        match self {
            // TODO(shahak): in SN API, make StateDiffCommitment implement Copy and remove this
            // clone.
            Block::PostV0_13_1(block) => block.state_diff_commitment,
        }
    }
```

**File:** crates/apollo_central_sync/src/lib.rs (L442-443)
```rust
        // TODO(dan): verifications - verify state diff against stored header.
        debug!("Storing state diff.");
```

**File:** crates/apollo_committer/src/committer_test.rs (L317-317)
```rust
    committer.config.verify_state_diff_hash = true;
```

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L30-41)
```rust
pub fn calculate_state_diff_hash(state_diff: &ThinStateDiff) -> StateDiffCommitment {
    let mut hash_chain = HashChain::new();
    hash_chain = hash_chain.chain(&STARKNET_STATE_DIFF0);
    hash_chain = chain_deployed_contracts(&state_diff.deployed_contracts, hash_chain);
    hash_chain = chain_declared_classes(&state_diff.class_hash_to_compiled_class_hash, hash_chain);
    hash_chain =
        chain_deprecated_declared_classes(&state_diff.deprecated_declared_classes, hash_chain);
    hash_chain = hash_chain.chain(&Felt::ONE) // placeholder.
        .chain(&Felt::ZERO); // placeholder.
    hash_chain = chain_storage_diffs(&state_diff.storage_diffs, hash_chain);
    hash_chain = chain_nonces(&state_diff.nonces, hash_chain);
    StateDiffCommitment(PoseidonHash(hash_chain.get_poseidon_hash()))
```

**File:** crates/apollo_committer/src/committer.rs (L207-208)
```rust
        let (filled_forest, global_root) =
            self.commit_state_diff(state_diff, &mut block_measurements).await?;
```

**File:** crates/apollo_committer/src/committer.rs (L215-218)
```rust
            (
                ForestMetadataType::StateRoot(DbBlockNumber(height)),
                serialize_felt_no_packing(global_root.0),
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

**File:** crates/starknet_transaction_prover/src/running/committer_utils.rs (L284-303)
```rust
pub async fn commit_state_diff(
    facts_db: &mut FactsDb<MapStorage>,
    contracts_trie_root_hash: HashOutput,
    classes_trie_root_hash: HashOutput,
    state_diff: StateDiff,
) -> Result<StateRoots, ProofProviderError> {
    let config = ReaderConfig::default();
    let initial_read_context =
        FactsDbInitialRead(StateRoots { contracts_trie_root_hash, classes_trie_root_hash });
    let input = Input { state_diff, initial_read_context, config };

    let filled_forest = CommitBlockImpl::commit_block(input, facts_db, &mut NoMeasurements)
        .await
        .map_err(|e| ProofProviderError::BlockCommitmentError(e.to_string()))?;
    facts_db.write(&filled_forest).await?;

    Ok(StateRoots {
        contracts_trie_root_hash: filled_forest.get_contract_root_hash(),
        classes_trie_root_hash: filled_forest.get_compiled_class_root_hash(),
    })
```
