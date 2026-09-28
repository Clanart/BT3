### Title
`SignableTransaction` omits the OP_RETURN data output from weight/vsize calculation, undercharging fees - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes an OP_RETURN `TxOut` carrying up to 80 bytes of caller-supplied `data` onto the transaction outputs, but computes the transaction weight and required fee via `calculate_weight_vbytes`, which only reconstructs a transaction containing `payments` and an optional `change` output. The data output is never included in the size estimate, so `needed_fee` is computed on a smaller virtual size than the transaction actually has. This mirrors the C-01 bug class: a multiplication (`toFulfill * realizedPrice`) missing a required normalization term; here `fee_per_vbyte * vbytes` is computed against a vbyte count that silently excludes an entire output.

### Finding Description
In `SignableTransaction::new`:

```rust
// Add the OP_RETURN output
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(
      PushBytesBuf::try_from(data).expect("..."),
    ),
  })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` (lines 62-127) builds a `Transaction` whose `output` list is `payments` plus optionally `change`. It has no parameter for the OP_RETURN output. Both the initial call at line 204 and the `change` call at line 226 (`calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`) exclude the data output. An OP_RETURN output costs ~11 + data_len vbytes (up to ~92 vbytes at the 80-byte cap).

Consequences:
- `needed_fee = fee_per_vbyte * vbytes` is too low by `fee_per_vbyte * (OP_RETURN output vbytes)`.
- The minimum-relay-fee check at line 211 compares against the underestimated `vbytes`, so the real transaction can end up below `DEFAULT_MIN_RELAY_TX_FEE` (1000 sat/kvB) even though the check passed.
- The `MAX_STANDARD_TX_WEIGHT` check at line 241 uses the underestimated `weight`, so a transaction with a data output can exceed the standardness limit while passing the check.
- The change deduction at line 228 uses `fee_with_change` computed on the underestimated size, so the change output is slightly overpaid relative to the intended fee rate — the overpayment goes to change rather than the miner, leaving the actual paid fee too low for the real size.

An unprivileged caller supplies `data` (the "send transaction data" public input), and the resulting signed transaction is the artifact affected.

### Impact Explanation
The signed transaction's effective fee rate is strictly lower than the `fee_per_vbyte` requested. When `fee_per_vbyte` is near the minimum relay rate, the real transaction's fee rate falls below 1 sat/vB, making it non-relayable/non-standard — the funds committed as inputs cannot move until the multisig re-signs a corrected transaction, and any node enforcing standardness will reject it. For larger `data` (near 80 bytes) or many inputs near the weight cap, the transaction can also silently exceed `MAX_STANDARD_TX_WEIGHT`, producing an invalid broadcast artifact despite `new()` returning `Ok`. In the processor path (`processor/src/networks/bitcoin.rs::make_signable_transaction`), `data` is currently `None`, but the library function is a public API that accepts arbitrary data and produces a signature over a malformed economic structure — the direct analog of "funds moved under an incorrectly computed value" from the Geode finding.

### Likelihood Explanation
The trigger is fully deterministic: any call to `SignableTransaction::new` with `data: Some(_)` produces a systematically undercharged fee and understated weight. No adversarial manipulation is needed — the miscalculation happens on every invocation that exercises the public `data` parameter. Severity is bounded because the only consequence is a too-low fee / unrelayable transaction rather than theft; no secret material is exposed, and the inputs remain spendable by a correctly formed transaction. This places it at Medium.

### Recommendation
Include the OP_RETURN output in the weight estimation. Extend `calculate_weight_vbytes` to accept the data output (or the finalized `tx_outs`), e.g.:

```diff
- let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
+ let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), &tx_outs, None);
```

with `calculate_weight_vbytes` building `tx.output` from the already-constructed `tx_outs` (payments + OP_RETURN) plus the optional change `TxOut`, rather than from `payments` alone. Recompute the change branch at line 226 against the same full output list so `fee_with_change` covers the data output as well.

### Proof of Concept
```rust
// Construct a minimal valid transaction request carrying data.
let inputs = vec![output];                        // a spendable ReceivedOutput
let payments = vec![(p2tr_script_buf(key).unwrap(), 1000)];
let data = Some(vec![0u8; 80]);                    // maximal OP_RETURN payload

let tx = SignableTransaction::new(inputs, &payments, None, data, FEE).unwrap();

// needed_fee was computed on a transaction with only `payments` in `output`.
// The actual signed transaction additionally carries the ~92-vbyte OP_RETURN
// output, so its real size is larger than what needed_fee was priced for.
assert_eq!(tx.tx.output.len(), 2);                 // payment + OP_RETURN
// tx.weight() (real) > weight used internally for `vbytes` (which omitted output[1]).
// fee rate actually paid = needed_fee / real_vsize < FEE.
// For FEE == 1 sat/vB, real rate < DEFAULT_MIN_RELAY_TX_FEE -> unrelayable.
```

Supporting code: `networks/bitcoin/src/wallet/send.rs` lines 62-127 (`calculate_weight_vbytes` builds outputs only from `payments` + `change`), 193-206 (OP_RETURN pushed to `tx_outs` but excluded from the weight call), 226-232 (change fee computed on the same incomplete output set), and 241 (weight limit check on the understated `weight`).