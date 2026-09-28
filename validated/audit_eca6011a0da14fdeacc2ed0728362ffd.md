### Title
OP_RETURN data output excluded from fee/vsize calculation, causing underpaid fees and potentially unrelayable transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes a caller-supplied `OP_RETURN` output into `tx_outs` *before* computing the transaction weight and required fee, but `calculate_weight_vbytes` rebuilds a fresh template transaction using only `payments` (and optionally `change`). The `data` output is never included in the template, so `needed_fee` and the change amount are computed against a vsize that omits up to ~83 bytes of real transaction data. The resulting transaction pays `fee_per_vbyte * vbytes_underestimate`, an effective fee rate lower than requested and potentially below the minimum relay fee — an asymmetric accounting bug directly analogous to crediting rewards without decrementing the fee-adjusted balance.

### Finding Description
In `SignableTransaction::new`, the data output is appended at lines 194–202:

```rust
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}
```

Then at line 204 the fee basis is computed:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` (lines 62–99) constructs its template `Transaction` solely from `payments` and `change` — `data` is not a parameter. The same omission occurs in the change path at lines 224–235: `fee_with_change` is computed over a template without the OP_RETURN, and the change output's value is set to `input_sat - payment_sat - fee_with_change`, locking in the underestimated fee.

Additionally, the minimum-relay-fee sanity check at line 211 uses the same underestimated `vbytes`, so it can pass even though the real transaction's effective fee rate falls below `DEFAULT_MIN_RELAY_TX_FEE`.

### Impact Explanation
When a `SignableTransaction` is built with both `data` and `change`, the fixed total fee (`input_sat - payment_sat - change`) corresponds to a smaller virtual transaction than the real one. The effective sat/vbyte rate is below `fee_per_vbyte`; for an 80-byte OP_RETURN the underestimate is ~83 vbytes. If the requested rate was near the relay minimum, the signed transaction falls below `DEFAULT_MIN_RELAY_TX_FEE` and will be rejected by relay policy — funds committed to the transaction (including the change output) are not spendable until a replacement is constructed and signed. Even when relayable, the protocol pays less than the intended fee, degrading confirmation reliability — a concrete integrity failure of fee accounting reachable purely by supplying `data` to the public `new` constructor.

### Likelihood Explanation
`data` is an explicit, documented parameter of the public API (`"If data is specified, an OP_RETURN output will be added with it"`), so any caller combining a data payload with a change output triggers the miscalculation deterministically. The miscalculation is silent — no error is returned — and the too-low-fee guard cannot catch it because it shares the same underestimated vsize.

### Recommendation
Pass the `data` output (or `tx_outs` directly) into `calculate_weight_vbytes` so the template transaction includes the OP_RETURN output in both the no-change and with-change fee computations, and re-run the `TooLowFee` check against the corrected vbytes.

### Proof of Concept
```rust
// Conceptual trace against networks/bitcoin/src/wallet/send.rs:
// inputs = [one ReceivedOutput worth 50_000 sats]
// payments = [(script, 20_000)]
// change = Some(script)
// data = Some(vec![0u8; 80])   // passes the <=80 check
// fee_per_vbyte = 1 (just above min relay)

// Line 204: vbytes computed from payments only — OP_RETURN not counted.
// Line 227: fee_with_change = 1 * vbytes_without_data
// Line 228-230: change = 50_000 - 20_000 - fee_with_change
// Final tx has 3 outputs incl. ~83-byte OP_RETURN, but pays only
// fee_with_change. Effective rate = fee_with_change / actual_vsize < 1 sat/vb
// → below DEFAULT_MIN_RELAY_TX_FEE → transaction is not relayed and the
// inputs/change are not spendable until re-signed.
```

The asymmetry mirrors the Stargate bug: value flows (the new output) are added to one side of the ledger while the corresponding cost (its serialized size) is never debited from the fee accounting, producing a balance that does not reflect the real obligation.