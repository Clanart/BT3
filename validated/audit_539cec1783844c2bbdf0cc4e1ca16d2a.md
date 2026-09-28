### Title
Unchecked arithmetic overflow in `SignableTransaction::new` causes panic / bypasses the `NotEnoughFunds` check - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` in `bitcoin-serai` computes `payment_sat` as a raw `u64` sum over caller-supplied payment amounts, multiplies `fee_per_vbyte * vbytes`, and then adds `payment_sat + needed_fee` — all without checked arithmetic. An unprivileged party who can cause payment instructions (amounts) to be included in a transaction can overflow these values, producing a panic (debug / `overflow-checks` builds) or silent wrap-around (release), analogously to the MySQL optimizer crash/hang class (unauthenticated-reachable denial of service via crafted input). The wrap-around additionally defeats the `NotEnoughFunds` gate, causing an unspendable/invalid transaction to be carried into the FROST signing session.

### Finding Description
The vulnerable arithmetic lives in three places in `SignableTransaction::new`:

- `let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();` — saturating `sum()` is not used; a payment list whose individual `u64` amounts total more than `u64::MAX` wraps (release) or panics (debug). [1](#0-0) 
- `let mut needed_fee = fee_per_vbyte * vbytes;` — multiplication overflow with a large fee rate. [2](#0-1) 
- `if input_sat < (payment_sat + needed_fee)` — the addition itself can overflow *before* the comparison runs, so the guard intended to reject unaffordable transactions can be bypassed entirely. [3](#0-2) 

Each payment amount is independently validated only against `DUST` (546 sats) — there is no upper bound and no check on the cumulative total. [4](#0-3) 

Downstream, `fee()` repeats the same pattern with `sum::<u64>()` over prevouts minus `sum::<u64>()` over outputs, which underflows if outputs exceed inputs — possible precisely because the `NotEnoughFunds` check was bypassed. [5](#0-4) 

### Impact Explanation
- **Crash (availability):** with overflow checks enabled, `payment_sat + needed_fee` (or the `sum()`/`fee_per_vbyte * vbytes` overflow) panics inside transaction construction, killing the task responsible for building the threshold-signed payout — a repeatable crash driven by attacker-chosen amounts. This mirrors the CVE's "easily exploitable … complete DOS" shape.
- **Invalid transaction enters signing (release):** with wrapping arithmetic, `payment_sat` wraps to a small value, `input_sat < payment_sat + needed_fee` evaluates false, and a `SignableTransaction` whose outputs exceed its inputs is returned. `multisig()` then binds it to the FROST machines and `sign()` computes the Taproot sighashes — every participant signs an inherently invalid transaction that can never confirm, stalling that payment batch and burning the round-trip of a threshold signing session.

### Likelihood Explanation
Reachability requires the scheduler/integrator to hand attacker-influenced payment amounts into `SignableTransaction::new` — which is the designed input path (payments correspond to user-requested withdrawals). Overflowing `u64` requires either absurd total amounts (sum > ~1.8×10¹⁹ sats, economically unrealistic for payments alone) or an operator-supplied `fee_per_vbyte` large enough to overflow the multiplication — the latter is a deployment parameter, not attacker input. The panic path is therefore concretely reachable in debug/`overflow-checks` builds; in release the wrap-around of the funds check is reachable only at economically extreme totals. Medium likelihood at best.

### Recommendation
Use checked arithmetic throughout `SignableTransaction::new` and `fee()`: `checked_add`/`checked_mul`/`checked_sum` for `payment_sat`, `input_sat`, `needed_fee`, and `payment_sat + needed_fee`, returning a `TransactionError` on overflow. Also apply `checked_sub` in `fee()` so an inconsistency between inputs and outputs cannot silently wrap to a huge fee value.

### Proof of Concept
Conceptual: invoke `SignableTransaction::new` with a `payments` slice containing multiple entries of value `u64::MAX / 2 + 1` (each ≥ `DUST`, passing the per-payment check) and `fee_per_vbyte = 1`. `payment_sat` overflows: panic under `debug`/`overflow-checks = on`; in release it wraps below `input_sat`, the `NotEnoughFunds` check at send.rs:215 is skipped, and a `SignableTransaction` with unaffordable outputs is produced and subsequently passed to `multisig()`/`sign()`, which will compute sighashes over an invalid transaction. [6](#0-5) 

Caveat: I did not exhaustively verify every `read_*` deserialization path (e.g., PedPoP `EncryptedMessage::read`, `Commitments::read` in `crypto/frost/src/nonce.rs`) for unbounded allocations or panics; the overflow above is the strongest panic/DoS analog I could confirm within the iteration budget.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-235)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();

    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();

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

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }

    // If there's a change address, check if there's change to give it
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
