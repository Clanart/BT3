### Title
OP_RETURN data output is excluded from the transaction weight calculation, causing `needed_fee` and change to be miscalculated - ([File: networks/bitcoin/src/wallet/send.rs](https://github.com/bsaldua/serai--011/blob/main/networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the Olympus report — where rewards routed to `owner()` bypassed the protocol's fee accounting — `SignableTransaction::new` computes the transaction's weight and required fee using only `payments` and the optional `change` output, while silently omitting the OP_RETURN `data` output that is actually included in the final transaction. The value that should have been allocated to the miner fee is instead routed into the change output (or simply never accounted for), so the real fee rate is lower than the `fee_per_vbyte` the caller specified and may fall below Bitcoin's minimum relay fee, producing a signed transaction the network will not relay.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is appended to `tx_outs` at `send.rs:194-202` *before* the weight calculation, but the weight/vbyte calculation at `send.rs:204` passes only `payments` and `None` for change to `calculate_weight_vbytes`:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` builds its template transaction solely from `payments` plus an optional change output (`send.rs:85-99`), so the OP_RETURN output — up to ~91 bytes of witness-discount-exempt payload for the maximum 80 bytes of data — is never counted. The same omission occurs for the change-aware recalculation at `send.rs:225-226`, which again passes `payments` rather than the actual `tx_outs` that include the data output.

Consequently `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`, `send.rs:227-232`) is under-computed whenever `data.is_some()`. Since the actual fee paid equals `sum(inputs) - sum(outputs)`, the shortfall is absorbed by crediting the missing fee's value to the change output at `send.rs:230` — exactly the "value routed to the wrong destination, bypassing the intended fee" shape of the reference bug.

The minimum-fee check at `send.rs:211` also uses the underestimated `vbytes`, so a transaction can pass the `TooLowFee` gate while its real fee rate is below `DEFAULT_MIN_RELAY_TX_FEE`.

### Impact Explanation
- Any caller specifying `data` produces a transaction whose actual fee rate is strictly less than the requested `fee_per_vbyte`, with the difference (up to ~91 vbytes worth of fee) misdirected into change.
- If `fee_per_vbyte` is near the minimum relay rate, the signed transaction is non-standard and will be rejected by the Bitcoin P2P network. The consumed `ReceivedOutput`s remain locked in a transaction that cannot broadcast, requiring re-construction and a new FROST signing round — effectively freezing those funds from the signers' perspective.
- The bug is reachable purely through public inputs: `data` is caller-supplied transaction data and `payments`/`change`/`fee_per_vbyte` are ordinary API parameters.

### Likelihood Explanation
Any use of `SignableTransaction::new` with a non-`None` `data` argument triggers the miscalculation deterministically. Whether it becomes a relay failure depends on the requested fee rate and the data size; at low fee rates with large data payloads, an unbroadcastable transaction is likely. In all cases the produced transaction deviates from the caller-specified fee policy.

### Recommendation
Include the OP_RETURN output in the weight template. Either pass the fully-constructed `tx_outs` (or `payments` plus a synthetic OP_RETURN output) into `calculate_weight_vbytes` at `send.rs:204`, and similarly include it when computing `weight_with_change`/`vbytes_with_change` at `send.rs:225-226`. For example, build the template `tx.output` from the actual `tx_outs` vector rather than re-deriving outputs from `payments` alone.

### Proof of Concept
```rust
// Conceptual reproduction
let inputs = vec![received_output];           // single input, e.g. 100_000 sats
let payments = vec![(addr(), 10_000)];
let data = Some(vec![0u8; 80]);               // max-size OP_RETURN

let tx = SignableTransaction::new(inputs, &payments, Some(change_addr()), data, 1).unwrap();

// The signed transaction will contain an extra ~91-byte OP_RETURN output
// that was never included in `vbytes`. Hence:
//   tx.transaction().vsize() > vbytes_used_for_needed_fee
//   actual_fee_rate = tx.fee() / tx.transaction().vsize()  <  1 sat/vB
// With inputs sized so change is large, the ~91 sats intended for the fee
// appear instead in the change output; at the boundary, needed_fee passes
// the TooLowFee check while the real feerate is below min-relay.
```
Specifically, `send.rs:194-202` pushes the OP_RETURN `TxOut` into `tx_outs`, while `send.rs:204` computes `vbytes` from `payments` only, and `send.rs:230` credits change with `input_sat - payment_sat - fee_with_change` — so every byte of the data output's weight is fee that gets redirected into change.