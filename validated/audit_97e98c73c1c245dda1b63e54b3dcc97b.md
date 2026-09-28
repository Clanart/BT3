### Title
`SignableTransaction::new` omits the OP_RETURN data output from the fee/weight estimate, causing systematic fee underestimation and potentially un-relayable transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

`SignableTransaction::new` appends an OP_RETURN output carrying `data` to `tx_outs`, but computes the transaction weight and `needed_fee` via `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which is fed the `payments` slice — a list that does not contain the OP_RETURN output. Both the no-change and with-change fee paths therefore underestimate the final transaction's vsize. The same underestimate feeds the minimum-relay-fee check, so a transaction whose true fee rate falls below `DEFAULT_MIN_RELAY_TX_FEE` can be constructed, signed, and emitted as if valid.

### Finding Description

The function builds the OP_RETURN output before sizing the transaction:

```rust
// networks/bitcoin/src/wallet/send.rs:194-204
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` reconstructs the transaction from `tx_ins` count and `payments` only (lines 68–99), so the serialized length of the OP_RETURN output — 8-byte value plus a script of `1 + push-opcode + data.len()` bytes, up to ~90 bytes / ~90 vbytes when `data` is the permitted 80 bytes — is excluded from `vbytes` and `weight`.

Consequences on the two fee paths:

- `needed_fee = fee_per_vbyte * vbytes` (line 206) is smaller than the true required fee by `fee_per_vbyte * (~10 + data_len)` sats.
- The min-relay check `needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` (line 211) is evaluated against the underestimated `vbytes`, so it can pass while the real fee rate is below the relay floor.
- The change branch (lines 224–235) repeats the same miscalculation via `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`, so `change = input_sat - payment_sat - fee_with_change` hands the shortfall to the change output instead of the fee — the transaction simply pays less than the caller-specified rate.
- `TooLargeTransaction` (line 241) is likewise checked against a weight missing the data output; a tx just under `MAX_STANDARD_TX_WEIGHT` can silently exceed it once the OP_RETURN is appended.

The impact window is bounded (≤ ~90 vbytes per transaction, single OP_RETURN), and the caller-visible `needed_fee()`/`fee()` remain self-consistent, so no funds are directly stolen — the defect is that the multisig signs a transaction whose real fee rate is below what was requested and possibly below relay policy.

### Impact Explanation

An unprivileged party can supply `data` (up to 80 bytes) as part of the normal Serai Bitcoin flow, and the resulting threshold-signed transaction will carry a real fee rate lower than `fee_per_vbyte` — potentially under `DEFAULT_MIN_RELAY_TX_FEE` even though the internal check passed. Such a transaction will not propagate through default-policy nodes, stalling spends/change settlement for the affected key until the condition is recognized and re-signed at a higher rate. With a change output, the discrepancy is silently absorbed by reducing the change; without change, `fee()` still equals the underestimated `needed_fee`, so the underpayment is invisible to downstream accounting. Severity: Medium — correctness/availability defect in transaction construction, not key or signature compromise.

### Likelihood Explanation

Reachable whenever `SignableTransaction::new` is invoked with `data: Some(..)`, which is the library's supported path for embedding protocol instructions. No attacker sophistication is needed — the miscalculation is deterministic on every data-carrying transaction. Exploitation for guaranteed relay failure additionally requires the requested `fee_per_vbyte` to sit within ~`90 / vbytes` of the relay minimum, which is narrow; the deterministic fee-rate shortfall itself is unconditional.

### Recommendation

Pass the fully-assembled output list into the weight calculation — i.e., compute `calculate_weight_vbytes` over `tx_outs` (which already includes the OP_RETURN and, in the change path, the change output) rather than `payments`. Concretely, defer `calculate_weight_vbytes` until after `tx_outs` is complete, or extend it to accept the OP_RETURN output explicitly. Also re-derive the `TooLowFee` check against the true vsize, and add a test asserting `needed_fee == fee_per_vbyte * tx.vsize()` for a transaction carrying 80 bytes of data.

### Proof of Concept

```rust
// Conceptual: in networks/bitcoin (test context)
let data = vec![0u8; 80]; // maximum permitted by the TooMuchData check
let tx = SignableTransaction::new(
  vec![output.clone()],
  &payments,              // any non-dust payments
  Some(change_script),    // or None — either path is affected
  Some(data),
  FEE_PER_VBYTE,
).unwrap();

// The OP_RETURN output exists in the final transaction:
assert!(tx.transaction().output.iter().any(|o| o.script_pubkey.is_op_return()));

// But it was never counted in the fee estimate:
// needed_fee() == FEE_PER_VBYTE * vsize(tx without the OP_RETURN output)
// actual vsize of tx.transaction() is larger by ~ (8 + 1 + 1 + 80) bytes
// => tx.fee() / real_vsize < FEE_PER_VBYTE
// and if FEE_PER_VBYTE * vbytes just cleared DEFAULT_MIN_RELAY_TX_FEE,
// the signed transaction's true rate is below the relay minimum.
```

The root cause is localized: `tx_outs.push(op_return)` at `networks/bitcoin/src/wallet/send.rs:194-202` precedes the weight calls at lines 204 and 226, both of which receive only `payments`.