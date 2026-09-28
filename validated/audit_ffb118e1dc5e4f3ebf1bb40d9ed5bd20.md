### Title
Fee and weight calculation omits the OP_RETURN data output, so signed transactions systematically underpay the requested fee rate — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` measures transaction size via `calculate_weight_vbytes`, which builds the template transaction only from `payments` and (optionally) the change output. The OP_RETURN output carrying `data` is pushed to `tx_outs` but is never included in the weight/vbyte measurement, in the minimum-relay-fee check, or in the change-amount computation. The result is the same class of bug as the reference report — a value computed in the wrong unit/scale — here the fee is computed against a smaller "size unit" than the transaction that is actually signed and broadcast.

### Finding Description
- `tx_outs` gets an extra OP_RETURN output at lines 194–202, before any size is measured.
- `calculate_weight_vbytes` (lines 62–127) constructs its template `Transaction` from `payments` and `change` only; `data` is not a parameter, so the returned `weight`/`vbytes` understate the real transaction by the full size of the OP_RETURN output (up to ~90 vbytes for the 80-byte data cap).
- `needed_fee = fee_per_vbyte * vbytes` (line 206) and the `TooLowFee` comparison `(DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` (line 211) therefore validate a fee that is too small for the actual signed transaction.
- With a change output, `value = input_sat - (payment_sat + fee_with_change)` (line 228): the change absorbs the shortfall, so the absolute fee stays `fee_with_change` while the true vsize is larger — the effective sat/vbyte is strictly below `fee_per_vbyte`.
- The `TooLargeTransaction` check at line 241 also uses the understated `weight`, so a transaction whose real weight exceeds `MAX_STANDARD_TX_WEIGHT` once the data output is counted is accepted and signed.

Every input here (`inputs`, `payments`, `change`, `data`, `fee_per_vbyte`) is attacker-influencable transaction data that is committed to by `TransactionSignMachine::sign` via `taproot_key_spend_signature_hash` (lines 373–391): the threshold signs the underpriced transaction exactly as constructed.

### Impact Explanation
The vault/multisig produces a signed transaction whose real fee rate is lower than the caller requested and lower than the min-relay check verified. For data sizes near the 80-byte cap, the discrepancy is ~90 vbytes; at low `fee_per_vbyte` values this can push the transaction below `DEFAULT_MIN_RELAY_TX_FEE`, making it unrelayable, and it can also exceed `MAX_STANDARD_TX_WEIGHT` and be rejected as non-standard. Funds are not stolen, but a signed spend intended to carry `data` (e.g., a protocol-mandated OP_RETURN) silently underpays or is unbroadcastable — a concrete mismatch between what `new`/`needed_fee` promise and what `complete` produces.

### Likelihood Explanation
Any call to `SignableTransaction::new` with `data.is_some()` triggers the underpayment; no adversarial timing or collusion is required. The magnitude grows linearly with `data.len()`, and the boundary cases (weight just under the standard limit, fee just over the relay minimum) are easy to hit deliberately or accidentally.

### Recommendation
Pass the fully-populated output list (payments + OP_RETURN + change) into `calculate_weight_vbytes`, or compute weight after `tx_outs` is finalized; apply the same corrected `vbytes` to the `TooLowFee` check, `needed_fee`, `fee_with_change`, and the `MAX_STANDARD_TX_WEIGHT` check so the measured size always matches the signed transaction.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs semantics:
let data = vec![0u8; 80]; // maximum allowed
// 1 payment of DUST, 1 input covering it, no change
let stx = SignableTransaction::new(inputs, &payments, None, Some(data.clone()), fee_per_vbyte)?;
// Real tx contains payments + OP_RETURN output (~90 extra vbytes),
// but stx.needed_fee() == fee_per_vbyte * vbytes(payments only).
// Actual signed Transaction from complete() includes the OP_RETURN,
// so effective fee rate = needed_fee / real_vbytes < fee_per_vbyte,
// and real weight can exceed MAX_STANDARD_TX_WEIGHT unchecked.
```