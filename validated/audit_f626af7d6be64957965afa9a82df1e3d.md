### Title
OP_RETURN Data Output Omitted From Fee/Weight Calculation, Causing Underpriced or Unbroadcastable Transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes the `OP_RETURN` output into `tx_outs` before calculating the transaction weight, but `calculate_weight_vbytes` only models `payments` and `change` — the data output is never included in the weight/vbytes used to derive `needed_fee` or to enforce the minimum relay fee check. The result is a fee accounting inconsistency across construction paths: when `data` is set, the declared `needed_fee` (and the guaranteed minimum fee rate) is computed for a smaller transaction than the one actually produced and signed.

### Finding Description
In `SignableTransaction::new`, the `OP_RETURN` output is appended to `tx_outs` at `networks/bitcoin/src/wallet/send.rs:195`, but both weight calculations (lines 204 and 225-226) pass only `payments` and `change` to `calculate_weight_vbytes`, which builds its template `Transaction` exclusively from those arguments (`send.rs:68-99`). Consequently:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206) undercounts by the serialized size of the OP_RETURN output (~9 + 80 bytes, up to ~89 vbytes with the script length prefix).
2. The minimum-relay check `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` (line 211) validates a fee against an underestimated vsize, so a transaction can pass this check while its real fee rate is below the relay minimum.
3. The change calculation `input_sat - payment_sat - fee_with_change` (line 228) allocates change based on the underpriced fee, so `fee()` (lines 138-141, actual inputs minus outputs) reports a fee smaller than `needed_fee` relative to true vsize — the builder returns a transaction whose effective fee rate is strictly less than the `fee_per_vbyte` the caller requested.

This mirrors the report's class: an inconsistent fee formula between paths causes one party's cost (here, the fee owed to the network) to be silently misallocated — the change output absorbs value that should have gone to the fee, or the transaction is produced at a fee rate below what the caller specified and below relay minimums.

### Impact Explanation
A transaction carrying an OP_RETURN payload is signed and broadcast with a fee rate lower than requested — potentially below `DEFAULT_MIN_RELAY_TX_FEE` effective rate despite passing the explicit guard — causing relay rejection or a stuck/unconfirmed transaction. In the threshold-multisig context this builder serves (FROST `TransactionMachine`/`TransactionSignMachine`), a stuck spend ties up the UTXOs committed via `Prevouts::All` until replaced, and change accounting differs from what signers agreed to (the signed change is larger than an honestly-priced transaction would produce). Unprivileged callers controlling the `data` field (transaction metadata they cause to be signed) trigger the mispriced output.

### Likelihood Explanation
Deterministic whenever `data` is `Some`: the discrepancy grows linearly with data length (up to the 80-byte cap). Exploitation for direct theft is not possible — the misallocation favors the change recipient — but the guarantee violations (specified fee rate, minimum relay fee) are unconditional on the code path.

### Recommendation
Include the OP_RETURN output in the weight model. Either extend `calculate_weight_vbytes` to accept the data length (pushing a `TxOut` with `ScriptBuf::new_op_return(...)`), or construct the template `Transaction` from the already-built `tx_outs` (which already contains the data output at line 195) instead of reconstructing outputs from `payments`. Recompute `vbytes`/`needed_fee` after `tx_outs` is finalized, before the `TooLowFee` and `NotEnoughFunds` checks.

### Proof of Concept
```rust
// Given inputs totaling input_sat, payments totaling payment_sat,
// change = Some(...), data = Some(vec![0u8; 80])
let tx_no_data = SignableTransaction::new(inputs.clone(), payments, change.clone(), None, f).unwrap();
let tx_data    = SignableTransaction::new(inputs.clone(), payments, change.clone(), Some(vec![0; 80]), f).unwrap();

// Both compute identical needed_fee despite tx_data being ~89 vbytes larger:
assert_eq!(tx_no_data.needed_fee(), tx_data.needed_fee()); // holds — bug

// Real transaction weight is larger, so effective fee rate < f:
// tx_data.fee() == tx_no_data.fee(), yet tx_data's real vsize is higher.
// With f = minimum relay fee, tx_data falls below the relay minimum
// while passing the check at send.rs:211.
```