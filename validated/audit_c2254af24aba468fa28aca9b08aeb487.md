### Title
OP_RETURN `data` output excluded from weight/fee/change accounting, producing under-priced or non-standard transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The bug class is "an amount is charged/computed without accounting for a component that exists." In `SignableTransaction::new`, the `data` argument creates a real OP_RETURN output that is pushed into `tx_outs` (networks/bitcoin/src/wallet/send.rs:194-202) before the weight calculation, yet `calculate_weight_vbytes` is only ever called with `payments` — never with the data output (send.rs:204, 226). Consequently `needed_fee`, the minimum-relay-fee check, the change-amount computation, and the `MAX_STANDARD_TX_WEIGHT` check are all computed on a transaction that is smaller than the one actually built and signed.

### Finding Description
`calculate_weight_vbytes` reconstructs the transaction from `inputs` + `payments` + optional `change` only (send.rs:85-99). The OP_RETURN output (up to ~89 bytes: 8-byte amount + script with up to 80 bytes of data) is absent from every call:

- `needed_fee = fee_per_vbyte * vbytes` underprices the real vsize (send.rs:204-206).
- The `TooLowFee` check at send.rs:211 validates a minimum feerate against the underestimated vbytes, so a transaction can pass the check while its real feerate is below `DEFAULT_MIN_RELAY_TX_FEE`.
- When `change` is provided, `fee_with_change` and `value = input_sat - payment_sat - fee_with_change` (send.rs:225-230) are computed with the wrong weight, so change is over-credited and the effective fee is lower than the caller-specified rate — the caller "pays" a fee that was never correctly accounted, mirroring M-03's mis-routed fee.
- The standardness guard `weight > MAX_STANDARD_TX_WEIGHT` (send.rs:241) uses `weight`, which excludes the data output; a transaction at the boundary passes the check yet is non-standard and will not relay.

### Impact Explanation
A `SignableTransaction` created with `data` can (a) silently pay a lower feerate than requested, or (b) be below the minimum relay feerate / exceed `MAX_STANDARD_TX_WEIGHT` despite passing the constructor's checks, yielding an unbroadcastable transaction. The signed transaction commits inputs (`prevouts`) whose spend was already authorized by the threshold signature; the resulting UTXO movement never confirms, leaving funds committed to a transaction the network rejects — funds effectively locked for the affected inputs.

### Likelihood Explanation
`data` is a public caller-supplied parameter to `SignableTransaction::new`; any integration path exposing arbitrary OP_RETURN data reaches this with public inputs. The discrepancy is deterministic and grows linearly with `data` length (up to ~89 vbytes unaccounted), so transactions near the feerate or weight boundary reliably trigger it. Impact is bounded to mispriced/stuck transactions rather than theft, consistent with Medium.

### Recommendation
Include the data output in `calculate_weight_vbytes` — e.g., pass `tx_outs` (or a `data: Option<&ScriptBuf>` argument) into the weight reconstruction at send.rs:204 and send.rs:225-226 so `vbytes`, `weight`, `needed_fee`, the `TooLowFee` check, and the change computation all reflect the transaction actually built at send.rs:245-255.

### Proof of Concept
```rust
// Conceptual PoC against SignableTransaction::new
// inputs totaling `input_sat`, payments summing to `payment_sat`,
// change = Some(...), data = Some(vec![0u8; 80])

let tx = SignableTransaction::new(inputs, &payments, Some(change), Some(data), fee_per_vbyte)?;

// The constructed transaction contains an OP_RETURN output (tx.output includes it),
// but needed_fee() was computed from vbytes excluding that output:
//   real_vsize = tx.transaction().vsize()
//   real_vsize > vbytes_used_in_constructor   // by ~89 vbytes
// Therefore:
//   tx.fee() < fee_per_vbyte * real_vsize      // underpriced vs caller intent
// and if fee_per_vbyte * real_vsize < min relay fee for real_vsize,
// TooLowFee was not raised yet the TX will not relay.
```
The mismatch is visible directly: the output pushed at send.rs:194-202 is never represented in either `calculate_weight_vbytes` call (send.rs:204, send.rs:226), while `fee()` (send.rs:138-141) measures the actual signed transaction — the two accounting views diverge whenever `data.is_some()`.