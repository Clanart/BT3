### Title
OP_RETURN `data` output excluded from transaction weight/fee/size accounting in `SignableTransaction::new`, producing transactions whose actual fee rate and weight diverge from the accounted values - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends an OP_RETURN output to the transaction when `data` is provided, but computes the transaction weight, virtual size, required fee, minimum-relay-fee check, and the maximum-standard-weight check using only `payments` and `change`. The resulting `SignableTransaction` accounts for a smaller transaction than the one it actually builds and signs — the same bug class as the reference finding, where actual outflows/components exist that the accounting logic never subtracts.

### Finding Description
`SignableTransaction::new` constructs the real output set in `tx_outs`, pushing an OP_RETURN output carrying up to 80 bytes of attacker/integrator-supplied data (`networks/bitcoin/src/wallet/send.rs` L193–202). However, the weight and vbyte calculation is performed by `calculate_weight_vbytes(tx_ins.len(), payments, None)` (L204) — which builds a synthetic `Transaction` containing only `payments` and the optional `change` output (L85–99). The OP_RETURN output is never included:

- `needed_fee = fee_per_vbyte * vbytes` (L206) is computed on a vsize missing the data output (~11 + len(data) bytes of output, plus amount/script overhead).
- The `TooLowFee` check (L211) compares against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the underestimated `vbytes`.
- The change calculation at L224–235 repeats the omission: `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` still omits the data output, so `fee_with_change` and the change amount `input_sat - payment_sat - fee_with_change` are computed against the wrong size.
- The `TooLargeTransaction` guard (L241) checks `weight` — which never includes the OP_RETURN output — against `MAX_STANDARD_TX_WEIGHT`.

Since `tx_outs` (with the data output) is what gets stored in `self.tx` and ultimately signed via `TransactionSignMachine::sign` (L373–397) and broadcast after `complete` (L413–428), the transaction the multisig signs is strictly larger than what all accounting in `new` assumed. `needed_fee()` (L133–135) and `fee()` (L138–141) therefore report a fee satisfying the requested rate only for the fictional smaller transaction; the actual fee rate is `needed_fee / actual_vbytes < fee_per_vbyte`.

### Impact Explanation
- **Below-minimum-relay transaction**: when `fee_per_vbyte` is at or near the relay minimum (~1 sat/vB), the uncounted ~90 bytes can push the actual fee rate below `DEFAULT_MIN_RELAY_TX_FEE`. The checks pass, FROST signing completes, and the resulting transaction is rejected by relay — the spend cannot confirm. For a threshold-controlled wallet, the plan is bricked: the signatures were produced for an unbroadcastable transaction and the inputs cannot be spent by that plan.
- **Standardness-weight bypass**: near the size limit, a transaction that passes `TooLargeTransaction` can exceed `MAX_STANDARD_TX_WEIGHT` once the data output is counted, again yielding an unrelayable transaction after threshold signing.
- **Fee accounting mismatch**: `needed_fee()` (consumed by upstream fee estimation via `Network::needed_fee` in `processor/src/networks/bitcoin.rs` L803–816 and amortized across payments in `processor/src/networks/mod.rs` `amortize_fee`) understates the fee required for the true transaction size, so callers that reserve/schedule funds based on it operate on an accounting that does not match the transaction that will actually be signed.

### Likelihood Explanation
The flaw is deterministic whenever `data` is `Some` — it is not probabilistic and requires no attacker coordination. It only degrades into a failed broadcast under edge conditions (fee rate near the relay floor, or transaction near the standardness weight cap). Note that the in-repo processor currently passes `None` for `data` (`processor/src/networks/bitcoin.rs` L450), so the path is not exercised by Serai's own flow today; the bug lives in the public wallet API where any caller supplying `data` triggers it unconditionally, making the defect certain but the harmful outcome conditional.

### Recommendation
Include the OP_RETURN output in the synthetic transaction used for size accounting. The cleanest fix is to pass the full output set (or the data output separately) into `calculate_weight_vbytes`, e.g. add a `data: Option<&[u8]>` parameter that appends the same `TxOut` (value zero, `ScriptBuf::new_op_return(...)`) to the synthetic `tx.output` before calling `tx.weight()`, and invoke it consistently in both the no-change and with-change branches. This makes `needed_fee`, the `TooLowFee` check, the change amount, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the transaction that is actually signed.

### Proof of Concept
The divergence can be shown with a unit test — no blockchain needed:

```rust
// networks/bitcoin/tests/wallet.rs (or a unit test in send.rs)
let inputs = vec![received_output];           // any funded ReceivedOutput
let payments = vec![(p2tr_script_buf(key).unwrap(), 10_000)];
let data = Some(vec![0u8; 80]);                // max allowed OP_RETURN data

let tx = SignableTransaction::new(inputs, &payments, None, data, fee_rate).unwrap();

// The accounted fee was computed without the data output
let accounted_vbytes = tx.needed_fee() / fee_rate;
let actual_vbytes = tx.transaction().vsize() as u64;
assert!(actual_vbytes > accounted_vbytes);     // OP_RETURN output unaccounted

// Effective fee rate is below the requested rate
let actual_rate = tx.fee() as f64 / actual_vbytes as f64;
assert!(actual_rate < fee_rate as f64);
```

With `fee_rate` chosen so `tx.fee() < DEFAULT_MIN_RELAY_TX_FEE * actual_vbytes / 1000` (satisfiable at `fee_rate = 1` with a large enough data output), the transaction passes `TooLowFee` yet pays below the relay minimum and cannot be broadcast after signing.