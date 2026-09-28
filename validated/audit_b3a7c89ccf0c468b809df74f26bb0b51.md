### Title
SignableTransaction fee and weight estimation omit the OP_RETURN data output, undercharging fees and bypassing the max-standard-weight check - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report describes a bug class where a hardcoded/stale resource estimate is used to check whether an operation can complete, causing the check to overestimate available resources. The analog in Serai is not a hardcoded constant but a stale/incomplete transaction template: `SignableTransaction::new` computes the transaction weight, vbytes, `needed_fee`, and the `MAX_STANDARD_TX_WEIGHT` check using a template transaction that omits the caller-supplied OP_RETURN `data` output. The fee actually paid (`inputs - outputs`) therefore corresponds to a lower effective fee rate than `fee_per_vbyte`, and the standard-weight bound can be exceeded without triggering `TooLargeTransaction`.

### Finding Description
In `SignableTransaction::new`, the caller may pass `data: Option<Vec<u8>>` (up to 80 bytes), which is pushed onto `tx_outs` as a zero-valued OP_RETURN output at `send.rs:194-202`. However, both weight/vbyte estimations are computed from `payments` only:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
// ...
let (weight_with_change, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
```

(`networks/bitcoin/src/wallet/send.rs:204`, `:225-226`)

`calculate_weight_vbytes` builds the template transaction's `output` list solely from `payments` plus optional `change` (`send.rs:85-99`); the OP_RETURN output already appended to `tx_outs` is never included. Consequently:

1. `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`) is computed on a vsize smaller than the final transaction's. The actual fee is `sum(inputs) - sum(outputs)` (`send.rs:138-141`), so the effective sat/vbyte rate of the broadcast transaction is strictly below `fee_per_vbyte`.
2. The `TooLowFee` check at `send.rs:211` compares the underestimated `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the same underestimated vbytes — a transaction whose real fee rate falls below the minimum relay fee can pass this check.
3. The `TooLargeTransaction` check at `send.rs:241` tests the underestimated `weight`, so a final transaction that actually exceeds `MAX_STANDARD_TX_WEIGHT` (non-standard, not relayed/mined by default policy) is accepted by the constructor.

The `data` bytes are untrusted caller input to a public constructor (`pub fn new`), matching the reachable-public-input requirement; the resulting `SignableTransaction` is then signed via `TransactionSignMachine::sign` / `TransactionSignatureMachine::complete` (`send.rs:355-428`) which fill witnesses without re-checking weight or fee.

### Impact Explanation
A transaction constructed and signed by Serai's Bitcoin wallet can pay an effective fee rate below both the caller's requested `fee_per_vbyte` and the network minimum relay fee, or exceed `MAX_STANDARD_TX_WEIGHT`, while all of the constructor's own safety checks pass. Such transactions will not relay or confirm under standard policy, leaving payments unexecuted and the associated inputs effectively stuck until a replacement is coordinated — the exact "funds committed to a transaction that cannot complete" consequence of the reported bug class. Each OP_RETURN output also adds its own weight (~`data.len() + ~10` bytes serialized), so the discrepancy grows with the caller-controlled `data`.

### Likelihood Explanation
The processor currently passes `data: None` (`processor/src/networks/bitcoin.rs:450`), so the discrepancy only triggers for integrators using the `data` parameter of the public `bitcoin_serai::wallet::SignableTransaction::new` API, or transactions already near the weight limit. The miscalculation is deterministic whenever `data.is_some()` — no attacker sophistication is needed beyond supplying the bytes — but the impact is bounded (up to ~80 bytes of unaccounted weight, i.e. a fee shortfall of up to ~`80 * fee_per_vbyte` sats plus output overhead, or pushing a near-limit transaction over the standardness boundary). Medium likelihood of silent underpayment/non-standardness when the feature is used, hence Medium severity.

### Recommendation
Include the OP_RETURN output in the weight/vbytes template before computing `needed_fee` and performing the `TooLowFee`/`TooLargeTransaction` checks. Concretely, change `calculate_weight_vbytes` to accept the full output list (payments + optional change + optional data OP_RETURN), or move the `data` handling earlier so `tx_outs` is complete before estimation; alternatively, build the final `Transaction` once and call `tx.weight()` on it directly rather than maintaining a parallel template. Also re-derive `weight` for the check at `send.rs:241` from the actual `tx` being signed.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs logic, conceptually:
let data = vec![0u8; 80];
// tx_outs gets 3 outputs: payment + OP_RETURN(+ maybe change)
let (_, vbytes) = SignableTransaction::calculate_weight_vbytes(
    tx_ins.len(), payments, None,
);
// vbytes counts ONLY payments (+change), not the OP_RETURN output.
let needed_fee = fee_per_vbyte * vbytes; // underestimates true required fee

// The constructed tx.output includes the OP_RETURN output:
//   tx_outs.push(TxOut { value: 0, script_pubkey: op_return(data) })
// so tx.weight() > the `weight` used in:
//   weight > MAX_STANDARD_TX_WEIGHT   // check bypassed
//   needed_fee = fee_per_vbyte * vbytes // undercharged
```
For a concrete demonstration: call `SignableTransaction::new(inputs, payments, change, Some(vec![0; 80]), fee_per_vbyte)`, then compare `signable.needed_fee()` and `signable.transaction().weight()` — the real transaction's weight exceeds the estimated value used for the fee and `MAX_STANDARD_TX_WEIGHT` check, and `signable.fee() / real_vsize < fee_per_vbyte`.