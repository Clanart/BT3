### Title
Sequencer Balance Overflow Causes Panic-Halt in Concurrent Fee Transfer Commit, Corrupting State Diff Storage Values - (File: crates/blockifier/src/concurrency/fee_utils.rs)

### Summary

In `add_fee_to_sequencer_balance`, the sequencer's Uint256 fee-token balance is read from storage as two `Felt` values and converted to `u128` via `.to_u128().expect(...)`. If the high limb of the sequencer's on-chain balance is a `Felt` value that exceeds `u128::MAX` (i.e., a felt in the range `[2^128, P-1]`), the `.expect()` call panics, halting the sequencer node mid-block-commit. This is the direct sequencer analog of the Moloch token-overflow bug: an artificially inflated or maliciously crafted token balance causes the processing function to abort, preventing block finalization and corrupting the in-progress state diff.

### Finding Description

In concurrent execution mode, fee transfers are executed with a placeholder zero sequencer balance. At commit time, `complete_fee_transfer_flow` is called from `commit_tx` in `worker_logic.rs`. It reads the real sequencer balance from state and calls `add_fee_to_sequencer_balance`.

Inside `add_fee_to_sequencer_balance`:

```rust
let sequencer_balance_low_as_u128 =
    low.to_u128().expect("sequencer balance low should be u128");
let sequencer_balance_high_as_u128 =
    high.to_u128().expect("sequencer balance high should be u128");
```

`Felt` is a 252-bit field element. `to_u128()` returns `None` for any value `>= 2^128`. If the sequencer's fee-token storage slot contains a felt value outside the `[0, 2^128)` range — which can happen if a malicious or buggy ERC-20 contract writes an oversized felt to the balance slot — the `.expect()` panics.

Additionally, even for valid u128 values, the high-limb overflow check:

```rust
assert!(
    !overflow_high,
    "The sequencer balance overflowed when adding the fee. This should not happen."
);
```

also panics on overflow, halting the sequencer. This assert fires when `sequencer_balance_high >= 2^128 - 1` and a carry propagates from the low limb addition.

