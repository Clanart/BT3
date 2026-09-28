### Title
Change/"refund" computed from a fee that excludes the OP_RETURN data output, overpaying change and underpaying the transaction fee - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` computes the transaction weight, and therefore `needed_fee` / `fee_with_change`, from a synthetic transaction built only from `payments` (and optionally `change`). The OP_RETURN `data` output is appended to `tx_outs` *before* the weight calculation but is never passed into `calculate_weight_vbytes`. As a result the computed fee is based on a smaller transaction than the one actually produced, and the change output — the "refund" of leftover funds back to the caller — is calculated on the wrong (too-low) fee, mirroring the audit finding where the refund was computed on the wrong base amount.

### Finding Description
- `data` pushes an extra `TxOut` onto `tx_outs` at `networks/bitcoin/src/wallet/send.rs:194-202`.
- The fee is then estimated with `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204 and again with `Some(&change)` at line 226 — both calls pass `payments`, not `tx_outs`, so the OP_RETURN output's ~10–93 vbytes (8-byte value + up to ~83-byte script) are excluded.
- The change value is `input_sat - payment_sat - fee_with_change` (line 228). Because `fee_with_change` is too small, the change output is inflated by exactly the missing fee — the caller is "refunded" more than the intended fee policy allows, while `self.needed_fee` and `self.fee()` disagree with the actual weight of the signed transaction.
- The `TooLowFee` check at line 211 and the `TooLargeTransaction` check at line 241 are also evaluated against the wrong weight: a transaction can pass both checks yet exceed the declared `fee_per_vbyte` budget in the negative direction (paying a lower *effective* feerate, possibly below `DEFAULT_MIN_RELAY_TX_FEE` once the missing vbytes are counted) or exceed `MAX_STANDARD_TX_WEIGHT` once the data output is included.

### Impact Explanation
Any caller supplying `data` produces a transaction whose real feerate is lower than `fee_per_vbyte` — potentially below the minimum relay fee despite the explicit guard — yielding a transaction that nodes refuse to relay/confirm, and a change output that is oversized relative to the intended fee. The bookkeeping (`needed_fee`, `fee()`) is computed on the wrong amount, so downstream consumers trusting `needed_fee` overestimate the fee actually paid per vbyte. Funds aren't stolen, but the constructed transaction is objectively wrong and can be unrelayable — funds committed to that construction are stuck until reconstructed.

### Likelihood Explanation
Deterministic: any `SignableTransaction::new` invocation with `data: Some(_)` and a `change` script triggers it. Whether the effective feerate falls below relay minimum depends on `fee_per_vbyte` and data size (worst case ~93 missing vbytes for an 80-byte push).

### Recommendation
Pass the full output set (including the OP_RETURN output) into `calculate_weight_vbytes`. E.g., build the fee-estimation `TxOut` list from `tx_outs` rather than `payments`, for both the no-change and with-change calculations, so `needed_fee`, the `TooLowFee`/`TooLargeTransaction` checks, and the change value are all computed against the true transaction shape.

### Proof of Concept
```rust
// 1 input of e.g. 100_000 sats, 1 payment of 10_000 sats, a change script,
// and data = Some(vec![0; 80]).
let tx = SignableTransaction::new(inputs, &payments, Some(change), Some(vec![0; 80]), fee_per_vbyte)?;

// tx.needed_fee() == fee_per_vbyte * vsize(tx without the OP_RETURN output)
// tx.fee() == inputs - outputs == needed_fee
// but tx.transaction().vsize() is ~93 vbytes larger than the estimate, so
// tx.fee() / tx.transaction().vsize() < fee_per_vbyte, and can be
// < DEFAULT_MIN_RELAY_TX_FEE / 1000, making the tx unrelayable while the
// change output is inflated by fee_per_vbyte * (missing vbytes).
```
The test at `networks/bitcoin/tests/wallet.rs:173` already exercises `data` but only for success/error cases and never asserts `needed_fee` against `tx.vsize()` when data is present, which is why the discrepancy is unobserved.