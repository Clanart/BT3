### Title
OP_RETURN `data` output excluded from weight/fee calculation, producing under-priced transactions whose input funds become unspendable - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the auction report — where an accounting omission (missing "rewards must sum to 100%" check) leaves a residual portion of funds unallocated and stuck — `SignableTransaction::new` omits the OP_RETURN `data` output from the transaction's weight/vbyte accounting. The fee is computed over a transaction that does not include the data output, so the true fee rate falls below `fee_per_vbyte` and, at low requested rates, below the network minimum relay fee. The result is a signed transaction that cannot be relayed/confirmed, leaving the input UTXOs' funds stranded.

### Finding Description
`SignableTransaction::new` pushes the OP_RETURN output into `tx_outs` (send.rs:194-202), but computes weight via `calculate_weight_vbytes(tx_ins.len(), payments, None)` (send.rs:204), which rebuilds a transaction containing *only* `payments` — the `data` output is never included:

- `calculate_weight_vbytes` constructs `tx.output` solely from `payments` plus the optional change output (send.rs:85-99).
- `needed_fee = fee_per_vbyte * vbytes` and the `TooLowFee` minimum-relay check both use this under-counted `vbytes` (send.rs:206-213).
- A data payload of up to 80 bytes is permitted (send.rs:171-173); the OP_RETURN output adds ~90 non-witness bytes (~360 weight units, ~90 vbytes) that are never charged for.

Consequently `needed_fee` — which becomes the actual paid fee, since `fee() = sum(inputs) − sum(outputs)` (send.rs:138-141) and the change path simply subtracts `payment_sat + needed_fee` — is short by roughly `90 * fee_per_vbyte` sats. The `MAX_STANDARD_TX_WEIGHT` check (send.rs:241) is likewise evaluated against the underestimated weight.

### Impact Explanation
An unprivileged caller supplies the `data` argument and `fee_per_vbyte`. If `fee_per_vbyte` is chosen near the minimum relay rate (e.g., 1 sat/vbyte), the check at send.rs:211 passes on the reduced vbytes, but the real transaction's effective fee rate falls below `DEFAULT_MIN_RELAY_TX_FEE`, making it un-relayable and never confirmable. Since the transaction is the signed spend of the wallet's UTXOs, those inputs' value is effectively locked until an entirely different transaction is crafted — a direct parallel to "funds stuck in the contract" from a missing completeness bound on allocation. Even at higher fee rates, callers are charged an incorrect fee and the transaction can exceed standard weight while passing the `TooLargeTransaction` guard.

### Likelihood Explanation
Any `SignableTransaction::new` invocation with `Some(data)` and a low `fee_per_vbyte` triggers it; no attacker sophistication is needed beyond supplying public inputs (data bytes and a fee rate). The miscount is deterministic, not timing-dependent.

### Recommendation
Include the data output in the weight accounting — e.g., pass the already-built `tx_outs` (or an equivalent output list containing the OP_RETURN) into `calculate_weight_vbytes` instead of `payments`, for both the no-change and with-change calls — and validate `needed_fee`/`MAX_STANDARD_TX_WEIGHT` against the final transaction that will actually be broadcast.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs semantics
// inputs: one ReceivedOutput worth 100_000 sats
// payments: [(some_script, 50_000)]  (>= DUST)
// change: None
// data: Some(vec![0u8; 80])        // passes the <= 80 check at line 171
// fee_per_vbyte: 1                 // minimum realistic relay rate

// calculate_weight_vbytes(tx_ins.len(), payments, None) builds a tx WITHOUT
// the OP_RETURN output, so `vbytes` is ~90 vbytes too small.
// needed_fee = 1 * vbytes passes the TooLowFee check at line 211 because
// vbytes (underestimated) * 1 >= DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000.

// The returned transaction's real vsize is vbytes + ~90 (OP_RETURN output:
// 8-byte amount + ~83-byte script). Its effective rate is
// needed_fee / real_vsize < 1 sat/vbyte < DEFAULT_MIN_RELAY_TX_FEE,
// so nodes reject it; the input UTXO's 100_000 sats are locked in a
// transaction that can never confirm.
```