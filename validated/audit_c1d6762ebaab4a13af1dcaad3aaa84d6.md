### Title
`SignableTransaction::new` omits the OP_RETURN data output from weight/vbytes and fee calculation - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the `StableOracleDAI` decimals mismatch — where a value is produced in the wrong scale and consumed downstream as if correctly scaled — `SignableTransaction::new` in bitcoin-serai computes the transaction's weight, virtual size, and required fee over an output set that excludes the OP_RETURN data output, even though that output is included in the final signed transaction. The fee is therefore priced for a smaller transaction than the one actually produced.

### Finding Description
`SignableTransaction::new` builds `tx_outs` by first pushing all payment outputs, then appending an OP_RETURN output carrying up to 80 bytes of caller-supplied `data` ([send.rs L194-L202](networks/bitcoin/src/wallet/send.rs)). However, the size/fee math is done on `payments` alone:

- `let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);` ([L204](networks/bitcoin/src/wallet/send.rs)) — `payments` does not include the OP_RETURN output that was already pushed to `tx_outs`.
- `needed_fee = fee_per_vbyte * vbytes` ([L206](networks/bitcoin/src/wallet/send.rs)) is computed on that underestimated `vbytes`.
- The change-output branch calls `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` ([L225-L227](networks/bitcoin/src/wallet/send.rs)), again omitting the OP_RETURN output.
- The `MAX_STANDARD_TX_WEIGHT` check ([L241](networks/bitcoin/src/wallet/send.rs)) also uses a weight that excludes the data output.

Inside `calculate_weight_vbytes`, the dummy transaction's `output` vector is built strictly from `payments` plus the optional change ([L85-L99](networks/bitcoin/src/wallet/send.rs)), so an OP_RETURN output (scriptPubKey = `OP_RETURN` + pushdata of up to 80 bytes ≈ ~83–91 additional bytes/vbytes) is never counted.

Like the oracle bug (numerator scaled by 1e18 against an 8-decimal term, yielding a result off by 10^10), here the measured quantity is in a different "denomination" of transaction than the one signed: the transaction the multisig actually signs in `TransactionSignMachine::sign` is `self.tx.tx`, which contains the OP_RETURN output ([L373-L390](networks/bitcoin/src/wallet/send.rs), [L246-L251](networks/bitcoin/src/wallet/send.rs)).

### Impact Explanation
Any caller that attaches `data` (the `data` argument is an untrusted/public input to `SignableTransaction::new`) produces a transaction whose real vsize exceeds the accounted vsize by roughly `1 + 1 + pushdata_len` vbytes. Consequences:

1. `needed_fee` / `fee()` understate the fee required to achieve `fee_per_vbyte`; the transaction's effective feerate is lower than requested. For an 80-byte payload it is ~8–10% lower on a typical 1-in/2-out Taproot transaction (~160 vbytes).
2. The minimum-relay-fee check at [L211](networks/bitcoin/src/wallet/send.rs) (`DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) is evaluated against the smaller vsize, so a transaction can pass `TooLowFee` while its actual feerate is below the mempool minimum, producing a signed transaction that will not relay.
3. The `TooLargeTransaction` weight check can be bypassed by the size of the omitted output, though this is marginal (~91 WU vs. the 400,000 WU limit).

The signed transaction is still consensus-valid, so this is a correctness/availability issue rather than theft; funds in the inputs are not spendable via the produced transaction if it fails relay policy at the intended feerate.

### Likelihood Explanation
Reachable by any unprivileged party who can cause a `SignableTransaction` to be constructed with a non-`None` `data` argument — exactly the "public inputs / transaction data they cause to be signed" surface. The miscalculation is deterministic whenever `data` is present; no adversarial timing or collusion is needed. Severity is limited because the deficit is bounded by the OP_RETURN size and does not corrupt signatures or leak keys.

### Recommendation
Compute weight/vbytes over the complete output set actually used in the final transaction. Concretely, build `tx_outs` (payments + OP_RETURN) first, then call a variant of `calculate_weight_vbytes` that takes the full `Vec<TxOut>` (or pass `payments` plus the data output) for both the no-change and with-change estimates at L204 and L225-L227, and for the `MAX_STANDARD_TX_WEIGHT` check at L241.

### Proof of Concept
```rust
// One input of 10_000 sats, one payment of 5_000 sats, no change,
// 80-byte OP_RETURN payload, fee_per_vbyte = 10.
let data = vec![0u8; 80];
let tx = SignableTransaction::new(
    inputs,                       // 10_000 sat input
    &[(payment_script, 5_000)],
    None,
    Some(data),
    10,
).unwrap();

// vbytes used for needed_fee excludes the OP_RETURN output (~83 vbytes).
// effective feerate = tx.fee() / actual_vsize
//   < fee_per_vbyte = 10
let actual_vsize = tx.transaction().vsize() as u64;
let accounted_fee = tx.needed_fee();
assert!(tx.fee() < 10 * actual_vsize); // real feerate below requested rate
```