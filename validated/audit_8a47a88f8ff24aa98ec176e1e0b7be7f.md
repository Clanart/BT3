### Title
`SignableTransaction::new` omits the OP_RETURN output from the weight/vbyte computation, under-debiting the fee and under-checking standardness - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the report's "the ledger credits/debits based on a partially-computed quantity, so the real balance invariant is violated", `SignableTransaction::new` computes the transaction fee and the standardness weight bound from a transaction skeleton that only contains the payment outputs, while the final signed transaction can additionally contain an OP_RETURN output of up to 83 bytes. The amount of fee actually committed (`sum(inputs) - sum(outputs)`) is therefore smaller than `fee_per_vbyte` over the real virtual size, i.e., the protocol under-debits the fee exactly as the Pendle system under-debited payments through partial computations.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` pushes the caller-supplied OP_RETURN `data` output into `tx_outs` at lines 194-202. However, both calls to `Self::calculate_weight_vbytes` (line 204 for the no-change case, lines 225-226 for the change case) are passed `payments` — the caller's `&[(ScriptBuf, u64)]` payment list — and never the OP_RETURN output:

```rust
// networks/bitcoin/src/wallet/send.rs:194-206
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) });
}
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` (lines 62-127) builds a mock `Transaction` whose `output` list is derived solely from `payments` (plus optionally `change`), so `weight`/`vbytes` exclude the ~90-byte OP_RETURN output (8-byte value + script length + up to 83-byte script). Three dependent quantities are consequently computed on a smaller transaction than the one actually signed:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206, and `fee_with_change` at line 227) — the reserved fee is `fee_per_vbyte` times a vbytes value missing the data output, so the real fee rate `fee() / actual_vbytes` is strictly below the caller-requested rate.
2. The minimum-relay-fee guard `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` (line 211) is checked against the same undercounted `vbytes`, so a transaction can pass the check while its true fee rate is below the 1000 sat/kvB relay minimum.
3. The standardness bound `weight > MAX_STANDARD_TX_WEIGHT` (line 241) is evaluated on `weight` excluding the data output, so a maximally-sized transaction plus OP_RETURN can exceed the 400,000 WU standardness limit and be unbroadcastable.

This mirrors the reported bug structurally: the accounting quantity (fee reserved / funds debited) is derived from a partial enumeration of the real obligation (the full output set), so the invariant "reserved fee = fee rate × real size" breaks by the size of the omitted output — the same "sum of parts ≠ whole" divergence, here deterministic rather than wei-scale.

### Impact Explanation
- The produced transaction pays a lower fee rate than the caller (the scheduler/processor, which selected `fee_per_vbyte` from observed block fees) intended, so confirmation is slower than planned or fails entirely when the true rate falls under the mempool minimum the line-211 check was meant to enforce.
- In the extreme case (inputs/payments sized near `MAX_STANDARD_TX_WEIGHT` plus a `data` output), the signed transaction is non-standard and cannot be relayed at all: the inputs it spends are locked behind a plan whose only signed transaction is unbroadcastable, making funds unspendable through that plan until a replacement is negotiated.

### Likelihood Explanation
The bug is deterministic whenever `SignableTransaction::new` is invoked with a non-`None` `data` argument — an external input to the API bounded only by the 80-byte `TooMuchData` check at line 171. In the in-repo processor path (`processor/src/networks/bitcoin.rs:446-452`) `data` is currently always `None`, which limits in-protocol reachability; however, the wallet crate is a public API and the `data`/`TooMuchData`/`OP_RETURN` path exists precisely to be exercised by callers, so the flaw is a genuine reachable defect in in-scope production code rather than dead code. Given the medium severity of the source class and the honest dependency on a caller actually using `data`, this rates Medium/Low-Medium.

### Recommendation
Include the data output (and any other outputs unconditionally appended to `tx_outs`) when computing weight and vbytes. Concretely, have `calculate_weight_vbytes` take the final `tx_outs` list (or an explicit `data: Option<&ScriptBuf>` parameter) instead of only `payments`, so `needed_fee`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check are all evaluated on the transaction that will actually be signed. Alternatively, compute `vbytes` once from the fully-constructed `tx` just before returning.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs semantics:
// OP_RETURN of 80-byte payload => output ≈ 8 (value) + 1 (len) + ~82 (script) ≈ 91 bytes
// => ~91 * 4 = 364 weight units ≈ 91 vbytes omitted from the fee computation.

let data = vec![0x42; 80];
let tx = SignableTransaction::new(
    inputs,          // ReceivedOutputs
    payments,        // &[(script_pubkey, amount)]
    Some(change_script),
    Some(data),      // OP_RETURN output added to tx_outs but NOT to weight calc
    fee_per_vbyte,   // e.g. 10
).unwrap();

// tx.needed_fee() == 10 * vbytes(payments-only tx)
// actual fee rate == tx.fee() / real_vbytes(tx including OP_RETURN)
//                 < fee_per_vbyte by ~910 satoshis worth of vbytes
// i.e., the reserved fee is ~10 * 91 = 910 sats short of the intended rate.
```
Concretely: with `fee_per_vbyte = 5`, 1 input and 1 payment (~57 + ~43 vbytes), the real transaction carrying an 80-byte OP_RETURN is ~191 vbytes, but `needed_fee` is computed on ~100 vbytes, reserving 500 sats where ~955 are needed — a ~47% under-debit of the fee, possibly below `DEFAULT_MIN_RELAY_TX_FEE` on the true size while still passing the line-211 check.