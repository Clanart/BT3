### Title
SignableTransaction computes weight and fee before appending the OP_RETURN output - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes the `data` OP_RETURN output onto `tx_outs` and then calls `calculate_weight_vbytes` with `payments` only. The returned `weight`/`vbytes` — used for the minimum-fee check, the `needed_fee` stored and reported to callers, and the `MAX_STANDARD_TX_WEIGHT` standardness check — therefore exclude an output that is actually present in the signed transaction. This is the same bug class as the reference finding: a value (`navPerShare` → here, weight/vbytes/fee) is computed from pre-mutation state, the state is then mutated (share mint → appended output), and the stale value is used for the accounting invariant.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

1. `tx_outs` is built from `payments`, then the OP_RETURN output is appended (lines 194–202).
2. Weight/vbytes are computed from `tx_ins.len()` and `payments` — not `tx_outs` (line 204):
   ```rust
   let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
   ```
   `calculate_weight_vbytes` reconstructs the transaction from `payments`, so the OP_RETURN output (up to 80 bytes of data + ~9 bytes of output overhead, ~30+ vbytes) is missing from the measurement.
3. `needed_fee = fee_per_vbyte * vbytes` uses the understated vbytes (line 206).
4. The `TooLowFee` check against `DEFAULT_MIN_RELAY_TX_FEE` uses the understated `vbytes` (line 211), so a transaction whose *actual* fee rate is below the relay minimum can pass.
5. The change-output calculation at lines 225–226 also omits the OP_RETURN output; both `weight_with_change`/`vbytes_with_change` and the change amount `input_sat - payment_sat - fee_with_change` are computed without it.
6. The `MAX_STANDARD_TX_WEIGHT` check (line 241) uses the stale `weight`, so a transaction that is actually non-standard can be produced.
7. `needed_fee` (the stale, under-computed fee) is stored on the `SignableTransaction` and returned by `needed_fee()`, which the processor amortizes over payments as if it were the true fee.

The actual fee paid is `sum(inputs) - sum(outputs)` (see `fee()`, lines 138–141), which is correct only by coincidence of the change logic — but the *effective* fee rate is lower than `fee_per_vbyte` for every transaction carrying `data`, and all size-derived checks are evaluated against a smaller transaction than the one signed.

### Impact Explanation
- Transactions carrying an OP_RETURN payload pay a lower sat/vbyte than specified; with `data` near 80 bytes the underpayment is roughly `fee_per_vbyte * ~30+` vbytes. At low fee rates this can leave the transaction below the node's minimum relay fee, so a signed transaction is never relayed/confirmed — funds in its inputs are effectively stuck until a replacement is constructed.
- The `TooLowFee` guard can be bypassed: a transaction passing the check may still fall below `DEFAULT_MIN_RELAY_TX_FEE` on its real vsize.
- The standardness bound can be exceeded: `weight` excludes the OP_RETURN output, so `SignableTransaction` can be built for a transaction exceeding `MAX_STANDARD_TX_WEIGHT`, which will be rejected as non-standard on broadcast.
- The processor's `prepare_send` amortizes `needed_fee()` across payments assuming it equals the true fee; the discrepancy makes fee accounting inconsistent with what is actually signed.

### Likelihood Explanation
Reachable whenever a plan includes `data` — i.e., an untrusted burn/payment payload flows into `SignableTransaction::new`. No validator misbehavior or collusion is required; it is a deterministic arithmetic omission on public inputs. Severity: Medium — incorrect fee/size accounting and potentially unbroadcastable transactions, without direct key or signature compromise.

### Recommendation
Compute weight/vbytes over the *final* set of outputs, not `payments`. Concretely, either:
- Pass `data`/the OP_RETURN script into `calculate_weight_vbytes` (e.g., have it accept `tx_outs` or append the OP_RETURN `TxOut` inside the function), and
- Re-derive `weight`/`vbytes` after the change-output decision so both the no-change and with-change paths measure the transaction actually being signed.

Also re-check `MAX_STANDARD_TX_WEIGHT` against the post-change weight (currently `weight = weight_with_change` is assigned, which is correct — but only once the OP_RETURN output is included in both measurements).

### Proof of Concept
1. Call `SignableTransaction::new` with one input of `100_000` sats, `payments = [(script, 50_000)]`, `change = None`, `data = Some(vec![0; 80])`, `fee_per_vbyte = 1`.
2. `tx_outs` contains the payment plus the OP_RETURN output; `calculate_weight_vbytes` is invoked with only `payments`.
3. Let `vbytes_measured` be the returned vbytes (missing the ~34-vbyte OP_RETURN output). `needed_fee = vbytes_measured`.
4. The signed transaction's real vsize is `vbytes_measured + ~34`; its effective fee rate is `needed_fee / (vbytes_measured + 34) < 1` sat/vbyte — below the requested rate and potentially below `DEFAULT_MIN_RELAY_TX_FEE`, even though `TooLowFee` was checked and passed.
5. `st.fee() > st.needed_fee()` only when change is involved; with no change, `needed_fee` equals the paid fee while the realized rate is still lower than `fee_per_vbyte`, demonstrating the stale pre-mutation measurement.

Relevant code: `networks/bitcoin/src/wallet/send.rs` lines 194–204 (OP_RETURN appended before measurement), 62–99 (`calculate_weight_vbytes` builds from `payments` only), 206–213 (fee and TooLowFee check on understated vbytes), 241–243 (weight check).