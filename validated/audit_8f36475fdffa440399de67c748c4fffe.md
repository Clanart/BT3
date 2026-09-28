### Title
`SignableTransaction::new` excludes the `OP_RETURN` data output from weight/vsize accounting, underpaying the fee and over-crediting change - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The analog to "payout math computed on an offset balance leaves unaccounted value" is a fee/change accounting discrepancy in `SignableTransaction::new`. The transaction's `OP_RETURN` output (up to 80 bytes of caller-supplied data) is pushed onto `tx_outs` *before* the weight/vsize calculation, but `calculate_weight_vbytes` is invoked with `payments` only — it never sees the data output. The real transaction is therefore heavier than the measured one, so `needed_fee` underestimates the true required fee, the `TooLowFee` and `TooLargeTransaction` checks run against the wrong size, and the change output is credited too many satoshis (the fee ends up lower than specified).

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` builds `tx_outs` and appends the `OP_RETURN` output at lines 194–202:

```rust
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })
}
```

Only afterwards is the weight computed — from `payments`, not `tx_outs` — at line 204:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` (lines 62–127) reconstructs a transaction whose `output` list is built solely from `payments` plus the optional change script. It has no parameter for the data output, so the `OP_RETURN` output (≈9 + 1 + up to 80 bytes of serialized output, ~90+ weight units) is omitted from both `weight` and `vbytes`. The same omission occurs for the change-aware recomputation at lines 225–227.

Consequences:

- `needed_fee = fee_per_vbyte * vbytes` (line 206) is short by `fee_per_vbyte * (vbytes_real - vbytes)`. `fee()` (lines 138–141) and the test invariant `input_value - output_value == needed_fee` no longer hold relative to the requested fee rate: when change is created, `change = input_sat - payment_sat - fee_with_change` (line 228) overpays the change output by exactly the missing fee, and the on-chain fee is lower than `needed_fee`.
- The minimum-relay-fee gate (line 211) compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the underestimated `vbytes`. A transaction constructed at the minimum fee rate boundary that also carries `data` will be produced successfully yet have a real fee rate below the relay minimum, so nodes will not relay/mine it.
- The `MAX_STANDARD_TX_WEIGHT` check (line 241) uses the underestimated `weight`, so a transaction at the weight boundary plus an `OP_RETURN` can be produced despite exceeding the standardness limit — again yielding an unbroadcastable transaction.

### Impact Explanation
A caller who supplies `data` (transaction data they cause to be signed — a public input to this API) and a `change` script obtains a signed transaction whose realized fee is less than `needed_fee()` reports, with the difference silently redirected into the change output. At boundary fee rates or weights the resulting transaction is non-standard and cannot be broadcast: the inputs are committed to a transaction no node will accept, while the caller believes the spend succeeded per the returned `needed_fee`. As in the PA1D report, the accounting base used for distribution (the measured vsize) does not match reality (the actual vsize including the `OP_RETURN` output), so value is misallocated on every data-carrying transaction.

### Likelihood Explanation
Deterministic whenever `data: Some(_)` is passed to `SignableTransaction::new`: the miscount is unconditional and scales with the data length and `fee_per_vbyte`. The unrelayable-transaction outcome additionally requires operating near the minimum relay fee or the max standard weight boundary. `processor/src/networks/bitcoin.rs` currently always passes `None` for data (line 450), so the exposed path is direct consumers of the public `SignableTransaction::new` API who attach `OP_RETURN` data to payments.

### Recommendation
Include the data output in the size estimate: either pass the fully-built `tx_outs` (payments + `OP_RETURN`) into `calculate_weight_vbytes` and have it consume `&[TxOut]` instead of `&[(ScriptBuf, u64)]`, or push a placeholder `TxOut` for the OP_RETURN inside `calculate_weight_vbytes` when `data.is_some()`. Recompute `weight`/`vbytes`/`needed_fee` on the true output set before performing the `TooLowFee`, `NotEnoughFunds`, change, and `TooLargeTransaction` checks, so the measured size always equals the signed transaction's size.

### Proof of Concept
```rust
// One input, one payment, a change script, and 80 bytes of OP_RETURN data.
let inputs = vec![received_output]; // value = 100_000 sats
let payments = vec![(payment_script, 50_000)];
let data = Some(vec![0u8; 80]);

let tx = SignableTransaction::new(inputs, &payments, Some(change_script), data, 1).unwrap();

// needed_fee was computed from vbytes WITHOUT the ~23+ vbyte OP_RETURN output.
// The returned Transaction contains the OP_RETURN, so its real vsize is larger.
assert!(tx.needed_fee() < real_vsize(&tx.tx) * 1);

// fee() == input - outputs == needed_fee only because change absorbed the
// shortfall; the effective fee rate is below the requested 1 sat/vB.
let real_rate = tx.fee() as f64 / real_vsize(&tx.tx) as f64;
assert!(real_rate < 1.0); // under-pays; below relay minimum if fee_per_vbyte was minimal
```

Concretely: `input_sat = 100_000`, `payment_sat = 50_000`, `fee_per_vbyte = 1`. The estimator misses ~23 vB of `OP_RETURN` output, so `fee_with_change` is ~23 sats short. The change output receives ~23 sats too much and the actual fee rate is `(needed_fee) / (vsize + ~23)`, which falls under `DEFAULT_MIN_RELAY_TX_FEE` when `fee_per_vbyte` was chosen at the relay floor — producing a validly signed transaction the network will not relay.