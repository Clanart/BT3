### Title
OP_RETURN output omitted from weight/fee calculation, producing underpaid fees and over-credited change - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the Allo report (an accounting boundary error in `msg.value` vs `baseFee + _amount` causes mis-valued funds), `SignableTransaction::new` computes transaction weight, the required fee, and the change amount while omitting the OP_RETURN output that was already appended to `tx_outs`. The result is a systematically incorrect fee/change accounting: the signed transaction carries one more output than was priced, so the actual fee rate is lower than the caller-specified `fee_per_vbyte` and can fall below the minimum relay fee, while any change output is credited more satoshis than the inputs justify at the target rate.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` before the weight is measured:

```rust
// networks/bitcoin/src/wallet/send.rs
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...)
  })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` builds its template transaction solely from `inputs`, `payments`, and the optional `change` — it never sees the OP_RETURN output that is really present in `tx_outs`. Consequently:

1. `vbytes` (and `vbytes_with_change`) are underestimated by the serialized size of the OP_RETURN output (roughly `8 + 1 + 1 + len(data)` base bytes plus output overhead — up to ~90 vbytes for the permitted 80 bytes of data).
2. `needed_fee = fee_per_vbyte * vbytes` is therefore too small.
3. The `TooLowFee` check (`needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) is evaluated against this understated value, so a caller-supplied fee rate at or slightly above the minimum can produce a real transaction whose effective fee rate is below the minimum relay fee.
4. In the change branch, `value = input_sat - payment_sat - fee_with_change` over-credits the change output by the unpaid fee for the OP_RETURN output's vbytes; the actual fee paid (`sum(inputs) - sum(outputs)`, see `fee()`) equals the underestimated `fee_with_change`, not the true required fee for the transaction's real size.

Like the Allo issue, this is an incorrect accounting formula over the funds supplied: the comparison/`checked_sub` logic is internally consistent, but it is computed against a transaction that is missing an output, so the arithmetic no longer reflects what is actually signed and broadcast.

### Impact Explanation
Any caller specifying `data` produces a transaction paying less than `fee_per_vbyte` requests — an incorrect fee formula. For data lengths near the 80-byte cap the shortfall is ~90 vbytes' worth of fee; at marginal fee rates this drops the transaction below `DEFAULT_MIN_RELAY_TX_FEE`, making it unrelayable/unconfirmable and effectively locking the spent inputs (the library exposes no RBF/bump path). Change outputs are also credited satoshis that should have gone to the fee, i.e., the wallet's accounting of `needed_fee`/`fee()` diverges from the intended fee rate. This maps to "an incorrect verifier formula" / incorrect fee math over user-supplied transaction data.

### Likelihood Explanation
Reachable by any caller of `SignableTransaction::new` with `data: Some(...)` — a public, unprivileged input (arbitrary bytes up to 80 bytes are accepted per the `TooMuchData` check). No malicious validator or leaked key required. However, impact is bounded: the transaction is still valid, and a sufficiently above-minimum `fee_per_vbyte` masks the shortfall; the failure mode is a stuck/unrelayable transaction rather than theft.

### Recommendation
Build the weight template from the actual outputs being created. Pass the real `tx_outs` (or `payments` plus the OP_RETURN output) into `calculate_weight_vbytes` instead of `payments`, e.g. compute `let template_outputs = tx_outs` (and `tx_outs` + change for the change variant) before measuring, so `needed_fee`, `fee_with_change`, the `TooLowFee` check, and the change amount all reflect the true transaction size.

### Proof of Concept
- Construct inputs with `input_sat` comfortably above `payment_sat`, a `change` script, and `data = Some(vec![0; 80])`.
- `SignableTransaction::new` returns a tx whose outputs include the OP_RETURN (~91 serialized bytes), yet `needed_fee()` was computed on a template without it.
- `signable.fee()` equals `fee_with_change = fee_per_vbyte * vbytes_with_change` where `vbytes_with_change` omits ~91+ weight units; the real transaction's effective fee rate is `fee / actual_vbytes < fee_per_vbyte`, and the change `TxOut` is over-valued by `fee_per_vbyte * omitted_vbytes` satoshis.
- With `fee_per_vbyte` at the minimum relay boundary, `sendrawtransaction` rejects the signed transaction for insufficient fee despite `SignableTransaction::new` having passed its own `TooLowFee` check.