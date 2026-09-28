### Title
Fee/weight estimation omits the OP_RETURN output, producing transactions with insufficient fees that may fail relay - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` sizes the transaction using only `payments` (and optionally `change`), never including the OP_RETURN data output it appends to `tx_outs`. Just as a hardcoded gas stipend may be insufficient for the actual work performed, the hardcoded size assumption here excludes up to ~92 real vbytes, so the computed `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check are both underestimated. The resulting transaction can be broadcast paying less than the intended `fee_per_vbyte` rate — potentially below the minimum relay fee for its true size — leaving funds sent but never confirmable by the recipient.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` before the weight is ever calculated:

```rust
// networks/bitcoin/src/wallet/send.rs:194-202
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...)
  })
}
```

Yet the weight/vbytes estimates at lines 204 and 225-226 pass `payments`, not `tx_outs`:

```rust
// networks/bitcoin/src/wallet/send.rs:204
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` builds its template transaction's output list solely from `payments` plus the optional `change` (send.rs:85-99). The OP_RETURN output — up to 80 bytes of data plus script/output overhead (~92+ vbytes) — is entirely absent from both `vbytes` and `weight`. Consequently:

1. `needed_fee = fee_per_vbyte * vbytes` underprices the transaction by `fee_per_vbyte * ~92` satoshis.
2. The `TooLowFee` minimum-relay check (send.rs:211) validates `needed_fee` against the underestimated `vbytes`, so a transaction can pass locally while being under the true `DEFAULT_MIN_RELAY_TX_FEE` requirement for its actual size.
3. When a change output is created (send.rs:228-233), `change = input_sat - payment_sat - fee_with_change` uses the underestimated fee, inflating the change and shrinking the *actual* fee paid (`sum(inputs) - sum(outputs)`), so the broadcast transaction pays exactly the underestimated fee.
4. The `TooLargeTransaction` check (send.rs:241) uses the underestimated `weight`, so a transaction including a large OP_RETURN can pass the limit while actually exceeding `MAX_STANDARD_TX_WEIGHT` and being rejected as non-standard.

This mirrors the reported bug class precisely: a fixed resource allowance that does not account for the real cost of execution, causing the receiver to be unable to receive funds (the transaction is unrelayable/unconfirmable) rather than an explicitly handled failure.

### Impact Explanation
Any caller of `SignableTransaction::new` who supplies `data` produces a transaction whose real fee rate is lower than the `fee_per_vbyte` it was constructed with — by ~`92 * fee_per_vbyte` satoshis, and whose change output is correspondingly oversized. At low fee rates the transaction falls under the Bitcoin minimum relay fee for its true size and will be rejected by the network; at the extreme, the transaction can exceed `MAX_STANDARD_TX_WEIGHT` and be non-standard. In both cases the inputs are committed to a transaction that cannot confirm: the receiver never receives the funds, and the UTXOs are effectively locked until the transaction is reconstructed. `sign`/`complete` will still produce valid signatures for this malformed transaction, so the failure surfaces only at broadcast.

### Likelihood Explanation
The `data` parameter is a public input to `SignableTransaction::new`; any integrator embedding data (the documented purpose of the `data` argument, per the doc comment "If data is specified, an OP_RETURN output will be added") triggers the underestimation deterministically. It requires no adversarial action beyond supplying the allowed data payload, and it fails silently — no error is returned, the transaction is signed and only fails at relay or sits unconfirmed. The probability depends on `data` usage and chosen fee rate, but the flaw is unconditional once `data` is used.

### Recommendation
Compute weight/vbytes from the actual output set, not from `payments` alone. Pass `tx_outs` (or `payments` plus the OP_RETURN output) into `calculate_weight_vbytes` at both call sites (send.rs:204 and send.rs:225), e.g. by having `calculate_weight_vbytes` accept `&[TxOut]` or by appending the OP_RETURN output to the payment list used for sizing. This ensures `needed_fee`, the `TooLowFee` minimum-relay check, the change amount, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the true transaction size.

### Proof of Concept
```rust
// Conceptual: a 1-input transaction paying `fee_per_vbyte` with an 80-byte
// OP_RETURN. The constructed template omits the OP_RETURN output.
let data = vec![0u8; 80];
let st = SignableTransaction::new(inputs, &payments, Some(change), Some(data), 1).unwrap();
// st.needed_fee() was computed from vbytes that exclude the ~92-vbyte
// OP_RETURN output. The real transaction's vsize is ~92 vbytes larger,
// so the actual fee rate is ~40-50% below the requested 1 sat/vbyte and
// the change output is ~92 sats too large. If the true size crosses the
// minimum relay fee threshold (or MAX_STANDARD_TX_WEIGHT), the signed
// transaction is unrelayable despite passing all local checks.
```