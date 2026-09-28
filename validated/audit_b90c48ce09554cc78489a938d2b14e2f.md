### Title
`SignableTransaction::new` omits the OP_RETURN data output from the weight/fee calculation, underpaying the fee and over-crediting change - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the Predy finding where excess tokens are routed to the wrong destination, `SignableTransaction::new` computes the transaction weight and required fee from only `payments` (plus optional change), even though it has already appended a caller-supplied OP_RETURN `data` output to the real transaction. The excess funds (the leftover that should have become fee) are instead credited to the change output, and the transaction's actual fee rate ends up lower than `fee_per_vbyte` — potentially below the minimum relay fee, leaving the multisig's funds stuck in an unbroadcastable transaction.

### Finding Description
`SignableTransaction::new` builds `tx_outs` including the OP_RETURN output at lines 194–202, but then calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204, which reconstructs a template `Transaction` whose outputs are only `payments` — the `data` output is never included. The same omission occurs in the change path at lines 225–227.

Because the OP_RETURN output (a fixed-size `TxOut` header plus up to 80 bytes of pushed data, ~11–92 bytes ≈ 44–368 weight units ≈ 11–92 vbytes) is missing from the template:

- `needed_fee = fee_per_vbyte * vbytes` under-counts, so the checked minimum-fee comparison (`needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) is performed against a smaller `vbytes` than the real transaction will have.
- The change amount `input_sat - (payment_sat + fee_with_change)` is too large, sweeping the missing fee into the change output (the "excess sent to the wrong recipient" analog: value meant for miners is credited to change).
- The final transaction's effective fee rate is `needed_fee / actual_vsize < fee_per_vbyte`. When `fee_per_vbyte` is at/near the relay minimum, the signed transaction is below `DEFAULT_MIN_RELAY_TX_FEE` for its true size and will be rejected by Bitcoin nodes' mempool — yet the Serai processor will have signed and may attempt to broadcast it, leaving the inputs' outpoints committed to a non-propagating TX.

Relevant code:

```rust
// send.rs:194-204 — data output added to tx_outs ...
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}

// ... but fee is computed only from `payments`, excluding `data`
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

```rust
// send.rs:85-99 — template tx built from payments + change only; data is never passed
output: payments.iter().map(|payment| TxOut { ... }).collect(),
if let Some(change) = change {
  tx.output.push(TxOut { value: Amount::ZERO, script_pubkey: change.clone() });
}
```

### Impact Explanation
An unprivileged user can attach up to 80 bytes of InInstruction/OP_RETURN data to a Serai Bitcoin transaction (`data: Option<Vec<u8>>` is a public input to `SignableTransaction::new`, reachable via `prepare_send`/payment `data`). With data present, the signed transaction pays less than the intended fee rate; near the minimum relay boundary the transaction is rejected by the network while the multisig has consumed preprocesses/commitments for it, and the affected UTXOs are tied to a TXID that won't confirm until a corrected transaction is signed. Additionally, change is silently inflated at the expense of the fee — the same "leftover goes to the wrong place" misrouting as the reference report (change instead of fee here; Market instead of reallocator there).

### Likelihood Explanation
Any transaction carrying `data` (the Serai transfer instruction path routinely uses OP_RETURN) miscomputes its fee. The mempool-rejection case requires a low `fee_per_vbyte`, but the fee underpayment relative to the requested rate occurs on every transaction with data, so likelihood of degraded confirmation is high; full relay failure is conditional on fee settings.

### Recommendation
Include the OP_RETURN output in the weight/vbytes template. Pass the fully-built `tx_outs` (or the data length) into `calculate_weight_vbytes` — e.g., compute the weight over `payments + data_output [+ change]` — so `needed_fee`, the minimum-fee check, and the change deduction all reflect the transaction actually signed.

### Proof of Concept
1. Build inputs totaling `payment_sat + needed_fee + DUST` exactly.
2. Call `SignableTransaction::new(inputs, &payments, Some(change), Some(vec![0u8; 80]), fee_per_vbyte)` where `fee_per_vbyte` equals the minimum relay rate.
3. The returned `tx` contains payments + OP_RETURN + change, but `needed_fee` was computed as if the OP_RETURN were absent.
4. `tx.fee() / tx.vsize()` is strictly less than `fee_per_vbyte`; for an 80-byte payload (~90 vbytes including output overhead) the effective rate drops ~`fee_per_vbyte * 90/vsize` below the intended rate, and a tx constructed at the minimum relay rate falls under `DEFAULT_MIN_RELAY_TX_FEE` for its true size and is rejected by `testmempoolaccept`.