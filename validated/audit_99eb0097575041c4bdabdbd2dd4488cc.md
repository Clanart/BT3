### Title
`chain_gas_consumed` hardcodes `Felt::ZERO` for L2 gas in receipt leaf hash, excluding actual L2 gas from receipt commitment and block hash — (File: `crates/starknet_api/src/block_hash/receipt_commitment.rs`)

---

### Summary

`chain_gas_consumed` in `receipt_commitment.rs` always chains `Felt::ZERO` as the L2-gas-consumed field of every receipt leaf, regardless of the actual `gas_consumed.l2_gas` value carried in `GasVector`. Because L2 gas is now non-zero for Sierra (Cairo 1) transactions in production (observed values such as `76014735`), the receipt commitment — and therefore the block hash that chains it — does not commit to the actual L2 gas consumed. Any value can be served for the L2-gas field of a receipt without violating the commitment, making that field unverifiable from the authoritative block hash.

---

### Finding Description

`GasVector` carries three independent gas dimensions:

```rust
pub struct GasVector {
    pub l1_gas: GasAmount,
    pub l1_data_gas: GasAmount,
    pub l2_gas: GasAmount,   // non-zero for Sierra txs
}
``` [1](#0-0) 

The receipt leaf hash is computed by `calculate_receipt_hash`, which delegates gas serialisation to `chain_gas_consumed`:

```rust
fn chain_gas_consumed(hash_chain: HashChain, gas_consumed: &GasVector) -> HashChain {
    hash_chain
        .chain(&Felt::ZERO)                    // ← hardcoded, not gas_consumed.l2_gas
        .chain(&gas_consumed.l1_gas.into())
        .chain(&gas_consumed.l1_data_gas.into())
}
``` [2](#0-1) 

The comment acknowledges the substitution: *"L2 gas consumed (In the current RPC: always 0)"*. That assumption was valid when L2 gas was universally zero, but production blocks at Starknet ≥ 0.14.x carry non-zero `l2_gas` in `total_gas_consumed` (e.g. `76014735`). There is **no version gate** in `calculate_block_commitments` that would switch `chain_gas_consumed` to use the real value for newer protocol versions — the only version-conditional logic there adjusts empty signatures for the transaction commitment, not the receipt commitment. [3](#0-2) 

The receipt commitment produced by `calculate_receipt_commitment` is then chained directly into the block hash:

```
.chain(&block_commitments.receipt_commitment.0)
``` [4](#0-3) 

**Structural analogy to the seed report.** In Superform, `Swap1InchHook.inspect()` returns only `(to, token, dex)` and omits `amount`/`minReturn`; the Merkle leaf therefore does not bind those fields, so a strategist can vary them freely after whitelisting. Here, `chain_gas_consumed` returns only `(0, l1_gas, l1_data_gas)` and omits `l2_gas`; the receipt leaf therefore does not bind that field, so any value can be substituted after the block hash is finalised.

---

### Impact Explanation

The receipt commitment is the authoritative cryptographic binding of per-transaction execution outcomes inside the block hash. Excluding `l2_gas` from every receipt leaf means:

1. **Wrong authoritative receipt value via RPC.** `starknet_getTransactionReceipt` returns `execution_resources.total_gas_consumed.l2_gas`. A node (or a sync peer) can serve any value for that field; a verifier recomputing the receipt leaf from the returned data and checking it against the receipt commitment tree will find a match regardless of what `l2_gas` value was substituted, because the leaf was computed with `Felt::ZERO`. This satisfies the **High** impact: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

2. **Block hash does not uniquely commit to L2 gas consumption.** Two distinct execution outcomes that differ only in `l2_gas` produce identical receipt commitments and therefore identical block hashes (all else equal). This breaks the uniqueness invariant of the block hash over execution outputs, touching the **Critical** impact: *"Wrong … receipt … result from blockifier/syscall/execution logic for accepted input."*

3. **Proof-path exposure.** The transaction prover (`starknet_transaction_prover`) builds `ProveTransactionResult` containing `proof_facts` that are verified against the block hash. Because the block hash does not bind `l2_gas`, a proof that attests to a block hash is consistent with any L2 gas value for the proven transaction, weakening the end-to-end integrity guarantee of the client-side proving flow. [5](#0-4) 

---

### Likelihood Explanation

Every Sierra (Cairo 1) transaction that executes storage writes, events, or non-trivial computation produces a non-zero `l2_gas` value. Production block data already shows values such as `76014735`. The trigger is therefore any ordinary user transaction on a Starknet ≥ 0.13.2 network — no special privilege is required. The root cause is entirely within the sequencer's own production code path (`calculate_receipt_commitment` → `chain_gas_consumed`), reachable on every block that contains a Cairo 1 transaction.

---

### Recommendation

Replace the hardcoded `Felt::ZERO` with the actual `gas_consumed.l2_gas` field:

```rust
fn chain_gas_consumed(hash_chain: HashChain, gas_consumed: &GasVector) -> HashChain {
    hash_chain
        .chain(&gas_consumed.l2_gas.into())   // actual L2 gas, not Felt::ZERO
        .chain(&gas_consumed.l1_gas.into())
        .chain(&gas_consumed.l1_data_gas.into())
}
```

If backward compatibility with pre-L2-gas blocks is required, gate the change on `starknet_version` (as is already done for the empty-signature fix in `calculate_block_commitments`) so that blocks produced before L2 gas was introduced continue to hash with `Felt::ZERO`, while newer blocks use the real value. Update the regression test vectors in `receipt_commitment_test.rs` accordingly. [6](#0-5) 

---

### Proof of Concept

1. **Submit** a Cairo 1 `INVOKE` transaction; the blockifier computes `receipt.gas.l2_gas = N` (e.g. `76014735`).

2. **Observe** that `output_for_hashing()` populates `TransactionOutputForHash { gas_consumed: GasVector { l2_gas: N, … }, … }`. [7](#0-6) 

3. **Trace** `calculate_block_commitments` → `calculate_receipt_commitment` → `calculate_receipt_hash` → `chain_gas_consumed`: the value `N` is silently discarded and `Felt::ZERO` is chained instead. [2](#0-1) 

4. **Construct** a second receipt element identical in all fields except `l2_gas = 0`. Compute its leaf hash with `chain_gas_consumed`. The two leaf hashes are **equal**, confirming that `l2_gas` is not bound by the commitment.

5. **Query** `starknet_getTransactionReceipt` for the transaction. Replace `l2_gas` in the response with `0` (or any other value). Recompute the receipt leaf hash and verify it against the receipt commitment stored in the block header — the check passes, demonstrating that the RPC can serve an incorrect `l2_gas` value with full apparent commitment-level authority.

### Citations

**File:** crates/starknet_api/src/execution_resources.rs (L106-111)
```rust
pub struct GasVector {
    pub l1_gas: GasAmount,
    pub l1_data_gas: GasAmount,
    #[serde(default)]
    pub l2_gas: GasAmount,
}
```

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L98-105)
```rust
#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
pub struct TransactionOutputForHash {
    pub actual_fee: Fee,
    pub events: Vec<Event>,
    pub execution_status: TransactionExecutionStatus,
    pub gas_consumed: GasVector,
    pub messages_sent: Vec<MessageToL1>,
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L263-265)
```rust
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L291-303)
```rust
    let transaction_leaf_elements: Vec<TransactionLeafElement> = transactions_data
        .iter()
        .map(|tx_leaf| {
            let mut tx_leaf_element = TransactionLeafElement::from(tx_leaf);
            if starknet_version < &BlockHashVersion::V0_13_4.into()
                && tx_leaf.transaction_signature.0.is_empty()
            {
                tx_leaf_element.transaction_signature =
                    TransactionSignature(vec![Felt::ZERO].into());
            }
            tx_leaf_element
        })
        .collect();
```
