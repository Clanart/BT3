### Title
`SignableTransaction` fee/weight calculation omits the `OP_RETURN` data output, underpaying fees and undercounting weight — (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
The referenced bug class is a hardcoded/incorrect parameter (`0` gas limit) that makes an otherwise valid recovery transaction always fail, leaving user funds stuck. The analog in `bitcoin-serai` is `SignableTransaction::new`, which computes the transaction weight/vbytes — and therefore `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check — from `payments` only, while the serialized transaction also includes an `OP_RETURN` data output of up to ~80 bytes that is never included in the weight calculation.

### Finding Description
`SignableTransaction::new` pushes an `OP_RETURN` output onto `tx_outs` when `data` is specified (send.rs:194-202). However, both weight calculations use the `payments` slice, which excludes that output:

```rust
// networks/bitcoin/src/wallet/send.rs
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None); // L204
let mut needed_fee = fee_per_vbyte * vbytes;                                          // L206
...
let (weight_with_change, vbytes_with_change) =
    Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));             // L225-226
```

`calculate_weight_vbytes` builds its template transaction only from `payments` and the optional change output (send.rs:85-99); the `OP_RETURN` output (script of `OP_RETURN` + up to 80 pushbytes, plus the 8-byte value and compact-size script length) is absent. The signed transaction that is actually produced contains it (send.rs:246-251), so:

- The real vsize exceeds `vbytes` by roughly 13–100 weight-units-worth (≈ up to ~25+ vbytes), so the effective fee rate is strictly less than the caller-requested `fee_per_vbyte`.
- The minimum-relay-fee check at send.rs:211 validates `needed_fee` against an under-measured `vbytes`, so a transaction that should have been rejected as `TooLowFee` — or that the caller believes pays `fee_per_vbyte` — can be signed while actually paying below the relay minimum.
- The `weight > MAX_STANDARD_TX_WEIGHT` check at send.rs:241 undercounts, permitting a transaction that exceeds the standardness weight limit and will never be relayed.

This mirrors the audit finding: a parameter used for an external validity constraint is computed from the wrong inputs, producing a transaction that fails at broadcast/inclusion while the library reports success.

### Impact Explanation
Any signing session for a transaction carrying `data` produces a transaction whose actual fee rate is below `fee_per_vbyte`. In the worst case (fee rate near the minimum relay fee, or a transaction near the max standard weight), the fully signed transaction is rejected by relay policy and never confirms, while `SignableTransaction` reported success — the inputs are committed to a transaction the network will not accept, and a new signing round is required. When the transaction does relay, the protocol still pays less fee than specified, degrading confirmation. This is a funds-liveness failure reachable purely by including a `data` payload, analogous to a refund path that always reverts due to a bad gas parameter.

### Likelihood Explanation
Reachable by any flow that constructs a `SignableTransaction` with `data: Some(...)` — an unprivileged party can cause a transaction containing OP_RETURN data to be signed. The discrepancy is deterministic (not probabilistic), but the failure mode only materializes when the underpaid fee drops the real fee rate below policy thresholds, so it is most exploitable/impactful for low `fee_per_vbyte` or near-limit transactions. Severity: Medium.

### Recommendation
Include the data output in the weight estimation. Either pass the already-built `tx_outs` (payments + OP_RETURN) into `calculate_weight_vbytes`, or append the OP_RETURN `TxOut` inside `calculate_weight_vbytes` before calling `tx.weight()`. Recompute for both the no-change and with-change variants so `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check cover the full serialized transaction.

### Proof of Concept
```rust
// Construct a transaction whose only "output" intent is data
let inputs = vec![received_output];           // any scanned ReceivedOutput
let payments: &[(ScriptBuf, u64)] = &[];      // no payments
let data = Some(vec![0u8; 80]);               // max allowed OP_RETURN payload

let stx = SignableTransaction::new(inputs, payments, Some(change_script), data, fee_per_vbyte)?;

// The returned tx contains the OP_RETURN output:
assert_eq!(stx.transaction().output.len(), 2); // OP_RETURN + change

// But vbytes was computed from payments + change only (send.rs:204-226),
// so stx.needed_fee() == fee_per_vbyte * (vsize_without_op_return),
// while fee() == inputs - outputs is fixed. The actual fee rate is
// fee() / real_vsize < fee_per_vbyte, and a tx that should have been
// TooLowFee at send.rs:211 or TooLargeTransaction at send.rs:241 is
// signed and emitted anyway.
```
Concretely: with `fee_per_vbyte` chosen exactly at the minimum relay threshold, `needed_fee` passes the check at send.rs:211 using a `vbytes` that omits the OP_RETURN output (~15–25 vbytes), yet the broadcast transaction's real fee rate falls below `DEFAULT_MIN_RELAY_TX_FEE` and it is dropped by relay — a valid-input transaction that deterministically produces an unrelayable result.