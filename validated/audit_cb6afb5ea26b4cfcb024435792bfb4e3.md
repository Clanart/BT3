### Title
Integer overflow in payment/fee arithmetic lets a plan with payouts exceeding available inputs be signed - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` validates funding with the unchecked addition `payment_sat + needed_fee` (and later `payment_sat + fee_with_change`), where `payment_sat` is the `u64` sum of attacker-influenced payment amounts and `needed_fee` is `fee_per_vbyte * vbytes` (an unchecked multiplication). In release builds, these wrap. A crafted set of payments whose amounts sum past `u64::MAX` wraps the comparison value below `input_sat`, so `NotEnoughFunds` is bypassed and the threshold signs a transaction whose declared outputs cannot be covered by its inputs.

### Finding Description
The funding check is:

```rust
if input_sat < (payment_sat + needed_fee) {
  Err(TransactionError::NotEnoughFunds { ... })?;
}
``` [1](#0-0) 

`payment_sat` is computed as `payments.iter().map(|payment| payment.1).sum::<u64>()`, which itself wraps in release mode if the individual `u64` amounts overflow [2](#0-1) . `needed_fee` is `fee_per_vbyte * vbytes`, again an unchecked `u64` multiplication [3](#0-2) . The change path uses `input_sat.checked_sub(payment_sat + fee_with_change)` — the inner `payment_sat + fee_with_change` can also wrap before the `checked_sub`, so a value that should be negative can appear positive and produce a change output [4](#0-3) . Each payment only has to satisfy `amount >= DUST` (546 sats) — there is no per-payment or total upper bound tied to available inputs before the overflowable arithmetic [5](#0-4) . Once the checks pass, the transaction is built and handed to the FROST signing machines, which sign each input's Taproot sighash unconditionally [6](#0-5) .

### Impact Explanation
The accounting invariant "sum(outputs) + fee ≤ sum(inputs)" is enforced entirely by this arithmetic. When it wraps, the multisig signs a transaction that is either consensus-invalid (total outputs exceeding inputs / output value exceeding `MAX_MONEY`) or that allocates change incorrectly. Concretely: a user-requested withdrawal plan with payment amounts crafted to overflow (e.g., multiple payments of ~2^63 sats each, each individually `>= DUST`) bypasses `NotEnoughFunds`, causes the processor to consume/burn its allocated `ReceivedOutput` inputs in `prevouts`, and produces a signed transaction that can never be confirmed — the plan is recorded as executed while the funds are not delivered (funds reported sent that are not spendable). The inputs referenced remain committed to that plan's outputs in Serai's bookkeeping.

### Likelihood Explanation
Reachability requires the payment amounts to reach `SignableTransaction::new` — payments originate from in-instructions / scheduler plans, which are driven by user withdrawal requests carrying user-chosen amounts. Whether upstream layers clamp payment totals to real balances determines exploitability; within `bitcoin-serai` itself no such clamp exists. This is a Medium: it corrupts fund accounting / produces unconfirmable signed spends rather than enabling direct theft, since a transaction with outputs exceeding inputs cannot be broadcast validly.

### Recommendation
Use `checked_add`/`checked_mul`/`checked_sum` for `payment_sat`, `needed_fee`, `fee_with_change`, and `payment_sat + needed_fee`, erroring on overflow. Also bound each payment amount and the payment total to `input_sat` before arithmetic (e.g., `input_sat.checked_sub(payment_sat).and_then(|r| r.checked_sub(needed_fee))`), matching the existing `checked_sub` style already used for change.

### Proof of Concept
```rust
// release build (wrapping arithmetic)
let inputs = vec![received_output_with_value(1_000_000)]; // 0.01 BTC input
// two payments, each >= DUST, whose u64 sum wraps to a small value
let payments = vec![
    (script_a, u64::MAX - 1000),
    (script_b, u64::MAX - 1000),
];
// payment_sat wraps to ~u64::MAX - 2002... still huge; better: choose
// amounts summing to exactly 2^64 + k so payment_sat wraps to k.
// Then `input_sat < payment_sat + needed_fee` compares against the
// wrapped (small) value -> check passes.
let stx = SignableTransaction::new(inputs, &payments, Some(change), None, 1)?;
// stx is signed; tx.output values are individually >= DUST and individually
// > MAX_MONEY / greater than inputs -> unbroadcastable, inputs burned.
```
With `payments` totaling `2^64 + 1000` sats, `payment_sat` wraps to `1000`, `payment_sat + needed_fee` stays below `input_sat`, the funding check passes, and `SignableTransaction::multisig` proceeds to sign the invalid spend.

Caveat: I could not fully trace whether the processor's plan-construction layer bounds payment totals against confirmed balances before reaching `SignableTransaction::new`; if it does with checked arithmetic, the attacker-controlled portion of this path is reduced and this degrades toward defense-in-depth rather than a reachable vulnerability.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-191)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-213)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L215-221)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```
