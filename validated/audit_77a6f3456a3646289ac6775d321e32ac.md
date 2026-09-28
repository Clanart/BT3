### Title
Fee/vsize calculation omits the OP_RETURN data output, so `needed_fee` and the minimum-relay-fee check do not represent the actual transaction — ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
Analogous to the Ubiquity report — where `getDollarPriceUsd()` returned a price in 3CRV rather than the USD value the threshold checks required — `SignableTransaction::new` computes the transaction's virtual size and required fee from `payments` only, even though an OP_RETURN `data` output has already been appended to the real `tx_outs`. The value used to represent the transaction's size (`vbytes`/`vbytes_with_change`) is therefore not the size of the transaction actually constructed, and every check that depends on it (`TooLowFee`, `NotEnoughFunds`, `needed_fee`) operates on a wrong quantity.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, the OP_RETURN output is pushed into `tx_outs` *before* the weight/vbytes are calculated:

```rust
// send.rs:193-204
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` builds its dummy transaction from `payments` plus an optional `change` — the `data` output is never passed to it (lines 62-99). The same omission occurs in the change path (`vbytes_with_change`, lines 225-227).

Consequences:
- `needed_fee = fee_per_vbyte * vbytes` is computed on a vbytes smaller than the real transaction. When change is created, the change amount is `input_sat - payment_sat - fee_with_change`, so the *actual* fee paid equals the underestimated `fee_with_change` — the transaction achieves a lower fee rate than the caller specified.
- The `TooLowFee` check at line 211 compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the undercounted `vbytes`. A transaction whose true vsize is larger can pass this check while its real fee rate falls below the minimum relay fee, producing a signed transaction the network will not relay.
- `weight > MAX_STANDARD_TX_WEIGHT` (line 241) also uses a weight missing the data output's contribution (marginal, since the cap is large).

Like the original report, a function's documented semantic (`needed_fee`: "the fee necessary for this transaction to achieve the fee rate specified") is backed by a value that does not represent the actual object.

### Impact Explanation
A transaction constructed with `data` set is created with a fee below what `fee_per_vbyte` promises, and in the worst case below the minimum relay fee — the resulting fully-signed transaction cannot be broadcast/confirmed, leaving the inputs' funds unspendable via that transaction (it must be reconstructed with a corrected fee). It also returns a `needed_fee()` value that does not correspond to the built transaction, and `fee()` (actual fee) can diverge from it only via the change-omission path. This is a correctness bug in fee math reachable purely by supplying a `data` payload, requiring no privileged position.

### Likelihood Explanation
Any caller of `SignableTransaction::new` that supplies `data` (the OP_RETURN path exists specifically to carry caller-supplied payloads, e.g., cross-chain memos in Serai's usage) triggers the miscalculation. The divergence is small (a ~10-90 byte output), so it only causes a consensus/policy-visible failure when the requested fee rate is near the minimum relay floor, but the `needed_fee`/`fee` values are always wrong when `data` is present.

### Recommendation
Compute weight/vbytes over the actual outputs to be committed: pass the data output (or the fully assembled `tx_outs` shape, mirroring how `change` is already handled) into `calculate_weight_vbytes`. Concretely, extend `calculate_weight_vbytes` to accept an `Option<&ScriptBuf>` for the OP_RETURN script and include it in the dummy `tx.output` list in both the no-change and with-change calculations.

### Proof of Concept
```rust
// Construct a SignableTransaction with a data payload
let data = vec![0xaa; 80]; // max allowed
let tx = SignableTransaction::new(
  inputs,               // e.g., one ReceivedOutput
  &[(payment_script, payment_amount)],
  Some(change_script),
  Some(data),
  fee_per_vbyte,
).unwrap();

// The committed transaction includes a 92-byte OP_RETURN output:
assert_eq!(tx.transaction().output.len(), /* payments + 1 OP_RETURN + change */);

// But needed_fee was computed as fee_per_vbyte * vbytes where vbytes
// excluded that output, so:
let actual_vbytes = /* vsize of tx.transaction() */;
assert!(tx.needed_fee() < fee_per_vbyte * actual_vbytes);
// i.e. the transaction does not achieve the requested fee rate,
// and may fall under DEFAULT_MIN_RELAY_TX_FEE relative to its true size.
```

Concrete trace: `data` is appended at `send.rs:194-202`; `calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204 receives only `payments`; its dummy tx at lines 85-94 maps `payments` and line 98 adds only `change` — no branch ever inserts the OP_RETURN output, so both `weight`/`vbytes` and the derived `needed_fee`, `TooLowFee`, `NotEnoughFunds`, and `MAX_STANDARD_TX_WEIGHT` checks evaluate a different (smaller) transaction than the one stored in `SignableTransaction.tx`.

Caveat: I verified this path in `send.rs`/`mod.rs` only; I did not have remaining iterations to check whether callers sanitize `data` or recompute fees elsewhere, though `SignableTransaction`'s own documented contract (`needed_fee` "to achieve the fee rate specified at construction") is violated by this code alone.