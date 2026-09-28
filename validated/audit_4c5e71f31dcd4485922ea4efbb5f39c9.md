### Title
SignableTransaction omits OP_RETURN output from weight/fee calculation, producing underpriced or oversized transactions at the funding boundary - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the Rubicon `_maxBorrow` bug — where borrowing exactly up to the computed liquidity limit leaves no margin for the interest accrued one block later — `SignableTransaction::new` computes the transaction's weight, virtual size, required fee, and the `MAX_STANDARD_TX_WEIGHT` check while ignoring the OP_RETURN output it has already appended to `tx_outs`. A caller who funds the transaction exactly at the computed boundary (`input_sat == payment_sat + needed_fee`) produces a transaction whose actual fee rate is below what was requested — and potentially below the minimum relay fee — or whose actual weight exceeds the standard limit. The transaction passes all internal validation yet is born "under water": signed but rejected by the network.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` (lines 194–202), but the weight/vsize computation at line 204 calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, which builds its synthetic transaction purely from `payments` and optional `change` — `data` is never represented:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

Consequences:

1. `needed_fee` is computed for a transaction smaller than the real one by the full serialized size of the OP_RETURN output (8-byte value + compact-size length + script bytes + up to 80 bytes of data ≈ up to ~90 vbytes). The minimum-relay check at line 211 and the `NotEnoughFunds` check at line 215 therefore use an understated fee.
2. When `change` is specified, `fee_with_change` (lines 225–233) is computed via `calculate_weight_vbytes(..., payments, Some(&change))`, again without the OP_RETURN output, so the change value can be inflated by the unpaid fee delta — or change that "fits" per the wrong estimate may push the real transaction below the target fee rate.
3. The `weight > MAX_STANDARD_TX_WEIGHT` check at line 241 uses the understated `weight`, so a transaction that actually exceeds the standard weight limit is accepted as valid.

A caller supplying `data` plus inputs sized exactly to `payment_sat + needed_fee` (the "max borrow" boundary) gets a `SignableTransaction` the multisig will happily sign — `TransactionSignMachine::sign` computes sighashes over the real `tx` (lines 373–390) — but whose real `fee() / real_vsize` is below the requested rate or which is non-standard, so it is never relayed/confirmed.

### Impact Explanation
The signed transaction is unbroadcastable or stuck: its effective fee rate can fall below `DEFAULT_MIN_RELAY_TX_FEE` (no mempool acceptance), or its real weight exceeds `MAX_STANDARD_TX_WEIGHT` (non-standard, universally rejected). Inputs locked into the produced transaction are not spendable via it; the signing round completes successfully yet yields a void artifact, forcing a complete resign/abort cycle for the threshold group. Where the coordinator-level API always passes `data: None`, direct consumers of the `bitcoin-serai` wallet API reach this path with attacker-controlled `data` bytes (a public input to `SignableTransaction::new`). Medium severity: permanent loss is avoided, but the multisig is induced to sign a transaction that can never execute its intent, burning a FROST signing session and stalling the associated UTXOs.

### Likelihood Explanation
Deterministic whenever (a) `data.is_some()`, and (b) inputs are sized near the computed `needed_fee` boundary or the transaction is near the standard-weight limit — precisely the "max spend" case the bug class concerns, where there is no surplus to absorb the unaccounted output's weight. The larger the `data` (up to the permitted 80 bytes), the wider the fee/weight discrepancy. Unlike a probabilistic edge, the miscount is guaranteed; only the boundary funding determines whether the result is merely overpriced-fee or invalid.

### Recommendation
Include the OP_RETURN output in the weight/vsize estimate. Either pass the fully assembled `tx_outs` (or a `payments`-shaped list augmented with a `(op_return_script, 0)` entry) into `calculate_weight_vbytes`, or add the serialized OP_RETURN output's weight to `weight`/`vbytes` before computing `needed_fee`, `fee_with_change`, and performing the `MAX_STANDARD_TX_WEIGHT` check. The same corrected vsize must be used for both the no-change and with-change fee calculations so change subtraction reflects the real transaction.

### Proof of Concept
In `networks/bitcoin/tests/wallet.rs` style:

```rust
// One input worth `payment + underestimated_fee`, plus maximal data
let output = send_and_get_output(&rpc, &scanner, key).await;
let payment = 1000u64;
let data = vec![0u8; 80];

// Estimate the fee the library will charge (no change)
let probe = SignableTransaction::new(
  vec![output.clone()], &[(addr(), payment)], None, None, FEE,
).unwrap();
let underestimated = probe.needed_fee();

// Fund exactly at the boundary the library computed
let funded = { /* output with value = payment + underestimated */ };

let signable = SignableTransaction::new(
  vec![funded], &[(addr(), payment)], None, Some(data), FEE,
).unwrap(); // incorrectly Ok

let tx = sign(&keys, &signable);
// The real transaction is ~90+ vbytes larger than estimated:
assert!(tx.vsize() as u64 * FEE > underestimated);
// So its effective fee rate < FEE, and with FEE near the minimum relay
// rate the real tx falls below DEFAULT_MIN_RELAY_TX_FEE and is rejected:
assert!(rpc.send_raw_transaction(&tx).await.is_err());
```

At the weight boundary, set `payments` such that the estimated `weight` is just below `MAX_STANDARD_TX_WEIGHT`; the added ~90 WU×4 from the OP_RETURN output pushes the real transaction over the limit while `TransactionError::TooLargeTransaction` is never returned.