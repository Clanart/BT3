### Title
SignableTransaction omits the OP_RETURN `data` output from weight/vbyte calculation, underpaying the fee and producing transactions that cannot be relayed - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The Serai Bitcoin wallet's `SignableTransaction::new` accepts an optional `data` payload which is turned into a real `OP_RETURN` transaction output, yet `calculate_weight_vbytes` is invoked with only `payments` — never including that output. The resulting `needed_fee` and the minimum-relay-fee check are computed on a lighter transaction than the one actually signed and broadcast, an accounting error analogous to `calculateMint` charging the user for a mint whose computed amount silently collapses.

### Finding Description
`SignableTransaction::new` pushes the `OP_RETURN` output into `tx_outs` before the fee math:

```rust
// networks/bitcoin/src/wallet/send.rs:193-206
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` builds a mock `Transaction` whose `output` vector is populated **only** from `payments` (plus the optional `change`), so the up-to-80-byte `data` output (≈90 serialized bytes: 8-byte amount + ~82-byte script, ≈360 weight units ≈ 90 vbytes) is never counted. The same omission occurs in the change branch, which also passes only `payments`. Consequently `needed_fee` is short by roughly `90 * fee_per_vbyte` satoshis relative to the true size, the `TooLowFee` guard passes on the understated size, and the `MAX_STANDARD_TX_WEIGHT` bound is checked against the smaller weight. The final `tx` does contain the extra output, so `fee()` (inputs − outputs) matches `needed_fee` numerically — the transaction is simply oversized for the fee it carries. A caller at the minimum relay rate (e.g. `fee_per_vbyte = 1` sat/vbyte, or any rate within ~90 vbytes of the minimum) produces a transaction below `DEFAULT_MIN_RELAY_TX_FEE` that the network will not relay or confirm, permanently stalling the spend of the inputs.

### Impact Explanation
An unprivileged party causes a `data`-bearing transaction to be constructed/signed against the multisig (the `data` OP_RETURN path is exactly how arbitrary instruction payloads travel with a transfer). The signed transaction underpays the relay fee for its true vsize, so it is rejected from mempool propagation: the consumed `ReceivedOutput`s are effectively locked — funds committed to a spend that cannot confirm — until an entirely new plan/transaction is constructed at a higher effective rate. This is fund loss/lockup stemming from incorrect accounting of user-supplied bytes, matching the report's class (accounting computed on incomplete terms yields a result the protocol silently accepts).

### Likelihood Explanation
The defect triggers deterministically whenever `data: Some(_)` is supplied — no race or privileged position needed; the payload size (up to 80 bytes) is attacker-influenced, maximizing the shortfall. It manifests whenever the negotiated `fee_per_vbyte` is at or near the relay minimum, which is precisely where `TooLowFee` is supposed to protect the caller.

### Recommendation
Include the `data` output in the mock transaction inside `calculate_weight_vbytes` (pass the `data` length or a constructed `OP_RETURN` `TxOut` so both weight and vbytes account for it), or add its serialized size to `weight`/`vbytes` before computing `needed_fee`. Additionally, re-check `MAX_STANDARD_TX_WEIGHT` against the weight *including* the data output and the change output actually emitted.

### Proof of Concept
Construct `SignableTransaction::new(inputs, &payments, None, Some(vec![0u8; 80]), fee_per_vbyte = 1)`. `calculate_weight_vbytes` computes `vbytes` for a TX without the OP_RETURN output, so `needed_fee = vbytes` sats passes the `DEFAULT_MIN_RELAY_TX_FEE` check at line 211. The returned `tx` contains the extra ~90-byte output, making its real vsize `vbytes + ~90` while paying only `vbytes` sats — an effective rate of `< 1` sat/vbyte. `send_raw_transaction`/`testmempoolaccept` rejects it as `min relay fee not met`, and the spent inputs cannot be recovered by this transaction.