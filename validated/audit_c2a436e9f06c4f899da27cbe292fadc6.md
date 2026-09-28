### Title
`SignableTransaction::new` underpays fees because transaction weight is measured before the OP_RETURN output is added — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The JUSDBank bug class is "a rate/factor computed at the wrong point in the flow, so the amount ultimately paid out differs from what the caller intended." The same shape exists in `SignableTransaction::new`: the fee (`needed_fee`) and the change amount are derived from `calculate_weight_vbytes`, which reconstructs the transaction using **only `payments`** — the OP_RETURN `data` output (up to 80 bytes, already pushed into `tx_outs` at send.rs:194-202) is never included in the weight/vbyte measurement.

### Finding Description
`calculate_weight_vbytes(inputs, payments, change)` builds a mock `Transaction` whose outputs come exclusively from `payments` plus an optional change output (send.rs:85-99). In `SignableTransaction::new`:

- `vbytes` is computed at send.rs:204 and `needed_fee = fee_per_vbyte * vbytes` at send.rs:206 — *after* the OP_RETURN output was pushed to `tx_outs` but with a measurement that ignores it.
- The change branch (send.rs:224-234) recomputes `vbytes_with_change` the same incomplete way, then sets `change = input_sat - payment_sat - fee_with_change`.

Because the OP_RETURN output adds real weight (script_pubkey length prefix + up to ~89 bytes → ~23+ extra vbytes) that is never priced:

1. The actual fee paid (`sum(inputs) - sum(outputs)`, per `fee()` at send.rs:138-141) corresponds to a *lower effective fee rate* than `fee_per_vbyte` requested.
2. The `TooLowFee` check at send.rs:211 validates `needed_fee` against the underestimated `vbytes`, so a transaction whose true fee rate is below `DEFAULT_MIN_RELAY_TX_FEE` can pass validation and be produced.
3. The dust check for change (`value >= DUST` at send.rs:229) is evaluated against the underpriced fee, so change can be created where a correctly-priced fee would have dropped it.

This mirrors the report exactly: the "rate" (fee per vbyte) is applied to a stale/wrong base (a transaction missing an output), so the residual amount (change) and the economic safety check (min relay fee) are both wrong.

### Impact Explanation
A transaction built with `data = Some(_)` pays less fee than intended. At marginal fee rates (e.g., `fee_per_vbyte = 1`, the common minimum), the ~10–25% size underestimate from a near-maximal OP_RETURN pushes the true fee rate below the minimum relay fee — the transaction will be rejected/non-propagating on the Bitcoin network while the signer has already produced it. Funds are frozen in an unbroadcastable/unconfirmable transaction, i.e., the user "gets less money than expected" out of the withdrawal flow until a replacement is constructed. The change output is also inflated by the uncharged fee, violating the invariant tested at tests/wallet.rs:269 (`needed_fee == vsize * FEE` holds only because the test never passes `data`).

### Likelihood Explanation
- `data` is attacker/integrator-controlled transaction data passed straight to `SignableTransaction::new`; the bug triggers deterministically whenever `data.is_some()`.
- Within this repo, the only production caller passes `None` (`processor/src/networks/bitcoin.rs:450`), so the in-tree scheduler never hits it — but the wallet library is in-scope production code and `data` is a public, caller-supplied parameter, so any consumer supplying OP_RETURN data reaches the path.
- No timing or adversary cooperation is required; the miscalculation is unconditional for that input class.

Severity: **Medium** — deterministic fee underpayment / stuck funds, bounded by caller willingness to set `data`, and recoverable by re-signing with a corrected fee.

### Recommendation
Include the OP_RETURN output (and its true `script_pubkey` length) in the transaction used by `calculate_weight_vbytes`. Cleanest fix: build the mock `tx.output` from the final `tx_outs` list (payments + optional OP_RETURN + optional change) rather than from `payments` alone, or pass the serialized data length in so the vbyte estimate matches the real transaction. Re-run the `TooLowFee` check against the final vbyte count including all outputs.

### Proof of Concept
Conceptual reproduction against the current code:

```rust
// networks/bitcoin/tests/wallet.rs style
let tx = SignableTransaction::new(
  vec![output],                       // one funded input
  &[(payment_script, DUST)],          // minimal payment
  Some(change_addr),                  // change output
  Some(vec![0u8; 80]),                // maximal OP_RETURN
  1,                                  // fee_per_vbyte = 1 (minimum relay)
).unwrap();

let actual_vsize = /* vsize of tx.tx including the OP_RETURN output */;
// tx.needed_fee() == 1 * vsize_without_opreturn  <  actual_vsize
// effective fee rate = tx.fee() / actual_vsize < 1 sat/vbyte
// => below DEFAULT_MIN_RELAY_TX_FEE; node rejects despite TooLowFee check passing
```

`needed_fee` is computed from a transaction missing the 80-byte OP_RETURN output, so `tx.fee()` (inputs minus outputs) divided by the real vsize yields a fee rate strictly below the requested `fee_per_vbyte` — and below the relay minimum when `fee_per_vbyte * data_vbytes < needed_fee` margin, which holds for `fee_per_vbyte = 1` and any non-trivial `data`.

Caveat: I could not exhaustively verify every caller of `SignableTransaction::new` within the iteration budget; the sole observed in-repo call site passes `data: None`, so the practical exploitability is contingent on external consumers of the library supplying OP_RETURN data.