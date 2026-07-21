### Title
Configurable `verify_state_diff_hash = false` Allows Caller-Supplied `StateDiffCommitment` to Diverge from Actual State Diff, Corrupting Stored Block Hash - (`crates/apollo_committer/src/committer.rs`)

---

### Summary

`commit_block_inner` in the Apollo committer accepts a caller-supplied `state_diff_commitment` and stores it as the authoritative `StateDiffHash` metadata for the block. The cross-check that verifies this commitment actually matches the `state_diff` being committed to the Patricia trie is gated behind a configurable boolean flag `verify_state_diff_hash`. When that flag is `false`, the provided commitment is stored verbatim without any validation. The stored `StateDiffHash` is then used as an input to `calculate_block_hash`, so a mismatched commitment produces a wrong block hash that is persisted to storage and served authoritatively by RPC and the prover pipeline.

---

### Finding Description

`CommitterConfig` exposes a public `verify_state_diff_hash: bool` field. [1](#0-0) 

The production deployment config substitutes this value at deploy time via a placeholder: [2](#0-1) 

Inside `commit_block_inner`, when `state_diff_commitment` arrives as `Some(commitment)`, the only guard that checks whether `commitment` actually equals `calculate_state_diff_hash(&state_diff)` is wrapped in `if self.config.verify_state_diff_hash`: [3](#0-2) 

When the flag is `false`, the branch is skipped entirely and `commitment` (the caller-supplied value) is used directly. That value is then written to the Patricia forest metadata as `ForestMetadataType::StateDiffHash`: [4](#0-3) 

The `StateDiffHash` metadata is later read back and fed into `calculate_block_hash` as `block_commitments.state_diff_commitment.0.0`: [5](#0-4) 

The Patricia trie itself is updated from the actual `state_diff` (correct global root), but the block hash is computed from the wrong `state_diff_commitment`. The two values are now inconsistent: the global root reflects the real state, but the block hash does not.

The analog to BribeVault M-14 is direct: just as `transferBribes` accepts `distributions[i].token` and `amount` from the admin without verifying they match what is stored in `rewardToBribes[rewardIdentifier]`, `commit_block_inner` accepts `state_diff_commitment` from the batcher without verifying it matches the actual `state_diff` when `verify_state_diff_hash = false`. In both cases the validation is not enforced by the protocol itself — it is delegated to an off-chain/operator trust assumption.

The test harness itself instantiates the committer with `verify_state_diff_hash: false` as the baseline, confirming this is a live code path: [6](#0-5) 

---

### Impact Explanation

When `verify_state_diff_hash = false` and a `CommitBlockRequest` arrives with `state_diff_commitment = Some(wrong_value)`:

1. `wrong_value` is stored as `ForestMetadataType::StateDiffHash` for that block height.
2. The actual `state_diff` is committed to the Patricia trie, producing the correct `global_root`.
3. `calculate_block_hash` chains `wrong_value` into the Poseidon hash, producing a wrong `BlockHash`.
4. That wrong `BlockHash` is written to storage via `set_global_root_and_block_hash` and cached in `recent_block_hashes_cache`.
5. RPC endpoints that serve `starknet_getBlockWithTxHashes` or `starknet_getBlockHashAndNumber` return the wrong block hash.
6. The prover pipeline reads the stored `StateDiffHash` as a proof input; a wrong value causes the SNOS output to diverge from what validators computed during consensus, breaking proof verification.

This matches **High: RPC execution returns an authoritative-looking wrong value** and **Critical: Wrong state/receipt/commitment from blockifier/syscall/execution logic**. [7](#0-6) 

---

### Likelihood Explanation

The `verify_state_diff_hash` flag is a public, operator-configurable parameter injected at deployment time. The replacer config leaves it as a substitution variable, meaning any deployment that resolves it to `false` (e.g., for performance, for replaying old blocks, or by misconfiguration) opens this path. The batcher's `add_sync_block` path passes `optional_state_diff_commitment` derived from network-received `block_header_commitments`, which is an external input: [8](#0-7) 

If the flag is `false` and the sync source provides a wrong `state_diff_commitment`, the committer stores it without complaint.

---

### Recommendation

Remove the `verify_state_diff_hash` bypass entirely. The check `calculate_state_diff_hash(&state_diff) == commitment` is O(state_diff_size) and is the only on-chain-equivalent guard that ensures the stored `StateDiffHash` is consistent with the Patricia trie update. It should be unconditional whenever a `Some(commitment)` is provided:

```rust
Some(commitment) => {
    let calculated = calculate_state_diff_hash(&state_diff);
    if commitment != calculated {
        return Err(CommitterError::StateDiffHashMismatch { ... });
    }
    commitment
}
```

If the flag must be retained for legacy/replay scenarios, it should be restricted to `state_diff_commitment = None` paths only (where the committer self-computes the hash), never to paths where a pre-computed commitment is supplied by an external caller. [9](#0-8) 

---

### Proof of Concept

1. Deploy the committer with `committer_config.verify_state_diff_hash: false`.
2. Send a `CommitBlockRequest` with:
   - `state_diff` = a real non-empty `ThinStateDiff` (e.g., one storage update)
   - `state_diff_commitment` = `Some(StateDiffCommitment(PoseidonHash(Felt::from(0xdeadbeef))))`
   - `height` = current committer offset
3. `commit_block_inner` skips the hash check (line 167 branch not taken), stores `0xdeadbeef` as `ForestMetadataType::StateDiffHash`.
4. `calculate_block_hash` chains `0xdeadbeef` into the Poseidon hash, producing a block hash that no honest validator would compute from the same state diff.
5. `write_commitment_results_to_storage` persists this wrong block hash; subsequent RPC calls to `get_block_hash` return it.
6. The prover reads the stored `StateDiffHash = 0xdeadbeef` as a SNOS input; proof verification fails because the actual state diff hashes to a different value. [3](#0-2) [10](#0-9)

### Citations

**File:** crates/apollo_committer_config/src/config.rs (L17-23)
```rust
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Validate)]
pub struct CommitterConfig<C: StorageConfigTrait> {
    pub reader_config: ReaderConfig,
    pub db_path: PathBuf,
    pub storage_config: C,
    pub verify_state_diff_hash: bool,
}
```

**File:** crates/apollo_committer_config/src/config.rs (L47-55)
```rust
impl<C: StorageConfigTrait> Default for CommitterConfig<C> {
    fn default() -> Self {
        Self {
            reader_config: ReaderConfig::default(),
            db_path: "/data/committer".into(),
            storage_config: C::default(),
            verify_state_diff_hash: true,
        }
    }
```

**File:** crates/apollo_deployments/resources/app_configs/replacer_committer_config.json (L17-17)
```json
  "committer_config.verify_state_diff_hash": "$$$_COMMITTER_CONFIG-VERIFY_STATE_DIFF_HASH_$$$"
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

**File:** crates/apollo_committer/src/committer.rs (L210-222)
```rust
        let metadata = HashMap::from([
            (
                ForestMetadataType::CommitmentOffset,
                DbValue(DbBlockNumber(next_offset).serialize().to_vec()),
            ),
            (
                ForestMetadataType::StateRoot(DbBlockNumber(height)),
                serialize_felt_no_packing(global_root.0),
            ),
            (
                ForestMetadataType::StateDiffHash(DbBlockNumber(height)),
                serialize_felt_no_packing(state_diff_commitment.0.0),
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

**File:** crates/apollo_committer/src/committer_test.rs (L72-74)
```rust
async fn new_test_committer() -> ApolloTestCommitter {
    Committer::new(CommitterConfig { verify_state_diff_hash: false, ..Default::default() }).await
}
```

**File:** crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs (L258-293)
```rust
            // Get the final commitment.
            let FinalBlockCommitment { height, block_hash, global_root } =
                Self::finalize_commitment_output(
                    storage_reader.clone(),
                    commitment_task_output,
                    should_finalize_block_hash,
                )?;

            // Verify the first new block hash matches the configured block hash.
            if let Some(FirstBlockWithPartialBlockHash {
                block_number,
                block_hash: expected_block_hash,
                ..
            }) = first_block_with_partial_block_hash.as_ref()
            {
                if height == *block_number {
                    assert_eq!(
                        *expected_block_hash,
                        block_hash.expect(
                            "The block hash of the first new block should be finalized and \
                             therefore set."
                        ),
                        "The calculated block hash of the first new block ({block_hash:?}) does \
                         not match the configured block hash ({expected_block_hash:?})"
                    );
                }
            }

            // Add block hash to cache.
            if let Some(block_hash) = block_hash {
                self.recent_block_hashes_cache.put(height, block_hash);
            }

            // Write the block hash and global root to storage.
            storage_writer.set_global_root_and_block_hash(height, global_root, block_hash)?;
            GLOBAL_ROOT_HEIGHT.increment(1);
```

**File:** crates/apollo_batcher/src/batcher.rs (L720-742)
```rust
        let optional_state_diff_commitment = match &storage_commitment_block_hash {
            StorageCommitmentBlockHash::ParentHash(_) => None,
            StorageCommitmentBlockHash::Partial(PartialBlockHashComponents {
                ref header_commitments,
                ..
            }) => Some(header_commitments.state_diff_commitment),
        };

        self.commit_proposal_and_block(
            height,
            state_diff.clone(),
            address_to_nonce,
            l1_transaction_hashes.iter().copied().collect(),
            Default::default(),
            storage_commitment_block_hash,
        )
        .await?;

        self.write_commitment_results_and_add_new_task(
            height,
            state_diff,
            optional_state_diff_commitment,
        )
```
