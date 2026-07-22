### Title
Missing State Diff Hash Validation Against Header Commitment in P2P Sync Client - (File: `crates/apollo_p2p_sync/src/client/state_diff.rs`)

### Summary

The P2P sync client collects state diff chunks from peers and validates only that the total chunk count matches `header.state_diff_length`. It never verifies that `calculate_state_diff_hash(&assembled_diff) == header.state_diff_commitment`. A malicious peer can send chunks whose count is correct but whose content is fabricated, causing the node to store a wrong `ThinStateDiff`. Every subsequent RPC storage/nonce/class-hash query and every state-root computation will then operate on the corrupted state.

---

### Finding Description

`parse_data_for_block` in `StateDiffStreamBuilder` reads the expected length from the stored header and loops until the running chunk count reaches that target:

```rust
let target_state_diff_len = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    ...
    .state_diff_length
    .ok_or(...)?;

while current_state_diff_len < target_state_diff_len {
    ...
    current_state_diff_len += state_diff_chunk.len();
    unite_state_diffs(&mut result, state_diff_chunk)?;
}

if current_state_diff_len != target_state_diff_len {
    return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength { ... }));
}

validate_deprecated_declared_classes_non_conflicting(&result)?;
Ok(Some((result, block_number)))   // ← stored without hash check
``` [1](#0-0) 

The assembled `result` is then written directly to storage:

```rust
storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
``` [2](#0-1) 

The header that was stored earlier carries a `state_diff_commitment` (a Poseidon hash over the full state diff) and a `state_diff_length`. The client uses only `state_diff_length` as a termination guard. The `state_diff_commitment` field is stored in the header but is **never cross-checked** against the assembled diff. [3](#0-2) 

`calculate_state_diff_hash` is the canonical function that produces the commitment: [4](#0-3) 

The `state_diff_commitment` is also embedded in `concatenated_counts` inside the block hash, so it is authenticated by the block signatures that accompany the header. A peer cannot forge the commitment without forging the validators' signatures. However, nothing stops a peer from sending chunks whose aggregate length equals `state_diff_length` but whose content hashes to a completely different value.

The analog to the original report is direct:

| Original (Solana vault) | Sequencer analog |
|---|---|
| `vault_staker_withdrawal_ticket_token_account` balance (live on-chain) | Assembled `ThinStateDiff` received from peer |
| `VaultStakerWithdrawalTicket.vrt_amount()` (recorded amount) | `state_diff_commitment` in the stored `BlockHeader` |
| Attacker directly transfers tokens to inflate the live balance | Malicious peer sends chunks with correct count but wrong content |
| `close_account` fails because the two values diverge | State root computed from the wrong diff diverges from the committed root |

---

### Impact Explanation

Once the wrong `ThinStateDiff` is committed to storage via `append_state_diff`, every read path that consults the state reader (`get_storage_at`, `get_nonce_at`, `get_class_hash_at`) returns fabricated values. [5](#0-4) 

The `apollo_p2p_sync` server re-exports the stored state diff as `StateDiffChunk` items to downstream peers, propagating the corruption further: [6](#0-5) 

The committer does have an optional `verify_state_diff_hash` flag, but it is gated behind a config option and is not enforced in the sync path: [7](#0-6) 

The global state root written to the forest will diverge from the root that the block hash commits to, producing an authoritative-looking but wrong value for every downstream consumer (RPC, proof manager, SNOS input).

**Matching impact:** *High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.*

---

### Likelihood Explanation

Any node that participates in the P2P network can act as a sync peer. No privileged position is required. The attacker only needs to:
1. Serve a valid `SignedBlockHeader` (replayed from the real chain, so signatures are genuine).
2. Serve state diff chunks whose total `len()` equals `header.state_diff_length` but whose content differs from the real state diff.

The client will accept the response and store the corrupted diff.

---

### Recommendation

After assembling the full `ThinStateDiff`, compute its Poseidon hash and compare it against the commitment stored in the header before accepting the result:

```rust
use starknet_api::block_hash::state_diff_hash::calculate_state_diff_hash;

let computed_commitment = calculate_state_diff_hash(&result);
let header_commitment = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("header must exist")
    .state_diff_commitment
    .ok_or(P2pSyncClientError::OldHeaderInStorage {
        block_number,
        missing_field: "state_diff_commitment",
    })?;

if computed_commitment != header_commitment {
    return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffHash {
        block_number,
        expected: header_commitment,
        got: computed_commitment,
    }));
}
```

This mirrors the check already present in the committer (`verify_state_diff_hash`) and closes the gap between the two representations.

---

### Proof of Concept

1. Sync node A connects to malicious peer M.
2. M serves a valid `SignedBlockHeader` for block N (replayed from the real chain). The header contains a genuine `state_diff_commitment` = `H_real` and `state_diff_length` = `L`.
3. M serves `L` state diff chunks whose content is fabricated (e.g., all storage values set to `0xdead`). The total chunk count equals `L`, so the length check passes.
4. `parse_data_for_block` returns `Ok(Some((fabricated_diff, N)))`.
5. `write_to_storage` calls `append_state_diff(N, fabricated_diff)`.
6. Node A's RPC now returns `0xdead` for any storage slot that was modified in block N.
7. `calculate_state_diff_hash(&fabricated_diff)` ≠ `H_real`, but this is never checked. [8](#0-7) [4](#0-3) [9](#0-8)

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L26-39)
```rust
impl BlockData for (ThinStateDiff, BlockNumber) {
    #[latency_histogram("p2p_sync_state_diff_write_to_storage_latency_seconds", true)]
    fn write_to_storage<'a>(
        self: Box<Self>,
        storage_writer: &'a mut StorageWriter,
        _class_manager_client: &'a mut SharedClassManagerClient,
    ) -> BoxFuture<'a, Result<(), P2pSyncClientError>> {
        async move {
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
            STATE_SYNC_STATE_MARKER.set_lossy(self.1.unchecked_next().0);
            Ok(())
        }
        .boxed()
    }
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L58-110)
```rust
        async move {
            let mut result = ThinStateDiff::default();
            let mut prev_result_len = 0;
            let mut current_state_diff_len = 0;
            let target_state_diff_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .state_diff_length
                .ok_or(P2pSyncClientError::OldHeaderInStorage {
                    block_number,
                    missing_field: "state_diff_length",
                })?;

            while current_state_diff_len < target_state_diff_len {
                let maybe_state_diff_chunk = state_diff_chunks_response_manager
                    .next()
                    .await
                    .ok_or(ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }))?;
                let Some(state_diff_chunk) = maybe_state_diff_chunk?.0 else {
                    if current_state_diff_len == 0 {
                        return Ok(None);
                    } else {
                        return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                            expected_length: target_state_diff_len,
                            possible_lengths: vec![current_state_diff_len],
                        }));
                    }
                };
                prev_result_len = current_state_diff_len;
                if state_diff_chunk.is_empty() {
                    return Err(ParseDataError::BadPeer(BadPeerError::EmptyStateDiffPart));
                }
                // It's cheaper to calculate the length of `state_diff_part` than the length of
                // `result`.
                current_state_diff_len += state_diff_chunk.len();
                unite_state_diffs(&mut result, state_diff_chunk)?;
            }

            if current_state_diff_len != target_state_diff_len {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                    expected_length: target_state_diff_len,
                    possible_lengths: vec![prev_result_len, current_state_diff_len],
                }));
            }

            validate_deprecated_declared_classes_non_conflicting(&result)?;
            Ok(Some((result, block_number)))
        }
        .boxed()
    }
