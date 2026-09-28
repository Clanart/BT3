### Title
`SignableTransaction::new` computes `needed_fee` and the weight bound without the OP_RETURN `data` output that the signed transaction actually carries - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the xPYT `assetBalance` not being decremented by the outbound `pounderReward`, `SignableTransaction::new` tracks the transaction's cost (`needed_fee`, `weight`) without accounting for the OP_RETURN `data` output that is pushed into `tx_outs` and therefore into the transaction that is actually signed and broadcast. The tracked fee/size diverges from the real transaction: the signed TX is larger than estimated, so its *effective* fee rate is lower than `fee_per_vbyte`, and the `MAX_STANDARD_TX_WEIGHT` check is performed against a weight that omits the data output.

### Finding Description
`SignableTransaction::new` appends the OP_RETURN output to `tx_outs` at `send.rs:194-202`, but both calls to `calculate_weight_vbytes` (lines 204 and 225-226) pass `payments`, which does **not** include the `data` output:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
...
let (weight_with_change, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
```

Inside `calculate_weight_vbytes` (lines 85-99), `tx.output` is built solely from `payments` plus an optional change output — no OP_RETURN. Consequently:

- `needed_fee = fee_per_vbyte * vbytes` (line 206) and `fee_with_change` (line 227) are computed for a transaction missing the `data` output (~9 bytes of output overhead + up to ~82 bytes of pushdata, since `data` may be up to 80 bytes per the `TooMuchData` check at line 171).
- The change value at lines 228-233 is computed against this underestimated fee, so the change output is *higher* than it should be at the requested rate — the shortfall is silently absorbed as a lower effective fee rate, exactly the "tracked balance not reduced by the real outflow" pattern.
- The `weight > MAX_STANDARD_TX_WEIGHT` check at line 241 uses the underestimated `weight`, so a transaction can pass the check while the real transaction exceeds the standardness limit.
- The minimum-relay-fee check at line 211 and `TooLowFee` are evaluated against the underestimated size; a caller specifying `fee_per_vbyte` at the relay minimum produces a transaction whose actual fee rate falls below the relay minimum once the data output is included.

`fee()` (lines 138-141) reports `sum(inputs) - sum(outputs)` — the nominal fee — which equals `needed_fee` when change is created, yet the real transaction is larger. Any caller comparing `needed_fee`/`fee` to a target rate or size bound is working from a ledger entry (`needed_fee`, `weight`) that does not reflect the transaction actually produced.

### Impact Explanation
The signed transaction's actual vsize exceeds the estimate by the full size of the OP_RETURN output (up to ~91 vbytes). Concrete consequences:

- **Under-priced fee rate**: the effective sat/vbyte is `needed_fee / real_vsize < fee_per_vbyte`. At low specified rates this can drop the TX below the mempool minimum relay fee, causing the signed transaction to be rejected — the inputs are committed to this exact TX (`Prevouts::All` sighash at lines 373-390), so the spend is stuck until a new transaction is constructed and re-signed.
- **Standardness bound bypass**: a TX constructed with `weight` just under `MAX_STANDARD_TX_WEIGHT` will exceed it once the data output is added, producing a transaction no standard node will relay despite the constructor returning `Ok`.
- **Fee accounting mismatch**: `needed_fee()`/`fee()` report a fee that does not correspond to the real transaction's size, so upstream accounting that amortizes `needed_fee` over payments and computes expected change (`inputs - payments - needed_fee`) underestimates the true cost weight and mis-attributes the discrepancy.

This mirrors the report's pattern: a value that is part of the actual outflow (here, the size contributed by the data output, which consumes fee budget) is omitted from the tracked quantity, so later consumers of the tracked value misprice subsequent operations.

### Likelihood Explanation
Reachable by any party able to supply the `data` argument to `SignableTransaction::new` — a public API input (`data: Option<Vec<u8>>`, up to 80 bytes). The divergence is deterministic whenever `data.is_some()`; no race or special state is required. Impact is limited to fee-rate degradation / non-relayability / bound-check bypass rather than direct theft, so severity is Medium: funds are not stolen, but a constructed transaction can be unbroadcastable or mispriced relative to the caller's requested parameters.

### Recommendation
Include the `data` output in the size estimation. Options:

- Pass the fully-built `tx_outs` (or a `payments + OP_RETURN` vector) into `calculate_weight_vbytes` instead of `payments`, so `weight`, `vbytes`, `needed_fee`, `fee_with_change`, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the transaction actually produced.
- Alternatively, add the serialized OP_RETURN output's weight (`output.weight()` or a fixed `8 + script_len` scaled by 4) to `weight`/`vbytes` after calling `calculate_weight_vbytes`.

Also add a test constructing a `SignableTransaction` with `data` and asserting `tx.vsize() * fee_per_vbyte == needed_fee` and `tx.weight() <= MAX_STANDARD_TX_WEIGHT` on the *signed* transaction — analogously to the report's recommendation of testing correct balance updates.

### Proof of Concept
```rust
// Construct a transaction with an 80-byte OP_RETURN payload.
let data = vec![0u8; 80];
let st = SignableTransaction::new(
    vec![output],                 // a ReceivedOutput with ample value
    &[(addr(), 546)],             // minimal payment
    Some(change_addr()),          // change absorbs fee
    Some(data),                   // OP_RETURN output
    FEE,                          // e.g. minimum relay rate
).unwrap();

// The signed TX includes the OP_RETURN output, but needed_fee was computed
// for a transaction WITHOUT it:
let real_vsize = st.transaction().vsize() as u64;
let implied_vsize = st.needed_fee() / FEE;
// BUG: implied_vsize < real_vsize — the OP_RETURN output (~90 vbytes) is
// unaccounted, so the effective fee rate is FEE * implied_vsize / real_vsize.
assert!(implied_vsize < real_vsize);

// Similarly, the TooLowFee check at line 211 and the MAX_STANDARD_TX_WEIGHT
// check at line 241 both used the underestimated weight, so a data-carrying
// TX can be produced that fails real relay policy at the requested rate.
```

Root cause: `calculate_weight_vbytes` is always called with `payments` (`send.rs:204`, `send.rs:225-226`), while the `data` output is appended to `tx_outs` (`send.rs:194-202`) and included in the final `Transaction` (`send.rs:245-251`) — the tracked size/fee ledger never sees the output the signed transaction actually spends on.