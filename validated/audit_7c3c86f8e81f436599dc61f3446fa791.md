### Title
Fee underestimation when a data (OP_RETURN) output is present produces signed transactions that cannot be broadcast - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` computes the transaction weight/vbytes — and hence `needed_fee` — from only the inputs and the payment outputs. The OP_RETURN data output appended at `send.rs:194-202` is never included in the weight calculation (`send.rs:204` calls `calculate_weight_vbytes(tx_ins.len(), payments, None)`). Any transaction carrying a `data` payload will therefore pay a fee lower than the intended rate — and, when `fee_per_vbyte` is at/near the minimum relay fee, lower than `DEFAULT_MIN_RELAY_TX_FEE` for the transaction's *actual* vsize — so the resulting fully-signed transaction will be rejected by Bitcoin nodes.

### Finding Description
The analog to "the refund transfer fails because of a property of the destination" is a protocol-produced outgoing transfer that is invalid due to data the destination/instruction carries. In Serai, external `OutInstruction`s can carry arbitrary user-supplied data, which `bitcoin-serai` embeds as an OP_RETURN output (`send.rs:194-202`, exercised by `test_data` in `networks/bitcoin/tests/wallet.rs`).

The fee math, however, is computed via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at `send.rs:204`, which builds a mock `Transaction` containing only inputs and payment outputs (`send.rs:68-99`). The data output (up to ~90 vbytes for the max 80-byte payload plus script overhead, per the `TooMuchData` bound at `send.rs:171-173`) is omitted. `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`) therefore underprices every data-carrying transaction by roughly `fee_per_vbyte * (34 + data_len)` satoshis.

Concretely, `SignableTransaction::fee()` (`send.rs:138-141`) defines the actual fee as `sum(inputs) - sum(outputs)`, which equals `needed_fee`, while the real vsize is `tx.vsize()` including the OP_RETURN output. The minimum-fee guard at `send.rs:211` checks `needed_fee >= DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the *understated* `vbytes`, so a transaction constructed at the minimum fee rate passes validation yet is unbroadcastable once signed.

### Impact Explanation
A signed transaction whose real fee rate falls below the mempool minimum will be rejected by `sendrawtransaction`/`testmempoolaccept` by Bitcoin Core nodes. For a threshold-signed transaction this is worse than a local wallet failure: the FROST signing round completes, shares are consumed, and the resulting `Transaction` (returned by `TransactionSignatureMachine::complete`, `send.rs:413-428`) is dead on arrival. If the construction is retried with the same inputs and fee rate (e.g., an automated scheduler repeatedly building the same plan), each attempt fails identically — a liveness failure for any payment batch that includes a data output, analogous to the Dinari report where a single order's refund path bricks `cancelOrder`. Funds are not stolen, but escrowed inputs cannot be moved by that transaction, and every re-sign consumes a fresh threshold-signing session.

### Likelihood Explanation
The bug triggers deterministically whenever `data.is_some()` and `fee_per_vbyte * actual_vsize` lands below the relay minimum — i.e., whenever the configured fee rate is at or near 1 sat/vbyte (common on low-fee regimes) or when the operator-set `fee_per_vbyte` doesn't leave headroom for the omitted ~40-120 vbytes. The data payload is attacker/unprivileged-user controllable in the processor flow (arbitrary instruction data), so a user can force inclusion of a near-maximum 80-byte payload to maximize the shortfall. Reachability requires no validator misbehavior; only a `data`-carrying `SignableTransaction` built at a marginal fee rate.

### Recommendation
Include the data output in the mock transaction used by `calculate_weight_vbytes` — e.g., change its signature to accept the full `tx_outs` vector (including the OP_RETURN output) or pass `data`'s serialized length so the output's real size is counted. Recompute `vbytes` after the OP_RETURN is known, before deriving `needed_fee` and running the `TooLowFee`/`NotEnoughFunds` checks at `send.rs:204-221`. Optionally assert post-construction that `self.fee() >= DEFAULT_MIN_RELAY_TX_FEE * self.tx.vsize() / 1000`.

### Proof of Concept
1. Construct `SignableTransaction::new(inputs, &payments, None, Some(vec![0u8; 80]), 1)` where `inputs` cover `payments + needed_fee` exactly.
2. The constructed `tx.tx` contains `payments + 1` outputs, but `needed_fee` was computed for a mock transaction without the OP_RETURN output.
3. `tx.fee() == needed_fee == 1 * vbytes_without_opreturn`, while `tx.tx.vsize()` is ~90 vbytes larger. The effective fee rate is `< 1 sat/vbyte`, below `DEFAULT_MIN_RELAY_TX_FEE`, so Bitcoin Core rejects the signed transaction with `min relay fee not met` / `insufficient fee`.
4. Compare with `networks/bitcoin/tests/wallet.rs:test_send`, which asserts `needed_fee == tx.vsize() * FEE` — that assertion would fail for any test exercising `data` with an exact-fee input set (the existing `test_data` never checks the fee, masking the bug).