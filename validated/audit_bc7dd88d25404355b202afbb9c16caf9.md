### Title
`SignableTransaction::new` omits the OP_RETURN output from the weight/vbyte calculation, under-charging the fee and allowing oversized transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` accepts an optional `data` blob that is committed into an OP_RETURN output. When the fee is calculated, `calculate_weight_vbytes` is only called over `payments` and `change`, so the OP_RETURN output is never counted. The resulting `needed_fee`, the minimum-relay-fee check, and the `MAX_STANDARD_TX_WEIGHT` check are all computed on a smaller transaction than the one actually signed. This mirrors the audit bug class: a bound/limit value (`potentialDebt` there, `needed_fee`/`weight` here) computed with a formula that drops a term, producing an incorrect validation result.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` (lines 194-202) before any weight accounting, yet both calls to `calculate_weight_vbytes` pass only `payments` and the optional `change`:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);   // line 204
...
let (weight_with_change, vbytes_with_change) =
    Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));                 // line 225-226
```

`calculate_weight_vbytes` (lines 62-127) builds a mock `Transaction` whose `output` vector is built exclusively from `payments` plus `change`. The up-to-80-byte `data` output (checked at lines 171-173) is entirely absent from the mock, so:

- `needed_fee = fee_per_vbyte * vbytes` (line 206) under-pays by `fee_per_vbyte * (size_of(OP_RETURN output))` — roughly `fee_per_vbyte * (9 + script overhead + data len)` sats.
- The `TooLowFee` gate (line 211) compares this underestimated fee against `DEFAULT_MIN_RELAY_TX_FEE * underestimated_vbytes / 1000`, so a `fee_per_vbyte` at or just above the relay minimum can pass while the real transaction's effective fee rate falls below 1 sat/vB.
- The `MAX_STANDARD_TX_WEIGHT` check (line 241) runs on `weight` that excludes the OP_RETURN output, so a transaction that is actually over the standard weight limit can be constructed and signed.
- The `NotEnoughFunds` check (line 215) also uses the undercounted `needed_fee`, so a spend can be accepted that leaves the real effective fee below what was requested.

The `data` bytes and `fee_per_vbyte` are both caller-controlled public inputs, so an unprivileged party feeding a large `data` blob reaches this path directly.

### Impact Explanation
The signed transaction pays `fee_per_vbyte` only on the payment/change bytes, not on the full transaction. For `data` near the 80-byte cap, the real effective fee rate can drop below `DEFAULT_MIN_RELAY_TX_FEE`, causing the transaction to be rejected from the mempool even though all checks in `new` passed — the threshold wallet's funds are locked in a non-propagating spend (a DoS on the wallet's ability to move funds). Similarly, a transaction that exceeds `MAX_STANDARD_TX_WEIGHT` once the data output is counted will be constructed and signed but rejected by the network. Both are silent mis-validation: the constructor returns `Ok` for a transaction the Bitcoin network will not accept.

### Likelihood Explanation
Any caller that attaches metadata via `data` (which Serai uses to carry protocol payloads on outputs) hits this whenever `data` is non-empty — the OP_RETURN output is always added but never weighed. With `fee_per_vbyte` near the relay minimum, the miscount directly decides whether the transaction propagates. No collusion or privileged access is required; `data` and `fee_per_vbyte` are ordinary public arguments to `SignableTransaction::new`.

### Recommendation
Pass the fully-formed output set into the weight calculation, or add the OP_RETURN output explicitly inside `calculate_weight_vbytes`. Concretely, compute weight over `tx_outs` after all outputs (payments, OP_RETURN, and change) are determined, e.g. restructure so `data` is incorporated before the `calculate_weight_vbytes` calls, and call it with the complete output list for both the with-change and without-change variants. Optionally also account for the maximum `data` size when deciding whether a change output's addition keeps the transaction under `MAX_STANDARD_TX_WEIGHT`.

### Proof of Concept
```rust
// Construct a transaction with:
//   one P2TR input of `payment + fee`, one payment output,
//   80 bytes of `data`, fee_per_vbyte = 1 (min relay rate).
let data = vec![0u8; 80];
let tx = SignableTransaction::new(
    vec![input],                        // ReceivedOutput paying (payment + needed_fee)
    &[(payment_script, 100_000)],
    None,
    Some(data),
    1,                                  // sat/vB
).unwrap();
```
- The OP_RETURN output (~91 bytes: 8-byte value + script overhead + 80-byte push) is in `tx.tx.output` but contributed 0 vbytes to `vbytes`.
- `needed_fee` covers only the payment-only transaction; the real transaction's effective rate is `needed_fee / real_vbytes < 1 sat/vB`, below `DEFAULT_MIN_RELAY_TX_FEE`, so nodes reject it.
- A unit test asserting `tx.weight()` (of the final `tx`) vs the `weight`/`vbytes` returned by `calculate_weight_vbytes` shows a discrepancy exactly equal to the OP_RETURN output's serialized size; adding the output to the mock makes them equal.