### Title
`SignableTransaction` omits the variable-length OP_RETURN output from its fee/weight calculation, underpaying the target fee rate — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The DBI bug class is "buffer/size computation assumes a fixed byte count per element while actual elements are variable-length." In Serai, `SignableTransaction::new` builds the real output list (`tx_outs`) including an OP_RETURN output carrying up to 80 bytes of caller data, but the fee is derived from `calculate_weight_vbytes`, which is only given `payments` — the OP_RETURN output is never included. The resulting `needed_fee` (and the dust-vs-change check) are computed against a transaction smaller than the one actually signed and broadcast, so the produced transaction systematically underpays relative to `fee_per_vbyte` whenever `data` is present.

### Finding Description
`SignableTransaction::new` pushes the OP_RETURN output into `tx_outs` at construction:

```rust
// networks/bitcoin/src/wallet/send.rs:193-202
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(
      PushBytesBuf::try_from(data)
        .expect("data didn't fit into PushBytes depsite being checked"),
    ),
  })
}
```

but the weight/vbyte computation is invoked only with `payments`:

```rust
// networks/bitcoin/src/wallet/send.rs:204
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

and inside `calculate_weight_vbytes` the outputs are reconstructed solely from `payments` plus optional `change`:

```rust
// networks/bitcoin/src/wallet/send.rs:85-99
output: payments.iter().map(|payment| TxOut { ... }).collect(),
if let Some(change) = change { tx.output.push(...) }
```

The `data` OP_RETURN output (up to ~91 serialized bytes: 8-byte value + script length + `OP_RETURN` + push opcode + 80 bytes) is missing from both the weight estimate and the minimum-fee check at line 211. The minimum-relay-fee guard uses the underestimated `vbytes`, so a transaction with `data` can pass `TooLowFee` while its actual feerate falls below `DEFAULT_MIN_RELAY_TX_FEE`, and can pass `NotEnoughFunds` while `input_sat` is insufficient to cover `payment_sat + true fee` — the shortfall being absorbed into (or exceeding) the intended change value. Note the change-value computation at lines 224-235 also uses the underestimated `vbytes_with_change`, so change is slightly overstated and the implied `fee()` at line 139 ends up lower than the "needed" fee that was computed.

### Impact Explanation
Any `SignableTransaction` created with `data` produces a signed transaction whose effective feerate is lower than `fee_per_vbyte`. At the boundary this yields a transaction below the minimum relay fee that propagates poorly or not at all — funds are committed to an outpoint whose spend is stuck — while the code reports `needed_fee`/`fee` consistent with a higher rate. The change output is also credited more than the accurate fee accounting would leave, so `fee()` diverges from the caller-requested rate. Effect: concrete fee accounting error on a signed transaction, the Serai analog of "allocated N bytes but wrote more" — here, charged for N vbytes but serialized more.

### Likelihood Explanation
The bug triggers whenever `SignableTransaction::new` is called with `Some(data)` and a low-to-marginal `fee_per_vbyte`. Reachability is internal: `data` is supplied by the caller of the wallet API (Serai's own transaction construction for eventful/multisig-rotation transactions), not directly by an unprivileged network peer. A peer cannot inject the data field, so this is an incorrect-formula/accounting bug rather than a remotely triggerable one — which bounds severity at Medium. With larger `data` and fees near `DEFAULT_MIN_RELAY_TX_FEE`, the produced transaction can be non-relayable despite passing all checks.

### Recommendation
Include the OP_RETURN output in the weight/vbyte estimate. Construct the OP_RETURN `TxOut` before calling `calculate_weight_vbytes` and pass it through (e.g., extend `calculate_weight_vbytes` to accept the full output list, or add the data output into `payments`-style accounting without its zero value affecting `payment_sat`). Recompute `vbytes`, `needed_fee`, the `TooLowFee` check, and `fee_with_change` against the complete output set. Also add a regression test asserting `tx.weight()`/`vsize` of the final transaction matches the value used for `needed_fee`.

### Proof of Concept
```rust
let inputs = vec![received_output_with(100_000 /* sats */)];
let payments = vec![(script_buf, 50_000)];
let data = vec![0xAA; 80];

let st = SignableTransaction::new(inputs, &payments, Some(change_script), Some(data), 1).unwrap();

// Final transaction includes the ~91-byte OP_RETURN output
let actual_vsize = st.transaction().vsize() as u64;
// needed_fee was computed as 1 * vbytes_without_opreturn
assert!(st.needed_fee() < actual_vsize); // fee < requested 1 sat/vbyte rate
assert!(st.fee() < actual_vsize);        // effective feerate below requested
```
With `fee_per_vbyte = 1` (below/at the min relay boundary after the underestimated-vbytes check), the signed transaction can carry an effective feerate below `DEFAULT_MIN_RELAY_TX_FEE`, making it non-relayable despite `new` having accepted it.