The panic occurs inside `commit_tx` after the bouncer has already accepted the transaction, meaning the block-building worker thread aborts without cleanly rolling back the partial `state_diff` mutations already applied to `execution_output.state_diff`. The `StateMaps` written by `add_fee_to_sequencer_balance` (lines 144–156) are partially applied before the panic, leaving the state diff in an inconsistent state. [1](#0-0) 

The `complete_fee_transfer_flow` is called from `commit_tx` at line 367: [2](#0-1) 

The state diff being mutated is `execution_output.state_diff`, which feeds directly into the block's `ThinStateDiff` and subsequently into `calculate_state_diff_hash` and `calculate_block_commitments`: [3](#0-2) 

### Impact Explanation

**Critical — Wrong state, receipt, or storage value from blockifier/syscall/execution logic for accepted input.**

1. **Sequencer halt**: The panic in `add_fee_to_sequencer_balance` propagates up through `commit_tx`, crashing the concurrent worker thread. In production, this halts block finalization for any block containing a transaction whose fee transfer triggers the overflow path.

2. **Corrupted state diff / wrong storage value**: The `state_diff` (`StateMaps`) passed by mutable reference to `add_fee_to_sequencer_balance` may have already received a partial write (e.g., the high-limb update at line 144–148) before the panic on the low-limb conversion or the overflow assert. This leaves the sequencer's fee-token balance storage entry in the state diff with an incorrect value — the high limb updated but the low limb not, or vice versa. This incorrect storage value propagates into `calculate_state_diff_hash`, producing a wrong `StateDiffCommitment`, which in turn corrupts `concatenated_counts` and the final `block_hash`. [4](#0-3) 

### Likelihood Explanation

**Medium.** The trigger requires the sequencer's fee-token balance high limb to be a felt value `>= 2^128`, or the combined Uint256 balance to be near `2^256 - 1`. This cannot happen through normal fee accumulation (fees are `u128`), but can be induced by:

- A malicious ERC-20 token contract (e.g., a custom fee token on a non-mainnet chain) that directly writes an oversized felt to the sequencer's balance storage slot via `storage_write`.
- A buggy token contract that performs unchecked arithmetic and wraps a felt value above `2^128` into the balance slot.

On mainnet with the canonical STRK/ETH token contracts this is not reachable. On testnets or custom deployments with non-standard fee tokens it is reachable by any user who can invoke the fee token contract.

### Recommendation

1. Replace the panicking `.expect()` calls with graceful error handling. If the balance felt is out of u128 range, treat it as a broken token (analogous to the Moloch `unsafeInternalTransfer` recommendation) and skip the balance update or use saturating arithmetic, logging the anomaly.

2. Replace the `assert!(!overflow_high, ...)` with a recoverable error path that does not crash the worker thread.

3. Add a pre-commit invariant check that validates the sequencer balance felts are within `[0, 2^128)` before entering `add_fee_to_sequencer_balance`, returning a `CommitResult::Error` rather than panicking.

### Proof of Concept

1. Deploy a custom fee token contract on a test network where the sequencer address's balance high-slot (`ERC20_balances[sequencer_address].high`) is set to `2^128` (a valid felt, but not a valid u128).
2. Submit any fee-paying transaction from a non-sequencer account.
3. In concurrent execution mode, `concurrency_execute_fee_transfer` runs with placeholder zero balance.
4. At commit time, `complete_fee_transfer_flow` → `add_fee_to_sequencer_balance` is called.
5. `high.to_u128()` returns `None` for the felt `2^128`.
6. `.expect("sequencer balance high should be u128")` panics.
7. The worker thread aborts; the block cannot be finalized; the sequencer halts.

The existing test `test_add_fee_to_sequencer_balance` only tests cases where both limbs are valid `u128` values (`felt!(u128::MAX)`, `felt!(5_u128)`, etc.) and does not cover the case where a limb felt exceeds `u128::MAX`. [5](#0-4)

### Citations

**File:** crates/blockifier/src/concurrency/fee_utils.rs (L119-129)
```rust
    let sequencer_balance_low_as_u128 =
        low.to_u128().expect("sequencer balance low should be u128");
    let sequencer_balance_high_as_u128 =
        high.to_u128().expect("sequencer balance high should be u128");
    let (new_value_low, overflow_low) = sequencer_balance_low_as_u128.overflowing_add(actual_fee.0);
    let (new_value_high, overflow_high) =
        sequencer_balance_high_as_u128.overflowing_add(overflow_low.into());
    assert!(
        !overflow_high,
        "The sequencer balance overflowed when adding the fee. This should not happen."
    );
```

**File:** crates/blockifier/src/concurrency/fee_utils.rs (L144-157)
```rust
    if sequencer_balance_high_as_u128 != new_value_high {
        // Update the high balance only if it has changed.
        state_diff
            .storage
            .insert((fee_token_address, sequencer_balance_key_high), Felt::from(new_value_high));
    }

    if sequencer_balance_low_as_u128 != new_value_low {
        // Update the low balance only if it has changed.
        state_diff
            .storage
            .insert((fee_token_address, sequencer_balance_key_low), Felt::from(new_value_low));
    }
    state.apply_writes(&writes, &ContractClassMapping::default());
```

**File:** crates/blockifier/src/concurrency/worker_logic.rs (L367-373)
```rust
            complete_fee_transfer_flow(
                &tx_context,
                tx_execution_info,
                &mut execution_output.state_diff,
                &mut tx_versioned_state,
                tx.as_ref(),
            );
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

**File:** crates/blockifier/src/concurrency/fee_utils_test.rs (L66-70)
```rust
#[rstest]
#[case::no_overflow(Fee(50_u128), felt!(100_u128), Felt::ZERO)]
#[case::overflow(Fee(150_u128), felt!(u128::MAX), felt!(5_u128))]
#[case::overflow_edge_case(Fee(500_u128), felt!(u128::MAX), felt!(u128::MAX-1))]
pub fn test_add_fee_to_sequencer_balance(
```
