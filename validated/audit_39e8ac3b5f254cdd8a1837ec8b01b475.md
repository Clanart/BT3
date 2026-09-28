### Title
Inconsistent dust thresholds cause change outputs between 546–9,999 sats to be created on-chain but ignored by the scanner, permanently burning funds - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The original bug is an inconsistency in dust handling between two code paths: primary debt dust is truncated to zero in `updateAccountDebt`, but secondary debt dust is not truncated in `_updateTotalSecondaryDebt`, causing accounting discrepancies. The same bug class exists in Serai's Bitcoin wallet: `SignableTransaction::new` creates a change output whenever the leftover is `>= 546` sats, while the processor's scanner and scheduler treat anything below `Bitcoin::DUST = 10_000` sats as non-existent. A change output valued in `[546, 10_000)` is therefore published on-chain yet never credited or spent by the protocol.

### Finding Description
In `SignableTransaction::new`, payments are rejected if below `DUST = 546`, and the change output is appended whenever `input_sat - (payment_sat + fee_with_change) >= DUST` (i.e. ≥ 546 sats) — there is no truncation/normalization against the protocol-level dust floor [1](#0-0) . The constant is hardcoded at 546 and explicitly does not delineate per-script-type [2](#0-1) .

Meanwhile, the processor defines `Bitcoin::DUST = 10_000`, justified by requiring outputs remain economically spendable under a 5,000 sat/kvB fee rate [3](#0-2) . The scanner drops every scanned output below that threshold before registration [4](#0-3) , and the scheduler drops payments below it after fee amortization [5](#0-4) . The accounting path in `prepare_send` also assumes any on-chain change under `Self::DUST` (10,000) is *not* created, adding `theoretical_change_amount` to `operating_costs` in that case [6](#0-5)  — but `make_signable_transaction` calls the wallet constructor with the 546-sat floor, so the change *is* created [7](#0-6) .

The result mirrors the audit finding exactly: one accounting path applies a truncation/normalization rule (drop below 10,000) while the sibling path applies a different, lower bound (create at ≥ 546), producing an inconsistent view of the same funds.

### Impact Explanation
A change output of 546–9,999 sats is (a) spent into existence by a signed Serai transaction, (b) silently filtered out by the scanner, so its value is never credited back to the multisig's tracked UTXO set, and (c) simultaneously booked as `operating_costs`, i.e. amortized as an extra fee onto subsequent user payments. This is a double loss: the sats are stranded in an output the protocol never tracks (effectively burned, since Serai will never select it as an input), and users are charged an operating cost for a change output that actually exists. While individual amounts are bounded by ~10k sats per transaction, the discrepancy is repeatable across arbitrarily many transactions and directly contradicts the invariant that `DUST` "MUST exceed the cost to spend said output" consistently across creation and scanning [8](#0-7) .

### Likelihood Explanation
Change amounts are determined by `sum(inputs) - sum(payments) - fee`, which is influenced by user-initiated payment amounts and scanned input values — public inputs an unprivileged user controls via ordinary burns/deposits. Landing a change value in a ~9.4k sat window is straightforward whenever the scheduler's plan produces a change address. No validator collusion or node compromise is required; the loss is triggered purely by the value the protocol's own transaction-building code computes.

### Recommendation
Use a single dust constant. Either pass the network-level `N::DUST` into `SignableTransaction::new` for the change check (the processor already knows the economically spendable floor of 10,000), or at minimum replace the hardcoded `546` in the change-output branch so that change below the scanner's registration threshold is folded into the fee instead of being created and orphaned. Align `prepare_send`'s `on_chain_expected_change < Self::DUST` accounting with whatever floor the transaction builder actually enforces [9](#0-8) .

### Proof of Concept
1. A plan is scheduled with `change = Some(serai_controlled_address)` and inputs/payments such that `sum(inputs) - sum(payments) - fee_with_change = 5_000` sats.
2. `make_signable_transaction` → `BSignableTransaction::new`: `value = 5_000 >= DUST (546)`, so a 5,000-sat change output is appended to `tx_outs` and the transaction is signed and broadcast [10](#0-9) .
3. `prepare_send` computes `on_chain_expected_change = 5_000 < Self::DUST (10_000)` and executes `operating_costs += theoretical_change_amount`, charging users for the change as if it were burned [6](#0-5) .
4. The scanner observes the confirmed transaction's change output, evaluates `output.balance().amount.0 (5_000) >= N::DUST (10_000)` as false, and drops it — the 5,000 sats are never registered as a spendable output [4](#0-3) .
5. Net effect: the multisig paid real sats into an output it will never spend or credit, while also amortizing the same amount onto users as an operating cost.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L30-32)
```rust
// This doesn't bother with delineation due to how marginal these values are, and because it isn't
// worth the complexity to implement differentation
pub const DUST: u64 = 546;
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-235)
```rust
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

**File:** processor/src/networks/bitcoin.rs (L446-452)
```rust
    match BSignableTransaction::new(
      inputs.iter().map(|input| input.output.clone()).collect(),
      &payments,
      change.clone().map(Into::into),
      None,
      fee.0,
    ) {
```

**File:** processor/src/networks/bitcoin.rs (L622-638)
```rust
    Bitcoin defines multiple minimum feerate constants *per kilo-vbyte*. Currently, these are:
    - 1000 sat/kilo-vbyte for a transaction to be relayed
    - Each output's value must exceed the fee of the TX spending it at 3000 sat/kilo-vbyte
    The DUST constant needs to be determined by the latter.
    Since these are solely relay rules, and may be raised, we require all outputs be spendable
    under a 5000 sat/kilo-vbyte fee rate.

    5000 sat/kilo-vbyte = 5 sat/vbyte
    5 * 57 = 285 sats/spent-output

    Even if an output took 100 bytes (it should be just ~29-43), taking 400 weight units, adding
    100 vbytes, tripling the transaction size, then the sats/tx would be < 1000.

    Increase by an order of magnitude, in order to ensure this is actually worth our time, and we
    get 10,000 satoshis.
  */
  const DUST: u64 = 10_000;
```

**File:** processor/src/multisigs/scanner.rs (L564-566)
```rust
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
```

**File:** processor/src/multisigs/scheduler/utxo.rs (L507-511)
```rust
    // Drop payments now below the dust threshold
    let payments = payments
      .into_iter()
      .filter(|payment| payment.balance.amount.0 >= N::DUST)
      .collect::<Vec<_>>();
```

**File:** processor/src/networks/mod.rs (L295-301)
```rust
  /// Minimum output value which will be handled.
  ///
  /// For any received output, there's the cost to spend the output. This value MUST exceed the
  /// cost to spend said output, and should by a notable margin (not just 2x, yet an order of
  /// magnitude).
  // TODO: Dust needs to be diversified per ExternalCoin
  const DUST: u64;
```

**File:** processor/src/networks/mod.rs (L579-591)
```rust
    if change.is_some() {
      let on_chain_expected_change =
        inputs.iter().map(|input| input.balance().amount.0).sum::<u64>() -
          payments.iter().map(|payment| payment.balance.amount.0).sum::<u64>() -
          tx_fee;
      // If the change value is less than the dust threshold, it becomes an operating cost
      // This may be slightly inaccurate as dropping payments may reduce the fee, raising the
      // change above dust
      // That's fine since it'd have to be in a very precarious state AND then it's over-eager in
      // tabulating costs
      if on_chain_expected_change < Self::DUST {
        operating_costs += theoretical_change_amount;
      }
```