```

**File:** crates/apollo_storage/src/header.rs (L98-107)
```rust
    /// The state diff commitment, if available.
    pub state_diff_commitment: Option<StateDiffCommitment>,
    /// The transaction commitment, if available.
    pub transaction_commitment: Option<TransactionCommitment>,
    /// The event commitment, if available.
    pub event_commitment: Option<EventCommitment>,
    /// The receipt commitment, if available.
    pub receipt_commitment: Option<ReceiptCommitment>,
    /// The length of the state diff, if available.
    pub state_diff_length: Option<usize>,
```

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L30-42)
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
}
```

**File:** crates/apollo_storage/src/state/mod.rs (L181-187)
```rust
    fn get_state_diff(&self, block_number: BlockNumber) -> StorageResult<Option<ThinStateDiff>> {
        let state_diff_location = self.get_state_diff_location(block_number)?;
        match state_diff_location {
            None => Ok(None),
            Some(location) => Ok(Some(self.get_state_diff_from_location(location)?)),
        }
    }
```

**File:** crates/apollo_p2p_sync/src/server/mod.rs (L246-259)
```rust
#[async_trait]
impl FetchBlockData for StateDiffChunk {
    async fn fetch_block_data(
        block_number: BlockNumber,
        txn: &StorageTxn<'_, db::RO>,
        _class_manager_client: &mut SharedClassManagerClient,
    ) -> Result<Vec<Self>, P2pSyncServerError> {
        let thin_state_diff =
            txn.get_state_diff(block_number)?.ok_or(P2pSyncServerError::BlockNotFound {
                block_hash_or_number: BlockHashOrNumber::Number(block_number),
            })?;
        Ok(split_thin_state_diff(thin_state_diff))
    }
}
```

**File:** crates/apollo_committer/src/committer.rs (L165-180)
```rust
        let state_diff_commitment = match state_diff_commitment {
            Some(commitment) => {
                if self.config.verify_state_diff_hash {
                    let calculated_commitment = calculate_state_diff_hash(&state_diff);
                    if commitment != calculated_commitment {
                        return Err(CommitterError::StateDiffHashMismatch {
                            provided_commitment: commitment,
                            calculated_commitment,
                            height,
                        });
                    }
                }
                commitment
            }
            None => calculate_state_diff_hash(&state_diff),
        };
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-357)
```rust
    let concatenated_counts = concat_counts(
        transactions_data.len(),
        event_leaf_elements.len(),
        state_diff.len(),
        l1_da_mode,
    );

    let n_txs = transactions_data.len();
    let n_events = event_leaf_elements.len();
    let state_diff_length = state_diff.len();

    // Spawn tasks for parallel execution; each measures its own duration.
    let transaction_task = spawn_measured_task(move || {
        calculate_transaction_commitment::<Poseidon>(&transaction_leaf_elements)
    });

    let event_task =
        spawn_measured_task(move || calculate_event_commitment::<Poseidon>(&event_leaf_elements));

    let receipt_task =
        spawn_measured_task(move || calculate_receipt_commitment::<Poseidon>(&receipt_elements));

    let state_diff_task = spawn_measured_task(move || calculate_state_diff_hash(&state_diff));

    // Wait for all tasks to complete.
    let (
        (transaction_commitment, transaction_duration),
        (event_commitment, event_duration),
        (receipt_commitment, receipt_duration),
        (state_diff_commitment, state_diff_duration),
    ) = tokio::try_join!(transaction_task, event_task, receipt_task, state_diff_task)
        .expect("Failed to join block commitments tasks.");

    let commitments = BlockHeaderCommitments {
        transaction_commitment,
        event_commitment,
        receipt_commitment,
        state_diff_commitment,
        concatenated_counts,
    };
```
