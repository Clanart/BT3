### Title
`SignableTransaction` excludes the OP_RETURN data output from fee and weight calculations, producing under-priced or consensus-unbroadcastable transactions - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The bug class in the external report is *unintended inclusion*: attacker-controlled content is pulled into a processing path that never accounts for it. In `bitcoin-serai`, an unprivileged user requesting a withdrawal can attach up to 80 bytes of `data`, which is included in the signed transaction as an `OP_RETURN` output — yet `SignableTransaction::new` computes the virtual size, required fee, and the `MAX_STANDARD_TX_WEIGHT` check from a template transaction that omits that output entirely. The included bytes are never accounted for, mirroring the report's "content included but not authorized/accounted" shape.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`), the `data` argument is appended to `tx_outs` as an `OP_RETURN` output at lines 194–202:

```rust
// Add the OP_RETURN output
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(
      PushBytesBuf::try_from(data)
        .expect("data didn't fit into PushBytes depsite being checked"),
    ),
  })
}
```

The fee/weight estimation, however, is performed by `calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204) and `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (line 225–226). Both call sites only ever serialize `payments` (plus optional change) into the template transaction — there is no parameter for the `data` output, which can be up to 80 bytes plus output/script overhead (~90+ bytes serialized, ~360+ weight units).

Consequences within the same function:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206) understates the real vsize of the final transaction by the size of the OP_RETURN output. `self.fee()` is `sum(inputs) - sum(outputs)` (line 139–140), and since the change calculation uses the same underestimated `fee_with_change` (line 227–232), the extra bytes are silently funded by the change output — the actual fee rate is strictly lower than `fee_per_vbyte`.
2. The standardness guard `weight > MAX_STANDARD_TX_WEIGHT` (line 241) uses the `weight` computed without the data output, so a transaction up to ~90+ bytes over the standard limit can be constructed, signed by `TransactionSignMachine::sign`, and broadcast-attempted while every relay node rejects it as non-standard.
3. The `TooLowFee` check (line 211) validates the *computed* fee against the *underestimated* vbytes, so the effective fee rate can fall below `DEFAULT_MIN_RELAY_TX_FEE`, again yielding an unrelayed transaction.

`data` is reachable by an unprivileged party: in the processor, the `data` argument originates from user-supplied `OutInstruction` payloads on withdrawal/burn requests, i.e., public inputs an unauthenticated user causes the multisig to sign over.

### Impact Explanation
- **Fee theft-adjacent economics / stuck spends**: the actual fee rate is below what was requested, which under congested mempools means the signed transaction is never confirmed — payments stall while the inputs remain locked by the scheduler's plan.
- **Permanently non-standard transaction**: at the `MAX_STANDARD_TX_WEIGHT` boundary (large multisig spends have many inputs), the omitted OP_RETURN weight pushes the real transaction over the limit. The FROST signing round completes and produces a transaction no node will accept. The funds aren't lost (inputs remain in the wallet), but every retry of the same plan reproduces an unbroadcastable transaction — a liveness failure requiring operator intervention.
- This is analogous to the report's impact: attacker-influenced content is "included" in the server's output without being validated/accounted, causing the system to produce an artifact whose real cost/validity it never checked.

### Likelihood Explanation
Any caller supplying `data` (a normal, documented input to `SignableTransaction::new`, fed by user-controlled `OutInstruction` payloads) triggers the miscalculation deterministically — no race or malicious internal state needed. The only reason the impact isn't worse is that ~90 bytes is a small relative fee error and the weight-limit violation requires a transaction already near the standardness cap. Medium severity at most; it is a correctness defect with deterministic reachability, not a secret-disclosure bug.

### Recommendation
Pass the fully-formed `tx_outs` (or the data output's serialized size) into `calculate_weight_vbytes` so that `vbytes`, `weight`, `needed_fee`, and `fee_with_change` all account for the OP_RETURN output, e.g. change the signature to take `&[TxOut]`/`payments` plus `data_len`, or compute weight from the final `Transaction` built with `tx_outs` before deciding change (recomputing after change insertion). Additionally, compute the `MAX_STANDARD_TX_WEIGHT` check against the final transaction's actual `tx.weight()` rather than the template's.

### Proof of Concept
In `networks/bitcoin/tests/wallet.rs` (or a standalone harness):

```rust
let (keys, key) = keys();
let mut scanner = Scanner::new(key).unwrap();
let output = send_and_get_output(&rpc, &scanner, key).await;

// 80-byte data payload, the maximum permitted
let data = vec![0xaa; 80];
let tx = SignableTransaction::new(
  vec![output.clone()],
  &[(p2tr_script_buf(key).unwrap(), output.value() - 100_000)],
  None,
  Some(data.clone()),
  FEE,
).unwrap();

// The actual fee paid:
let actual_fee = tx.fee();
// tx.needed_fee() was computed from a template WITHOUT the OP_RETURN output,
// so the real vsize exceeds the estimated one by ~90 bytes:
let real_vsize = /* consensus-serialize tx.transaction() + dummy witnesses */;
assert!(real_vsize as u64 * FEE > tx.needed_fee());
// i.e. actual_fee / real_vsize < FEE — below the caller-requested rate
```

Equally, constructing a transaction whose `payments` template weighs exactly `MAX_STANDARD_TX_WEIGHT` and adding any `data` yields a signed transaction whose real weight exceeds `MAX_STANDARD_TX_WEIGHT`, making it unrelayable despite `SignableTransaction::new` returning `Ok`.