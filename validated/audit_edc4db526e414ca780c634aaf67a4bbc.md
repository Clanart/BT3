### Title
P2P State Diff Sync Terminates on `state_diff_length` Without Verifying Assembled Content Against Header's `state_diff_commitment` - (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

---

### Summary

`StateDiffStreamBuilder::parse_data_for_block` accumulates peer-supplied `StateDiffChunk` messages until their aggregate `len()` equals the `state_diff_length` stored in the block header, then writes the assembled `ThinStateDiff` directly to storage. It never computes `calculate_state_diff_hash` on the assembled result and never compares it against the `state_diff_commitment` that is also present in the same stored header. A malicious P2P peer can therefore supply chunks whose total element count matches the header's `state_diff_length` but whose content is entirely different from the committed state diff, causing the syncing node to persist a wrong state diff and serve wrong storage values, class hashes, and nonces from its RPC.

---

### Finding Description

**Analog mapping.** The original report describes two code paths that read the same `feedIds` field differently: `_opPoke` uses Solidity's ABI-compliant calldata resolution (the "commitment path"), while `LibSchnorrData.loadFeedId` uses a hardcoded byte offset (the "verification path"). An attacker crafts calldata so the commitment path sees the legitimate `feedIds` while the verification path sees attacker-chosen bytes, making the commitment check pass while the actual Schnorr verification fails.

The exact structural analog here is:

| Original | Sequencer analog |
|---|---|
| `_opPoke` reads `feedIds` via Solidity ABI (commitment path) | Block header stores `state_diff_commitment = calculate_state_diff_hash(actual_diff)` |
| `LibSchnorrData.loadFeedId` reads `feedIds` via hardcoded offset (verification path) | `parse_data_for_block` terminates on `state_diff_length` (a count, not a hash) |
| Attacker crafts calldata so both paths see different bytes | Attacker sends chunks whose `len()` sum equals `state_diff_length` but whose content differs from the committed diff |
| Commitment check passes; Schnorr verification fails | Length check passes; cryptographic commitment is never checked |

**Exact code path.**

`parse_data_for_block` reads `state_diff_length` from the stored header:

