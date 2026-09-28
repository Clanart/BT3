### Title
`SignableTransaction::new` omits the OP_RETURN data output when calculating transaction vsize, undercharging the fee — (`networks/bitcoin/src/wallet/send.rs`)

### Summary

`SignableTransaction::new` pushes an `OP_RETURN` output onto `tx_outs` but computes `vbytes`/`weight` via `calculate_weight_vbytes(tx_ins.len(), payments, ...)` which only models the `payments` outputs and (optionally) change — never the data output. The declared `needed_fee` therefore does not account for the OP_RETURN output's size, an accounting omission analogous to a balance not being reduced after unstake: a resource (bytes carrying weight) exists in the final transaction but is excluded from the accounting that prices it. [1](#0-0) [2](#0-1) 

### Finding Description

The data output is appended to `tx_outs` at `send.rs:194-202`, before `vbytes` is computed at line 204 using only `tx_ins.len()` and `payments`. `calculate_weight_vbytes` (lines 62-127) builds a template transaction from `payments` plus optional `change`; it has no parameter for the data output, so the ~10-110 extra weight units of a populated `OP_RETURN` (80 bytes of data plus script overhead) are invisible to:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206) — the fee the caller is told is needed.
2. The minimum-relay-fee check at lines 211-213 — evaluated against the underestimated vbytes.
3. The `MAX_STANDARD_TX_WEIGHT` check at line 241 — the real transaction is heavier than `weight`.

When change is present, the second call at lines 225-227 also omits the data output, so `fee_with_change` is likewise undercharged and the change amount at line 228 (`input_sat - payment_sat - fee_with_change`) is over-credited by the same missing amount — directly inflating the change "balance" returned to the vault, mirroring the unstake bug's inflated `stakeBalance`.

`TransactionSignMachine::sign` then binds the signatures to the real (larger) transaction via `taproot_key_spend_signature_hash` with `Prevouts::All`, so the signed tx commits to the OP_RETURN but its fee/change were priced without it. [3](#0-2) [4](#0-3) [5](#0-4) 

### Impact Explanation

Two concrete harms follow from the missing accounting:

1. **Overpaid change / silently larger effective fee... or underpaid fee.** If `change` is set, the change output is credited with `input_sat - payment_sat - fee_with_change` where `fee_with_change` was computed on a smaller transaction, so change is larger than intended while the *actual* fee rate falls below the requested `fee_per_vbyte`. If no change is set, the leftover is absorbed as fee, so the effective feerate may still end up near the requested one — but `needed_fee()` reports a value inconsistent with `fee()`, breaking callers that rely on it (as the test does at `networks/bitcoin/tests/wallet.rs:269`).

2. **Unbroadcastable transactions.** The minimum-relay check uses the underestimated `vbytes`. A caller specifying `fee_per_vbyte` at/near the relay minimum while including an up-to-80-byte `data` payload produces a transaction whose *actual* feerate is below `DEFAULT_MIN_RELAY_TX_FEE`, so Bitcoin Core rejects it (`min relay fee not met`). An unprivileged party who can cause a transaction carrying data (e.g., an `InInstruction`/withdrawal flow that attaches attacker-influenced data) can therefore produce a signed transaction that cannot be relayed, stalling funds — the analog of rewards made unclaimable.

### Likelihood Explanation

The gap is deterministic whenever `data` is `Some`. Whether it is attacker-reachable depends on the integrator: any flow where a user's payment request can carry an OP_RETURN payload (Serai's own `extract_serai_data`/InInstruction pattern indicates data-bearing transactions are first-class) lets an attacker maximize the discrepancy with the full 80 bytes. Triggering relay rejection additionally requires the configured feerate to be near the minimum, which is the normal operating point for cost-sensitive batching. Severity is Medium: the outcome is a DoS/mispriced fee rather than direct theft, but it stems from a concrete incorrect accounting formula reachable via transaction data an unprivileged user can influence.

### Recommendation

Pass the fully-constructed `tx_outs` (or at least the `data` output) into `calculate_weight_vbytes` so that `vbytes`, `needed_fee`, the minimum-relay check, the change computation, and the `MAX_STANDARD_TX_WEIGHT` check all account for the OP_RETURN output. Concretely, build the data `TxOut` first, then compute weight over `payments + data + optional change`, mirroring exactly the outputs that will appear in `self.tx`.

### Proof of Concept

```rust
// networks/bitcoin/src/wallet/send.rs semantics
let data = vec![0u8; 80];
let tx = SignableTransaction::new(
    inputs,                       // e.g. a single 10_000 sat input
    &[(payment_script, 5_000)],
    None,
    Some(data.clone()),           // OP_RETURN output IS added to tx.output
    1,                            // 1 sat/vbyte, near DEFAULT_MIN_RELAY_TX_FEE
).unwrap();

// vbytes was computed over inputs+payments only, excluding the ~95-byte
// OP_RETURN output pushed at lines 194-202.
let real_vsize = tx.transaction().vsize() as u64;
assert!(tx.needed_fee() < real_vsize * 1);   // fee undercharged vs. requested rate

// With change: change is over-credited by (real_vsize - priced_vsize) * fee_per_vbyte.
// Without change at min feerate: actual feerate < DEFAULT_MIN_RELAY_TX_FEE and the
// signed transaction is rejected by Bitcoin Core's mempool.
```

The fix-site discrepancy is visible by inspection: `tx_outs` gains the OP_RETURN at lines 194-202 while the `vbytes` used for all fee math at line 204 is derived from `payments` only.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L85-94)
```rust
      output: payments
        .iter()
        // The payment is a fixed size so we don't have to use it here
        // The script pub key is not of a fixed size and does have to be used here
        .map(|payment| TxOut {
          value: Amount::from_sat(payment.1),
          script_pubkey: payment.0.clone(),
        })
        .collect(),
    };
```

**File:** networks/bitcoin/src/wallet/send.rs (L194-213)
```rust
    if let Some(data) = data {
      tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(
          PushBytesBuf::try_from(data)
            .expect("data didn't fit into PushBytes depsite being checked"),
        ),
      })
    }

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
```rust
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-390)
```rust
    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
```
