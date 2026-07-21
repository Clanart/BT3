### Title
Validator's `commitment_state_diff` Not Trimmed When `execution_data` Is Trimmed in `BlockExecutionArtifacts::new`, Causing Wrong State Diff Commitment and State Root - (File: `crates/apollo_batcher/src/block_builder.rs`)

### Summary

In validator mode, when the proposer sends `final_n_executed_txs < N` (where N is the number of transactions the validator already executed), `remove_last_txs` correctly trims `execution_data` but the `commitment_state_diff` sourced from `BlockExecutionSummary::state_diff` is never trimmed. `BlockExecutionArtifacts::new` then computes `state_diff_commitment` and `concatenated_counts` from the oversized state diff, producing a wrong block hash and wrong state diff stored in storage on every validator node that hits this path.

### Finding Description

In `BlockBuilder::finalize_block` the executor's `close_block()` is called first, returning a `BlockExecutionSummary` whose `state_diff` field accumulates the state changes of **all** N executed transactions. Only afterward is `remove_last_txs` called on `execution_data` to strip the trailing `N − final_n_executed_txs` transactions: [1](#0-0) 

The trimmed `execution_data` and the **untrimmed** `block_summary` are then both passed to `BlockExecutionArtifacts::new`: [2](#0-1) 

Inside `new`, `calculate_block_commitments` receives:
- `transactions_data` derived from the **trimmed** `execution_data.execution_infos_and_signatures` (only K transactions)
- `ThinStateDiff::from(commitment_state_diff.clone())` derived from the **untrimmed** `block_summary.state_diff` (N transactions' state changes) [3](#0-2) 

`calculate_block_commitments` uses `state_diff.len()` for `state_diff_length` inside `concat_counts` and hashes the full oversized diff for `state_diff_commitment`: [4](#0-3) 

Both values are embedded in `partial_block_hash_components` and stored in `BlockExecutionArtifacts`. When `decision_reached` is called on the validator node it extracts both the wrong `state_diff_commitment` and the wrong `thin_state_diff()`: [5](#0-4) 

The wrong `thin_state_diff` is written to storage and the wrong `partial_block_hash_components` (containing the wrong `state_diff_commitment` and `concatenated_counts`) are used to derive the partial block hash.

The code comment at the trim site explicitly acknowledges the scenario:

> "This can happen if the proposer sends some transactions but closes the block before including them, while the validator already executed those transactions." [6](#0-5) 

### Impact Explanation

**Critical — Wrong state, storage value, and block commitment from execution logic.**

On every validator node where `N > final_n_executed_txs`:

1. `commitment_state_diff` contains extra storage writes, nonce updates, and class-hash updates from transactions T(K+1)…TN that are **not** in the block.
2. `state_diff_commitment` (Poseidon hash of the wrong diff) diverges from the proposer's value, corrupting the `partial_block_hash_components` and ultimately the block hash.
3. `concatenated_counts` encodes the wrong `state_diff_length`, further corrupting the block hash.
4. The wrong `ThinStateDiff` is written to `apollo_storage` via `commit_proposal_and_block`, so the Patricia Merkle Tree is updated with phantom state changes, producing a wrong global state root.
5. All downstream consumers of the state root — proof inputs, SNOS, L1 state updates — receive the wrong value.

### Likelihood Explanation

The trigger is fully unprivileged. Any proposer can:
1. Stream N transactions to validators.
2. Send a `Finish` message with `final_n_executed_txs = K < N`.

Because the validator's concurrent executor pipeline may have already processed all N transactions before the `Finish` message is handled, the divergence occurs on every validator node in that round. The code comment confirms this is a known, expected execution path.

### Recommendation

After `remove_last_txs` trims `execution_data`, the `commitment_state_diff` must be recomputed to reflect only the first K transactions. Two approaches:

1. **Re-derive from trimmed execution infos**: Reconstruct the `CommitmentStateDiff` by replaying only the state changes recorded in the K retained `TransactionExecutionInfo` entries (nonces, storage writes, class hashes).
2. **Executor-level support**: Extend the blockifier's `TransactionExecutor` with a `close_block_at(k)` variant that returns the partial state diff for the first K transactions, avoiding the need to re-derive it externally.

Until fixed, `BlockExecutionArtifacts::new` should assert that `execution_data.execution_infos_and_signatures.len() == final_n_executed_txs` **before** computing commitments, so the inconsistency is caught at runtime rather than silently producing a wrong block.

### Proof of Concept

```
Proposer streams: T1, T2, T3, T4, T5  →  validator executor
Proposer sends:   Finish { final_n_executed_txs: 3 }

Validator executor state after close_block():
  block_summary.state_diff = ΔT1 ∪ ΔT2 ∪ ΔT3 ∪ ΔT4 ∪ ΔT5   ← includes phantom ΔT4, ΔT5

finalize_block(block_summary, Some(3)):
  remove_last_txs([T4, T5])  →  execution_data = {T1, T2, T3}  ✓
  commitment_state_diff       = ΔT1∪ΔT2∪ΔT3∪ΔT4∪ΔT5           ✗ (not trimmed)

BlockExecutionArtifacts::new:
  transactions_data           = [T1, T2, T3]                    ✓
  state_diff for commitment   = ΔT1∪ΔT2∪ΔT3∪ΔT4∪ΔT5           ✗

  state_diff_commitment       = Poseidon(wrong diff)            ✗  ≠ proposer's value
  state_diff_length           = len(wrong diff)                 ✗  ≠ proposer's value
  concatenated_counts         = encode(3 txs, events, wrong_len)✗

decision_reached on validator:
  thin_state_diff stored      = ΔT1∪ΔT2∪ΔT3∪ΔT4∪ΔT5           ✗  → wrong state root
  partial_block_hash_components used → wrong block hash         ✗
```

### Citations

**File:** crates/apollo_batcher/src/block_builder.rs (L143-183)
```rust
    pub async fn new(
        BlockExecutionSummary {
            state_diff: commitment_state_diff,
            compressed_state_diff,
            bouncer_weights,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            block_info,
        }: BlockExecutionSummary,
        execution_data: BlockTransactionExecutionData,
        final_n_executed_txs: usize,
    ) -> Self {
        let l1_da_mode = L1DataAvailabilityMode::from_use_kzg_da(block_info.use_kzg_da);
        let transactions_data =
            prepare_txs_hashing_data(&execution_data.execution_infos_and_signatures);
        // TODO(Ayelet): Remove the clones.
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
        record_and_log_block_commitment_measurements(block_info.block_number, measurements);
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
        let l2_gas_used = execution_data.l2_gas_used();
        Self {
            execution_data,
            commitment_state_diff,
            compressed_state_diff,
            bouncer_weights,
            l2_gas_used,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            final_n_executed_txs,
            partial_block_hash_components,
        }
    }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L447-458)
```rust
        let mut execution_data = std::mem::take(&mut self.execution_data);
        if let Some(final_n_executed_txs) = final_n_executed_txs {
            // Remove the transactions that were executed, but eventually not included in the block.
            // This can happen if the proposer sends some transactions but closes the block before
            // including them, while the validator already executed those transactions.
            let remove_tx_hashes: Vec<TransactionHash> =
                self.block_txs[final_n_executed_txs..].iter().map(|tx| tx.tx_hash()).collect();
            execution_data.remove_last_txs(&remove_tx_hashes);
        }
        Ok(BlockExecutionArtifacts::new(block_summary, execution_data, final_n_executed_txs_nonopt)
            .await)
    }
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-341)
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

```

**File:** crates/apollo_batcher/src/batcher.rs (L767-801)
```rust
        let state_diff = block_execution_artifacts.thin_state_diff();
        let n_txs = u64::try_from(block_execution_artifacts.tx_hashes().len())
            .expect("Number of transactions should fit in u64");
        let n_rejected_txs =
            u64::try_from(block_execution_artifacts.execution_data.rejected_tx_hashes.len())
                .expect("Number of rejected transactions should fit in u64");
        let n_reverted_count = u64::try_from(
            block_execution_artifacts
                .execution_data
                .execution_infos_and_signatures
                .values()
                .filter(|(info, _)| info.revert_error.is_some())
                .count(),
        )
        .expect("Number of reverted transactions should fit in u64");
        let partial_block_hash_components =
            block_execution_artifacts.partial_block_hash_components();
        let state_diff_commitment =
            partial_block_hash_components.header_commitments.state_diff_commitment;
        let parent_proposal_commitment = self.get_parent_proposal_commitment(height)?;
        self.commit_proposal_and_block(
            height,
            state_diff.clone(),
            block_execution_artifacts.address_to_nonce(),
            block_execution_artifacts.execution_data.consumed_l1_handler_tx_hashes,
            block_execution_artifacts.execution_data.rejected_tx_hashes,
            StorageCommitmentBlockHash::Partial(partial_block_hash_components),
        )
        .await?;

        self.write_commitment_results_and_add_new_task(
            height,
            state_diff.clone(), // TODO(Nimrod): Remove the clone here.
            Some(state_diff_commitment),
        )
```