```rust
let target_state_diff_len = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("A header with number lower than the header marker is missing")
    .state_diff_length          // ← only this field is used
    .ok_or(P2pSyncClientError::OldHeaderInStorage { ... })?;
``` [1](#0-0) 

It then accumulates chunks until the count matches:

```rust
while current_state_diff_len < target_state_diff_len {
    ...
    current_state_diff_len += state_diff_chunk.len();
    unite_state_diffs(&mut result, state_diff_chunk)?;
}
``` [2](#0-1) 

After the loop, the only additional check is for duplicate deprecated declared classes:

```rust
validate_deprecated_declared_classes_non_conflicting(&result)?;
Ok(Some((result, block_number)))
``` [3](#0-2) 

**The missing check.** The same `StorageBlockHeader` that supplies `state_diff_length` also carries `state_diff_commitment: Option<StateDiffCommitment>`: [4](#0-3) 

`calculate_state_diff_hash` is the canonical function that produces this commitment: [5](#0-4) 

`parse_data_for_block` reads `state_diff_length` from the header but never reads `state_diff_commitment` and never calls `calculate_state_diff_hash` on the assembled result. The assembled `ThinStateDiff` is returned and immediately written to storage:

```rust
storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
``` [6](#0-5) 

`append_state_diff` fans the diff out into the live state tables (`deployed_contracts`, `contract_storage`, `nonces`, `compiled_class_hash`): [7](#0-6) 

These are the exact tables that RPC state-reader queries consult.

**Committer path does not close the window.** The committer's `commit_or_load` does have a `verify_state_diff_hash` guard: [8](#0-7) 

However: (a) this guard is a configurable flag (`verify_state_diff_hash: bool`) that can be set to `false`; (b) even when `true`, the committer is invoked asynchronously after the state diff is already persisted to the live state tables, so there is a window during which RPC queries return the wrong values; (c) for pure syncing nodes that do not run the batcher, the committer may not be invoked at all on the P2P-synced state diff. [9](#0-8) 

---

### Impact Explanation

A malicious P2P peer that knows the `state_diff_length` of a target block (available from the header it already sent) can craft `StateDiffChunk` messages whose aggregate element count equals `state_diff_length` but whose content is attacker-chosen. The victim node stores the wrong diff into its live state tables. Subsequent `starknet_getStorageAt`, `starknet_getClassHashAt`, `starknet_getNonce`, `starknet_getStateUpdate`, fee estimation, and simulation calls all read from those tables and return authoritative-looking wrong values. If the committer is configured with `verify_state_diff_hash: false`, the wrong diff is also committed to the Patricia trie, producing a wrong global state root that propagates to block hash computation and proof inputs.

This matches: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value**, and potentially **Critical — Wrong state, storage value, or class hash from blockifier/syscall/execution logic**.

---

### Likelihood Explanation

The trigger requires a malicious P2P peer. This is not a privileged role: any node that connects to a peer it does not fully control is exposed. The crafted chunks need only satisfy the element-count check, which is trivially achievable. No cryptographic material needs to be forged.

---

### Recommendation

After the accumulation loop completes, read `state_diff_commitment` from the stored header and verify the assembled diff:

```rust
let header = storage_reader.begin_ro_txn()?.get_block_header(block_number)? ...;
let target_state_diff_len = header.state_diff_length.ok_or(...)?;
// ... accumulate chunks ...
if let Some(expected_commitment) = header.state_diff_commitment {
    let actual_commitment = calculate_state_diff_hash(&result);
    if actual_commitment != expected_commitment {
        return Err(ParseDataError::BadPeer(BadPeerError::InvalidStateDiffCommitment { ... }));
    }
}
```

This mirrors the fix applied in the original report: use the cryptographic commitment (not a structural count) as the authoritative check, exactly as `Committer::commit_or_load` already does when `verify_state_diff_hash` is enabled. [8](#0-7) 

---

### Proof of Concept

1. A syncing node connects to a malicious peer.
2. The malicious peer sends a valid `SignedBlockHeader` for block N with `state_diff_length = 3` and a legitimate `state_diff_commitment` (copied from the real chain).
3. Instead of the real three state diff entries, the peer sends three `ContractDiff` chunks with attacker-chosen `class_hash`, `nonce`, and `storage_diffs` values — the total element count is still 3, satisfying the loop termination condition.
4. `parse_data_for_block` returns `Ok(Some((attacker_diff, block_N)))`.
5. `write_to_storage` calls `append_state_diff(block_N, attacker_diff)`, writing the wrong class hashes, storage slots, and nonces into the live state tables.
6. A subsequent `starknet_getStorageAt(contract, key, block_N)` RPC call returns the attacker-chosen value instead of the real value.
7. If `verify_state_diff_hash` is `false` in the committer config, the wrong diff is also committed to the Patricia trie, producing a wrong `global_root` that is stored in the block header and used as the `new_root` for subsequent block hash calculations and SNOS proof inputs. [10](#0-9) [5](#0-4)

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L34-34)
```rust
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L51-110)
```rust
    fn parse_data_for_block<'a>(
        state_diff_chunks_response_manager: &'a mut ClientResponsesManager<
            DataOrFin<StateDiffChunk>,
        >,
        block_number: BlockNumber,
        storage_reader: &'a StorageReader,
    ) -> BoxFuture<'a, Result<Option<Self::Output>, ParseDataError>> {
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

**File:** crates/apollo_storage/src/state/mod.rs (L621-666)
```rust
        // Write state.
        write_deployed_contracts(
            &thin_state_diff.deployed_contracts,
            inner_txn,
            block_number,
            &deployed_contracts_table,
            &nonces_table,
        )?;
        write_storage_diffs(
            &thin_state_diff.storage_diffs,
            inner_txn,
            block_number,
            &storage_table,
        )?;
        // Must be called after write_deployed_contracts since the nonces are updated there.
        write_nonces(&thin_state_diff.nonces, inner_txn, block_number, &nonces_table)?;

        for (class_hash, _) in &thin_state_diff.class_hash_to_compiled_class_hash {
            let not_declared = declared_classes_block_table.get(inner_txn, class_hash)?.is_none();
            if not_declared {
                declared_classes_block_table.insert(inner_txn, class_hash, &block_number)?;
            }
        }

        write_compiled_class_hashes(
            &thin_state_diff.class_hash_to_compiled_class_hash,
            inner_txn,
            block_number,
            &compiled_class_hash_table,
        )?;

        for class_hash in thin_state_diff.deprecated_declared_classes.iter() {
            // Cairo0 classes can be declared in different blocks. The first block to declare the
            // class is recorded here.
            if deprecated_declared_classes_block_table.get(inner_txn, class_hash)?.is_none() {
                deprecated_declared_classes_block_table.insert(
                    inner_txn,
                    class_hash,
                    &block_number,
                )?;
            }
        }

        // Write state diff.
        let location = self.file_handlers().append_state_diff(&thin_state_diff);
        state_diffs_table.append(inner_txn, &block_number, &location)?;
```

**File:** crates/apollo_committer/src/committer.rs (L265-280)
```rust
        let state_diff_commitment = match state_diff_commitment {
            Some(commitment) => {
                if self.config.verify_state_diff_hash {
                    let calculated_commitment = calculate_state_diff_hash(state_diff);
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
            None => calculate_state_diff_hash(state_diff),
        };
```

**File:** crates/apollo_committer_config/src/config.rs (L17-57)
```rust
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Validate)]
pub struct CommitterConfig<C: StorageConfigTrait> {
    pub reader_config: ReaderConfig,
    pub db_path: PathBuf,
    pub storage_config: C,
    pub verify_state_diff_hash: bool,
}

impl<C: StorageConfigTrait> SerializeConfig for CommitterConfig<C> {
    fn dump(&self) -> BTreeMap<ParamPath, SerializedParam> {
        let mut dump = BTreeMap::from_iter([
            ser_param(
                "verify_state_diff_hash",
                &self.verify_state_diff_hash,
                "If true, the committer will verify the state diff hash.",
                ParamPrivacyInput::Public,
            ),
            ser_param(
                "db_path",
                &self.db_path,
                "Path to the committer storage directory.",
                ParamPrivacyInput::Public,
            ),
        ]);
        dump.extend(prepend_sub_config_name(self.reader_config.dump(), "reader_config"));
        dump.extend(prepend_sub_config_name(self.storage_config.dump(), "storage_config"));
        dump
    }
}

impl<C: StorageConfigTrait> Default for CommitterConfig<C> {
    fn default() -> Self {
        // TODO(Nimrod): Consider adding dynamic config and move `build_storage_tries_concurrently`
        // to it.
        Self {
            reader_config: ReaderConfig::default(),
            db_path: "/data/committer".into(),
            storage_config: C::default(),
            verify_state_diff_hash: true,
        }
    }
```
