### Title
OP_RETURN output omitted from fee estimation causes underpaid fees and misattributed change - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` builds `tx_outs` including an OP_RETURN `data` output, but computes the transaction weight/vbytes — and therefore `needed_fee` — from `payments` alone. Any leftover value is then dumped into the change output (or burned as fee), so value that should have funded the fee is misattributed to the change output, exactly mirroring the "excess value is silently misallocated/lost" bug class: the accounting uses the wrong quantity, and the residual is absorbed by the wrong bucket.

### Finding Description
The data output is pushed into `tx_outs` at lines 193–202. The subsequent weight calculations at line 204 (`Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`) and line 226 (`... Some(&change)`) pass `payments` — the pre-data payment list — rather than `tx_outs`. Inside `calculate_weight_vbytes` (lines 85–99), the mock `Transaction` is built solely from `payments` plus the optional change output, so an OP_RETURN output of up to 83+ bytes is never counted. `needed_fee = fee_per_vbyte * vbytes` (lines 206, 227) is therefore understated, and the change value `input_sat - payment_sat - fee_with_change` (line 228) is inflated by the same amount.

Consequences:
- The actual paid fee equals the understated `needed_fee`, so the effective fee rate is below the caller's `fee_per_vbyte`. For a maximal 80-byte payload this underpays by roughly `80–84 vbytes * fee_per_vbyte`.
- The `TooLowFee` check at line 211 compares the understated fee against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using understated `vbytes`, so a transaction can pass the check yet be un-relayable once broadcast — funds locked until the tx is regenerated.
- `needed_fee` is exposed via `needed_fee()` (line 133) for external fee amortization, so callers budget and deduct fees from payments using a number that does not reflect the real transaction.

### Impact Explanation
Caller-supplied `data` silently shifts value: the change output receives sats that should have been fee (or, without change, the reported `needed_fee` no longer bounds `fee()`). The transaction underpays its intended fee rate, can fall below relay minimums despite passing the `TooLowFee` check, and any downstream accounting relying on `needed_fee()` is wrong. This is an incorrect fee/accounting formula reachable purely through public transaction-construction inputs (`payments`, `data`, `change`), with no privileged position required.

### Likelihood Explanation
Triggered whenever `SignableTransaction::new` is called with `Some(data)` and a change output or fee-sensitive downstream accounting. An unprivileged user supplying transaction data (e.g., embedding data in a withdrawal/swap transaction) hits the miscalculation deterministically.

### Recommendation
Pass the fully-constructed output list into `calculate_weight_vbytes` — i.e., build the mock transaction from `tx_outs` (including the OP_RETURN output) rather than `payments`, in both the no-change (line 204) and with-change (line 226) calls — so `needed_fee` covers the actual serialized size.

### Proof of Concept
Call `SignableTransaction::new` with one input of `input_sat` sats, `payments = [(script, DUST)]`, `change = Some(change_script)`, `data = Some(vec![0u8; 80])`, and any `fee_per_vbyte`. The resulting `SignableTransaction::fee()` equals `needed_fee` computed without the ~83-vbyte OP_RETURN output, so `fee() / actual_vbytes < fee_per_vbyte`, and the change output is `~83 * fee_per_vbyte` sats larger than it should be — value allocated to "change" that the caller intended as fee (or vice versa, misaccounted either way).