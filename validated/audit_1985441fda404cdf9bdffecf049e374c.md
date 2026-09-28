### Title
`SignableTransaction::new` omits the OP_RETURN data output from the transaction weight used for fee and change calculation — (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
The reported bug class is a value-computation mismatch: a correct value (`entitledShares`) is computed, yet a different, stale value (`queue[index].assets`) is actually used, and the difference is lost to the user. The Serai analog lives in `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`. The weight/vbytes used to compute `needed_fee`, the change amount, and the minimum-relay-fee check are all derived from `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which builds a dummy transaction containing only the payment outputs. The OP_RETURN `data` output, pushed to `tx_outs` earlier at lines 194-202, is never included in that weight calculation.

### Finding Description
At `send.rs:194-202`, an OP_RETURN output carrying up to 80 bytes of caller-controlled `data` is appended to `tx_outs`. However, both fee computations ignore it:

- `send.rs:204`: `let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);`
- `send.rs:226`: `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));`

`calculate_weight_vbytes` (`send.rs:62-127`) constructs a transaction whose `output` vector is populated only from `payments` plus the optional change output. The OP_RETURN output (10+ bytes of output overhead plus up to 80 bytes of script) is absent, so `vbytes` and `vbytes_with_change` undercount the real virtual size by up to ~100 vbytes.

Consequences that follow from this single mismatch:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206, 227) undercharges relative to the specified fee rate — the actual fee paid is `sum(inputs) - sum(outputs) = fee_with_change` (line 230 sets the change to exactly `input_sat - payment_sat - fee_with_change`), so the effective sat/vbyte rate of the broadcast transaction is strictly lower than `fee_per_vbyte`.
2. The minimum-relay check at lines 211-213 compares the undercounted `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes` computed on the same undercounted `vbytes`, so a transaction whose true required minimum fee is higher can pass validation yet be rejected by the Bitcoin mempool.
3. The `NotEnoughFunds` check at line 215 and the change branch condition `input_sat.checked_sub(payment_sat + fee_with_change)` at line 228 both use the undercounted fee, so a change output may be created (or the transaction accepted) where the true fee requirement would have left insufficient funds or sub-dust change — the difference effectively vanishes into either an underpaid fee or an over-credited change plan that doesn't match the real transaction.

Just as in the Carousel bug where `entitledShares` was computed correctly but `assetsToMint` used the wrong value and the difference was silently lost, here the real transaction weight exists but every downstream accounting decision (fee, change, minimum-fee gate, weight limit at line 241) is made on a transaction that isn't the one being signed.

### Impact Explanation
The processor's `Batch` transactions carry an OP_RETURN containing batch metadata driven by user burn operations, so `data` is non-empty in production use. Every such transaction systematically pays a lower effective fee rate than the configured `fee_per_vbyte`. At the boundary, the transaction can pass Serai's `TooLowFee` check while falling below the real `DEFAULT_MIN_RELAY_TX_FEE` for its true size, producing a transaction that is validly signed yet unrelayed — locking the multisig's UTXOs behind an unconfirmable transaction until the flow is retried. The `TooLargeTransaction` check at line 241 is also computed on a weight that excludes the OP_RETURN output, allowing a transaction that exceeds `MAX_STANDARD_TX_WEIGHT` to be accepted and signed. Severity: Medium — funds/liveness impact on transactions the multisig signs, reachable through transaction data an unprivileged user's burns cause to be created, without requiring any malicious validator behavior.

### Likelihood Explanation
Any call to `SignableTransaction::new` with `data.is_some()` triggers the miscalculation deterministically — no adversarial setup beyond causing a burn/batch that embeds OP_RETURN data is needed. The miscount is constant (~10-100 vbytes per transaction), so whether it crosses the min-relay boundary or the standard-weight boundary depends on the configured `fee_per_vbyte` and transaction size, but the effective-fee-rate reduction occurs on every data-carrying transaction.

### Recommendation
Include the data output in the weight calculation. Pass the full output list (payments plus the OP_RETURN output) into `calculate_weight_vbytes`, or add the OP_RETURN output's exact vsize (`8 + compactsize(script_len) + script_len`) to the weight before computing `needed_fee`. Concretely, restructure so that `tx_outs` — including the OP_RETURN — is what `calculate_weight_vbytes` measures, rather than `payments` alone:

```rust
// build tx_outs (payments + OP_RETURN) first, then measure over tx_outs
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), &tx_outs, None);
```

with `calculate_weight_vbytes` taking `&[TxOut]` instead of `&[(ScriptBuf, u64)]`, or equivalently pushing the OP_RETURN into the template before measurement.

### Proof of Concept
Conceptual PoC against `networks/bitcoin/src/wallet/send.rs`:

```rust
let data = vec![0u8; 80];
let st = SignableTransaction::new(
    vec![received_output],          // one P2TR ReceivedOutput
    &[(payment_script, DUST)],      // one payment
    None,                           // no change
    Some(data),                     // OP_RETURN output added at send.rs:194-202
    10,                             // fee_per_vbyte
).unwrap();

// The real transaction includes the OP_RETURN output:
let real_vbytes = /* vsize of st.transaction() */;              // larger
// needed_fee was computed WITHOUT the OP_RETURN output:
assert!(st.needed_fee() < 10 * real_vbytes);                    // underpays specified rate
// st.fee() == st.needed_fee() exactly, since fee = inputs - outputs
assert_eq!(st.fee(), st.needed_fee());
```

`st.fee()` equals the undercounted `needed_fee`, so the broadcast transaction achieves an effective fee rate strictly below the requested `fee_per_vbyte`, and the `TooLowFee`/`TooLargeTransaction` gates were evaluated against a transaction ~100 weight units smaller than the one actually signed by `TransactionSignMachine::sign` (`send.rs:373-390`).

Note on scope of verification: I confirmed the OP_RETURN omission directly in `send.rs`. I did not exhaustively examine `networks/bitcoin/src/wallet/mod.rs` (scanner/`register_offset`) or the `crypto/dkg` PedPoP paths for additional analogs of this accounting-mismatch class; those may warrant follow-up, but the `send.rs` finding stands on its own as a reachable, concrete value-mismatch analog.