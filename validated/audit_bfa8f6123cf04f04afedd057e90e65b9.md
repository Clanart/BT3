### Title
Transaction fee is calculated on a vsize that omits the OP_RETURN data output, underpaying the requested fee rate and crediting the shortfall to change - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` adds a caller-supplied `data` OP_RETURN output to `tx_outs`, but computes the transaction weight/vbytes — and therefore `needed_fee` and the change amount — from `payments` only, which excludes the data output. The analog to the reported bug is exact in shape: just as the CDS profit was computed on a base (deposited vs. returned delta) that omitted the price-appreciation component, the fee here is computed on a vsize base that omits an output's bytes.

### Finding Description
At `networks/bitcoin/src/wallet/send.rs:194-202`, the OP_RETURN output is pushed into `tx_outs`. However, the fee sizing calls at `send.rs:204` and `send.rs:225-227` call `Self::calculate_weight_vbytes(tx_ins.len(), payments, ...)` with `payments`, not `tx_outs`, so the witness-weight estimate never includes the data output (up to 80 bytes of payload plus output overhead, ~90+ vbytes). `needed_fee = fee_per_vbyte * vbytes` at line 206 and `fee_with_change` at line 227 are therefore under-estimates. When change is created (`send.rs:228-230`), `value = input_sat - payment_sat - fee_with_change` gives the undercharged amount back to the change output, so the transaction genuinely pays only `needed_fee` while being larger than estimated — an effective fee rate strictly below `fee_per_vbyte`.

Consequences:
- The `TooLowFee` check at `send.rs:211` uses the understated `vbytes`, so it can pass while the real transaction is below `DEFAULT_MIN_RELAY_TX_FEE`, producing a transaction that nodes will not relay.
- Even above the relay floor, the transaction confirms more slowly than the fee rate requested, and `needed_fee()`/`fee()` report a fee the caller believes achieves `fee_per_vbyte` but does not.

### Impact Explanation
Any `SignableTransaction::new` invocation with `data: Some(_)` produces a transaction paying less fee than intended; at low `fee_per_vbyte` it can be unrelayable (funds' inputs effectively stuck until reconstructed at a higher fee), and in all cases the protocol's fee accounting (`needed_fee`, `fee()`) is wrong by the size of the data output. This is an incorrect value-computation on an incomplete base — the same class as the reference report — reachable purely through public function inputs (`payments`, `change`, `data`, `fee_per_vbyte`).

### Likelihood Explanation
Triggering requires `data` to be `Some`; the current production caller (`processor/src/networks/bitcoin.rs:450`) passes `None`, but `SignableTransaction::new` is a public API explicitly documented to accept OP_RETURN data, and any integrator using it hits this unconditionally. Medium likelihood of occurrence, Medium impact (incorrect/insufficient fee, potentially non-propagating transaction).

### Recommendation
Compute weight over the outputs actually serialized: pass `tx_outs` (including the OP_RETURN output) into `calculate_weight_vbytes`, or add the data output to the payment list used for sizing before the calls at `send.rs:204` and `send.rs:226`.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs:194-206
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) });
}
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
// `payments` does NOT contain the OP_RETURN output pushed above
let mut needed_fee = fee_per_vbyte * vbytes; // underestimated
```
With `data = vec![0; 80]`, the real transaction is ~90+ vbytes larger than `vbytes`; with `fee_per_vbyte = 1` on a ~150-vbyte payment transaction, `needed_fee ≈ 150` while the true fee required is ~240 sat/vbyte-scaled — the effective rate falls to ~0.6 sat/vbyte, below the 1 sat/vbyte min-relay rate, yet the `TooLowFee` check on the understated `vbytes` passes. The change output at `send.rs:228-230` then absorbs the difference, and `tx.output` includes the data output never priced.