### Title
`SignableTransaction` computes fees and weight while omitting the attacker-supplied OP_RETURN output, producing transactions that pay less than the requested fee rate and can fall below the minimum relay fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

The Li.finance incident is a "contract vulnerability" class bug: attacker-influenced data flowed into a privileged action and produced an outcome the caller never authorized (arbitrary call draining approvals). The analogous shape in Serai is `SignableTransaction::new`, where caller/attacker-influenced transaction data (the `data` argument that becomes an OP_RETURN output) is added to the real transaction's outputs but is excluded from the size estimate used to compute the fee and to enforce the minimum-relay-fee and standard-weight checks. The signed transaction therefore carries more bytes than were priced — it pays a lower effective feerate than `fee_per_vbyte` specified, and can be silently below the network minimum relay fee.

### Finding Description

`SignableTransaction::new` builds `tx_outs` and pushes the OP_RETURN `data` output into the actual transaction at `send.rs:194-202`. However, the weight/vsize estimation at `send.rs:204` calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — passing only `payments`, not the OP_RETURN output that was already committed to `tx_outs`. The same omission occurs in the change branch at `send.rs:225-226`, which again prices only `payments` + change.

Consequences:

- `needed_fee = fee_per_vbyte * vbytes` (line 206) undercounts vsize by the entire serialized OP_RETURN output (9 bytes overhead + up to 80 bytes of data ≈ up to ~90 vbytes, since `data` up to 80 bytes is permitted by the `TooMuchData` check at line 171).
- The `TooLowFee` guard at line 211 compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes` using the same underestimated `vbytes`, so a transaction whose *actual* feerate is below the relay minimum is accepted.
- The `MAX_STANDARD_TX_WEIGHT` check at line 241 uses `weight`, which also excludes the data output, so a maximally-sized transaction plus an 80-byte OP_RETURN can exceed the standard weight limit while passing the check.
- `fee()` (line 138) and `needed_fee()` (line 133) therefore report different economics than what the signed transaction actually achieves: `needed_fee()` reports a fee for `vbytes` that is smaller than the real `tx.vsize()`.

The reachability matches the analog class: `data` and `fee_per_vbyte` are parameters of the transaction the threshold group is asked to sign — i.e., transaction data a party causes to be signed, which is a public input per the scope rules. `TransactionSignMachine::sign` then signs `taproot_key_spend_signature_hash` over the *real* `self.tx.tx` (which includes the OP_RETURN output) via `Prevouts::All` at `send.rs:373-390`, so the discrepancy between priced size and signed size is committed into the signature.

### Impact Explanation

A sign order carrying a large `data` payload and a minimal `fee_per_vbyte` yields a fully signed, consensus-valid transaction whose effective feerate is lower than requested and can be below `DEFAULT_MIN_RELAY_TX_FEE`. Such a transaction will not relay/confirm, leaving the input outpoints pinned to a fee-doomed TXID until a replacement attempt is organized — a liveness/funds-stuck condition on threshold-controlled Bitcoin outputs. More directly, it violates the function's contract that the transaction pays `fee_per_vbyte` sat/vbyte: callers computing budget via `needed_fee()`/`fee()` receive a fee that was never sized for the transaction actually signed. This is an incorrect accounting/verifier-formula analog: the price-check formula does not cover all bytes the signature commits to.

### Likelihood Explanation

Any signing request that includes `data` (an OP_RETURN output) triggers the undercount deterministically — no race or exotic precondition is needed. The miscount is bounded (~90 vbytes max), so practical harm requires a low specified feerate where ~90 extra vbytes push the real feerate under the relay minimum, or push weight over `MAX_STANDARD_TX_WEIGHT`; both are reachable at the edges the constructor explicitly tries to guard. The bug is deterministic logic error, not probabilistic.

### Recommendation

Compute size over the same output set that will be signed. Pass the already-built `tx_outs` (or at least the OP_RETURN output) into `calculate_weight_vbytes` — e.g., change its signature to take `outputs: &[TxOut]` and call it after `tx_outs` is fully populated for both the no-change and change paths. Then derive `needed_fee`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check from that complete weight. Add a regression test asserting `tx.vsize() * fee_per_vbyte <= needed_fee` for transactions with `data`.

### Proof of Concept

```rust
// Construct a signable TX with a large OP_RETURN payload and a feerate at the
// relay floor. Networks/bitcoin/src/wallet/send.rs:
//   - line 195: tx_outs.push(OP_RETURN output)        <- included in signed TX
//   - line 204: calculate_weight_vbytes(n, payments, None)  <- excludes it
//   - line 211: TooLowFee check uses underestimated vbytes

let output = /* a scanned ReceivedOutput */;
let data = vec![0x42; 80]; // max allowed by TooMuchData check (line 171)

// 1 sat/vbyte; passes TooLowFee because vbytes is undercounted
let tx = SignableTransaction::new(
    vec![output], &[], None, Some(data), /* fee_per_vbyte */ 1,
).unwrap();

// tx.needed_fee() == 1 * vbytes(payments-only estimate)
// tx.transaction().vsize() is larger by ~90 vbytes (the OP_RETURN output)
assert!(tx.transaction().vsize() as u64 > tx.needed_fee());
// Real feerate = needed_fee / actual_vsize < 1 sat/vbyte,
// i.e. below DEFAULT_MIN_RELAY_TX_FEE -> transaction will not relay,
// yet the multisig will sign it (send.rs:383-390 signs the full tx).
```