### Title
OP_RETURN data output omitted from fee/vsize estimation, so `SignableTransaction` pays a lower effective fee rate than requested (and possibly below the relay minimum) - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

`SignableTransaction::new` appends an OP_RETURN output carrying up to 80 bytes of caller data to the transaction, but computes `needed_fee` and the change amount using `calculate_weight_vbytes`, which builds its weight-estimation transaction from only `payments` and `change` — the data output is never included. The recorded/committed fee therefore does not reflect the actual transaction, the same class of bug as a `totalSupply` that is not updated when tokens are created: an accounting value that fails to track the state it is supposed to describe.

### Finding Description

In `SignableTransaction::new`, the OP_RETURN output is pushed to `tx_outs` before fee estimation (`send.rs:194-202`). The fee is then computed as `fee_per_vbyte * vbytes` where `vbytes` comes from `calculate_weight_vbytes(tx_ins.len(), payments, None)` (`send.rs:204-206`). Inside `calculate_weight_vbytes`, the synthetic transaction's outputs are built solely from `payments` plus an optional `change` output (`send.rs:85-99`) — there is no parameter for, and no inclusion of, the OP_RETURN data output.

Consequences:

- The actual transaction is up to ~91 vbytes larger than estimated (80-byte push + OP_RETURN + amount + length overhead), yet the fee paid (`input_sat - payment_sat - change`) equals the underestimated `needed_fee`, so the effective fee rate is strictly less than the `fee_per_vbyte` the caller specified.
- The minimum-relay-fee check at `send.rs:211` compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the underestimated `vbytes`. A transaction that just passes this check can have an actual fee rate below the relay minimum once the data output is accounted for, causing the transaction to be rejected by the Bitcoin network.
- The change-output value at `send.rs:228-230` is computed as `input_sat - payment_sat - fee_with_change`, where `fee_with_change` is also derived from the undercounted vsize. The shortfall is silently absorbed into an insufficient fee rather than into the change.

### Impact Explanation

Any `SignableTransaction` constructed with `data: Some(_)` pays a lower effective fee rate than requested — a fee/change accounting error where the committed fee does not reflect the real transaction. In the worst case (fee rate near the relay minimum), the signed transaction is non-standard/unrelayable, so a spend the coordinator produces will not propagate: funds that should be movable are effectively stuck until the transaction is reconstructed with a correct fee. This is reachable by an unprivileged party whenever protocol data (e.g., an `InInstruction` payload embedded via OP_RETURN) accompanies a payment the user causes to be created.

### Likelihood Explanation

Triggered deterministically whenever `data` is `Some` and non-trivial in size; the fee is always underpaid relative to the specified rate. Reaching the "unrelayable" outcome additionally requires the chosen `fee_per_vbyte` to be near the minimum, which is realistic since fee rates are set deliberately low.

### Recommendation

Include the OP_RETURN output in the weight/vsize estimation — e.g., pass the fully-formed `tx_outs` (or the data length) into `calculate_weight_vbytes` so `needed_fee`, the minimum-fee check, and the change computation all account for the data output that is actually serialized into the transaction.

### Proof of Concept

```rust
// networks/bitcoin: conceptual PoC
// Construct payments and 80 bytes of data.
let data = Some(vec![0u8; 80]);

let tx = SignableTransaction::new(
  vec![output],                 // one ReceivedOutput input
  &[(payment_script, 1000)],    // payments
  Some(change_script),          // change
  data,                         // OP_RETURN output appended at send.rs:194-202
  fee_per_vbyte,
).unwrap();

// needed_fee was computed from a transaction containing only
// payments (+change) at send.rs:204-227 -- the OP_RETURN output is missing.
// The real transaction at tx.transaction() contains the data output, so:
//   actual_vsize > estimated_vsize
//   tx.fee() / actual_vsize < fee_per_vbyte
// With fee_per_vbyte == 1, the effective rate can fall below
// DEFAULT_MIN_RELAY_TX_FEE, making the transaction unrelayable.
```

Supporting code: the estimation transaction omits the data output at `send.rs:85-99`, while the real output is added at `send.rs:194-202` and the fee is fixed at `send.rs:204-234`.