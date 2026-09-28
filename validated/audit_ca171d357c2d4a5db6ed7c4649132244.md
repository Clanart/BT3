### Title
`SignableTransaction::new` omits the OP_RETURN data output when estimating weight/vbytes, underestimating the required fee - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` pushes a caller-supplied `OP_RETURN` output into `tx_outs`, but computes `vbytes`/`needed_fee` via `calculate_weight_vbytes`, which rebuilds the transaction using only `payments` (and optionally `change`) — never including the data output. Like the Elfi finding where available margin ignored accrued fees, the "cost" side of the balance check is understated: `NotEnoughFunds` and `TooLowFee` are evaluated against a fee calculated for a smaller transaction than the one actually produced.

### Finding Description
- The OP_RETURN output is appended to `tx_outs` before any sizing is done (lines 194–202).
- `calculate_weight_vbytes` builds a throwaway `Transaction` whose `output` list is only `payments` plus optional change (lines 67–99). The `data` output is not passed and cannot be included.
- `needed_fee = fee_per_vbyte * vbytes` (line 206), the `TooLowFee` minimum-relay check (line 211), the `NotEnoughFunds` check (line 215), and the change-amount computation `input_sat - (payment_sat + fee_with_change)` (lines 226–234) all use this underestimated vsize.
- Result: the produced transaction is larger than estimated by the serialized size of the OP_RETURN output (up to ~90 bytes for the allowed 80 bytes of data). The actual fee paid (`sum(inputs) - sum(outputs)`, `fee()` at line 138) equals the underestimated `needed_fee` (minus nothing — change absorbs the difference), so the effective fee rate is strictly below `fee_per_vbyte`, and can fall below `DEFAULT_MIN_RELAY_TX_FEE` even though the `TooLowFee` check passed.

### Impact Explanation
An unprivileged caller supplies the `data` bytes to `SignableTransaction::new`. The resulting signed transaction pays a fee rate lower than requested and potentially below the network's minimum relay fee, making it non-propagating/non-confirmable. Since inputs use `Sequence::MAX` (no RBF signaling) and the UTXOs are consumed by the signed tx, the funds are effectively stuck in a transaction the mempool rejects — an availability/loss-of-funds condition on spendable outputs, not merely an overpayment.

### Likelihood Explanation
Deterministic whenever `data` is provided and the chosen `fee_per_vbyte` is at or near the minimum relay bound, or whenever the omitted output's weight is a meaningful fraction of tx size (small payment-only transactions). Purely public inputs: `payments`, `change`, `data`, `fee_per_vbyte`.

### Recommendation
Include the OP_RETURN output in the transaction built inside `calculate_weight_vbytes` (pass `data` through, mirroring how `change` is handled at line 98), so `vbytes`, `needed_fee`, the `TooLowFee` check, and the change computation all reflect the true transaction size. Also re-check `TooLowFee` against the final with-change vsize if the change branch is taken.

### Proof of Concept
Conceptual: call `SignableTransaction::new(inputs, payments, None, Some(vec![0u8; 80]), fee_per_vbyte)` where `fee_per_vbyte * vbytes` equals exactly the minimum relay fee for the estimated vsize. The returned tx contains a ~90-byte larger output set than estimated; `fee()` equals `needed_fee` but `tx.vsize() > vbytes`, so the real sat/vbyte rate is below the minimum relay fee and the `TooLowFee` guard at line 211 was bypassed. Relevant code: `tx_outs.push(OP_RETURN)` at send.rs:194–202; size estimate ignoring it at send.rs:204–206; fee checks at send.rs:211–221; change path repeating the omission at send.rs:226–234.