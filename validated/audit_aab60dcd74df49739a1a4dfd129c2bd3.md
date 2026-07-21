### Title
Hardcoded `Felt::ZERO` for L2 Gas Consumed in Receipt Commitment Produces Wrong Block Hash - (File: `crates/starknet_api/src/block_hash/receipt_commitment.rs`)

### Summary

The `chain_gas_consumed` function in `receipt_commitment.rs` hardcodes L2 gas consumed to `Felt::ZERO` instead of reading `gas_consumed.l2_gas` from the actual `GasVector`. Because the receipt commitment feeds directly into `calculate_block_hash`, every block containing a v3 transaction that consumed non-zero L2 gas carries a structurally wrong receipt commitment and therefore a wrong block hash.

### Finding Description

`chain_gas_consumed` is the leaf-level function that serialises per-transaction gas into the receipt Patricia tree:

```rust
// crates/starknet_api/src/block_hash/receipt_commitment.rs  lines 86-91
fn chain_gas_consumed(hash_chain: HashChain, gas_consumed: &GasVector) -> HashChain {
    hash_chain
        .chain(&Felt::ZERO)               // L2 gas consumed  ← hardcoded
        .chain(&gas_consumed.l1_gas.into())
        .chain(&gas_consumed.l1_data_gas.into())
}
```

`GasVector` carries three independent fields — `l1_gas`, `l2_gas`, and `l1_data_gas`. The function silently discards `gas_consumed.l2_gas` and substitutes the constant `Felt::ZERO`. The inline comment acknowledges the substitution ("In the current RPC: always 0") but does not gate it on a version check or assert that the field is actually zero.

The call chain that propagates this wrong value into the block hash is:

1. `chain_gas_consumed` → wrong leaf value
2. `calculate_receipt_hash` (line 45) → wrong per-transaction receipt hash
3. `calculate_receipt_commitment` (line 33) → wrong Patricia root
4. `BlockHeaderCommitments.receipt_commitment` populated in `calculate_block_commitments` (line 338)
5. `calculate_block_hash` (line 264) chains `block_commitments.receipt_commitment.0` into the Poseidon hash

For v3 transactions executed under `GasVectorComputationMode::All`, the blockifier tracks and charges real L2 gas. The OS Cairo code confirms L2 gas is a first-class resource bound:

```cairo
// crates/apollo_starknet_os_program/.../transaction_impls.cairo  lines 75-78
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```

When `gas_consumed.l2_gas > 0`, the receipt hash computed by the sequencer diverges from the hash that any independent verifier (proof system, state-sync peer, RPC client) would compute if it used the actual consumed value. The resulting `receipt_commitment` is wrong, and the block hash derived from it is wrong.

This is the direct sequencer analog of the external report's hardcoded pool fee: just as `_usdDeposit` and `_withdrawDeposit` hardcode `500` instead of reading the fee from `hypervisorData`, `chain_gas_consumed` hardcodes `Felt::ZERO` instead of reading `gas_consumed.l2_gas`. In both cases the hardcoded value is acknowledged but not fixed, and in both cases the hardcoded value silently diverges from the true runtime value.

### Impact Explanation

A wrong `receipt_commitment` propagates into `calculate_block_hash` and produces a wrong block hash. Any downstream consumer that independently recomputes the receipt commitment — including the proof manager, state-sync peers, and the transaction prover — will disagree with the sequencer's block hash. This satisfies:

- **Critical – Wrong receipt from blockifier/execution logic for accepted input**: every accepted v3 transaction with non-zero L2 gas consumption produces a receipt hash that does not commit to the actual gas consumed.
- **High – RPC execution / tracing returns an authoritative-looking wrong value**: `starknet_getTransactionReceipt` and `starknet_getBlockWithReceipts` will return a receipt commitment that does not match independently computed values.

### Likelihood Explanation

Any v3 transaction submitted with non-zero `l2_gas` resource bounds and executed under `GasVectorComputationMode::All` triggers the discrepancy. This is the normal execution mode for current Starknet v0.13.4+ blocks. No special privilege or adversarial setup is required; ordinary user transactions are sufficient.

### Recommendation

Replace the hardcoded constant with the actual field value, gated on a version check if the protocol specification requires zero for older versions:

```rust
fn chain_gas_consumed(hash_chain: HashChain, gas_consumed: &GasVector) -> HashChain {
    hash_chain
        .chain(&gas_consumed.l2_gas.into())   // use actual L2 gas consumed
        .chain(&gas_consumed.l1_gas.into())
        .chain(&gas_consumed.l1_data_gas.into())
}
```

If the Starknet specification mandates zero for L2 gas consumed in receipts until a specific version, add an explicit assertion (`assert_eq!(gas_consumed.l2_gas, GasAmount(0))`) so that any future non-zero value is caught at the point of commitment rather than silently discarded.

### Proof of Concept

1. Submit a v3 `INVOKE` transaction with non-zero `l2_gas` resource bounds to a sequencer running `GasVectorComputationMode::All`.
2. After the block is sealed, independently compute the receipt hash for that transaction using the actual `gas_consumed.l2_gas` value from the blockifier's `TransactionExecutionInfo`.
3. Compare against the receipt hash stored in the block's `receipt_commitment`.
4. The two values will differ by exactly the contribution of `gas_consumed.l2_gas` to the Poseidon chain, demonstrating that the committed receipt does not match the executed receipt.
5. Because `receipt_commitment` feeds into `calculate_block_hash`, the block hash produced by the sequencer will not match the hash computed by any independent verifier that uses the correct L2 gas value. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/starknet_api/src/block_hash/receipt_commitment.rs (L45-53)
```rust
fn calculate_receipt_hash(receipt_element: &ReceiptElement) -> Felt {
    let hash_chain = HashChain::new()
        .chain(&receipt_element.transaction_hash)
        .chain(&receipt_element.transaction_output.actual_fee.0.into())
        .chain(&calculate_messages_sent_hash(&receipt_element.transaction_output.messages_sent))
        .chain(&get_revert_reason_hash(&receipt_element.transaction_output.execution_status));
    chain_gas_consumed(hash_chain, &receipt_element.transaction_output.gas_consumed)
        .get_poseidon_hash()
}
```

**File:** crates/starknet_api/src/block_hash/receipt_commitment.rs (L81-91)
```rust
// Chains:
// L2 gas consumed (In the current RPC: always 0),
// L1 gas consumed (In the current RPC:
//      L1 gas consumed for calldata + L1 gas consumed for steps and builtins.
// L1 data gas consumed (In the current RPC: L1 data gas consumed for blob).
fn chain_gas_consumed(hash_chain: HashChain, gas_consumed: &GasVector) -> HashChain {
    hash_chain
        .chain(&Felt::ZERO) // L2 gas consumed
        .chain(&gas_consumed.l1_gas.into())
        .chain(&gas_consumed.l1_data_gas.into())
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L260-265)
```rust
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L330-340)
```rust
    let transaction_task = spawn_measured_task(move || {
        calculate_transaction_commitment::<Poseidon>(&transaction_leaf_elements)
    });

    let event_task =
        spawn_measured_task(move || calculate_event_commitment::<Poseidon>(&event_leaf_elements));

    let receipt_task =
        spawn_measured_task(move || calculate_receipt_commitment::<Poseidon>(&receipt_elements));

    let state_diff_task = spawn_measured_task(move || calculate_state_diff_hash(&state_diff));
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L75-78)
```text
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```
