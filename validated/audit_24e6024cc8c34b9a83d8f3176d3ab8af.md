### Title
Transaction weight/fee accounting ignores the OP_RETURN `data` output, causing under-charged fees (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` builds the transaction's output list including an attacker-influenced OP_RETURN `data` output, but `calculate_weight_vbytes` is only ever called with `payments` and the optional change output — never with the data output. The measured "size" of the transaction (the analog of the report's gas measurement) therefore does not reflect the actual transaction that will be broadcast and signed, so the `needed_fee` deducted from the change output is computed against an under-measured vbyte count. Like the referenced bug where one `preGas` snapshot was reused for two distinct accounting points, here a single incomplete measurement stands in for the true cost of the final transaction.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is appended to `tx_outs` before the fee is computed:

- `networks/bitcoin/src/wallet/send.rs:194-202` pushes the `TxOut` containing `ScriptBuf::new_op_return(data)` into `tx_outs`.
- `networks/bitcoin/src/wallet/send.rs:204` then calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — which builds the weight-measurement transaction purely from `payments` and the optional `change` (`send.rs:62-99`). There is no parameter for the OP_RETURN output, so its `8 + script_pubkey` bytes of weight are never counted.
- `networks/bitcoin/src/wallet/send.rs:206-213` derives `needed_fee = fee_per_vbyte * vbytes` and enforces the minimum relay fee against this understated `vbytes`.
- `networks/bitcoin/src/wallet/send.rs:224-234` computes the change output value as `input_sat - (payment_sat + fee_with_change)`, where `fee_with_change = fee_per_vbyte * vbytes_with_change` is again computed without the data output.

The resulting transaction pays `needed_fee` for a smaller transaction than the one actually serialized and broadcast by `TransactionSignMachine`/`TransactionSignatureMachine` (`send.rs:355-428`). The effective sat/vbyte rate of the final transaction is strictly lower than the caller-requested `fee_per_vbyte`, and — critically — can fall below `DEFAULT_MIN_RELAY_TX_FEE` even though the `TooLowFee` check passed, since that check also used the understated `vbytes`.

### Impact Explanation
The worst case is a transaction whose real fee rate is below the Bitcoin network's minimum relay fee: it will be rejected by relay policy / never confirm, so funds that were "sent" are effectively unspendable through this path (the signable transaction is the only construction the wallet offers). With a max-size (80-byte) data payload and a fee rate near the minimum, the ~90+ unaccounted bytes translate into ~90+ unaccounted weight-units per-byte-of-output, enough to push the realized fee rate below relay minimum. Even in milder cases, the wallet systematically pays a lower fee rate than configured for every transaction carrying a data output.

### Likelihood Explanation
The `data` parameter is populated from user/instruction-supplied payloads (the processor embeds `InInstruction`/`Shorthand` data into OP_RETURN outputs), so the condition is reachable from untrusted/public input: any deposit or instruction that results in an OP_RETURN-carrying send produces an under-measured fee. It requires no malicious validator or collusion — just a data-bearing transaction at a fee rate close to the minimum.

### Recommendation
Include the OP_RETURN output in the weight measurement: pass the full intended output set (payments + data output + optional change) to `calculate_weight_vbytes`, or add a `data_len`/`script_pubkey` parameter so `send.rs:204` and `send.rs:225-226` measure the same transaction that will actually be broadcast. Also enforce the `TooLowFee` check against the final, fully-populated vbyte count.

### Proof of Concept
```rust
// Conceptual, against SignableTransaction::new:
let inputs = vec![received_output /* value = payment + needed_fee + DUST */];
let payments = vec![(payment_script, DUST)];
let data = Some(vec![0u8; 80]); // max-allowed OP_RETURN payload

let tx = SignableTransaction::new(
    inputs, &payments, Some(change_script.clone()), data, /* fee_per_vbyte */ 1,
).unwrap();

// tx.needed_fee() was computed from vbytes that exclude the ~90-byte OP_RETURN output,
// so tx.fee() / actual_vbytes < 1 sat/vbyte and potentially < DEFAULT_MIN_RELAY_TX_FEE,
// even though the TooLowFee check passed.
```

Key lines: `networks/bitcoin/src/wallet/send.rs:194-213` (data output added, then weight measured without it), `send.rs:62-99` (`calculate_weight_vbytes` has no data-output input), `send.rs:224-234` (change reduced only by the under-measured fee).