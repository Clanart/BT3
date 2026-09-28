### Title
Unchecked `u64` overflow in payment/fee arithmetic causes panic or produces unspendable transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report describes arithmetic underflow in cost/return calculations that should saturate to zero but instead revert. The direct analog exists in `SignableTransaction::new`: payment amounts and fees are summed and added in plain `u64` arithmetic without overflow checks, so attacker-chosen payment amounts overflow the `input_sat < payment_sat + needed_fee` guard — panicking in debug builds and, in release builds, wrapping to produce a `SignableTransaction` whose outputs are consensus-invalid and whose `fee()` getter itself underflows.

### Finding Description
`SignableTransaction::new` computes `payment_sat` as an unchecked `sum::<u64>()` over caller-supplied payment amounts [1](#0-0) . The solvency check adds `needed_fee` (itself an unchecked `fee_per_vbyte * vbytes` product) to that sum [2](#0-1) . If `payment_sat + needed_fee` exceeds `u64::MAX`, the addition panics in debug builds and wraps in release builds. A wrapped sum becomes a small number, so the `NotEnoughFunds` guard is bypassed even though the payments vastly exceed the inputs. The change calculation repeats the same unchecked addition, protected only on the subtraction side by `checked_sub` [3](#0-2) , so a wrapped `payment_sat + fee_with_change` yields a change output valued near the full `input_sat`. The resulting `Transaction` is pushed to the FROST `TransactionMachine` and signed [4](#0-3) , while `fee()` performs another unchecked `sum(inputs) - sum(outputs)` subtraction that underflows once output sums wrap [5](#0-4) . `payments` is a public parameter of `SignableTransaction::new` [6](#0-5) , so any caller feeding untrusted withdrawal amounts reaches this path.

### Impact Explanation
Two reachable outcomes: (1) a panic crashing the transaction-construction path (denial of service of signing), and (2) in release builds, construction and threshold-signing of a transaction whose outputs contain `u64::MAX`-valued amounts — consensus-invalid, so the change "received" by the protocol change address is not spendable, while the inputs are still consumed by the signed (but unbroadcastable/rejected) transaction attempt. This matches the "funds reported received that are not spendable" / crash-on-overflow acceptance criteria; severity Medium.

### Likelihood Explanation
Triggering requires payment amounts whose sum (with fee) exceeds `u64::MAX`, i.e. amounts far above the real Bitcoin supply. Whether upstream balance checks bound amounts to actual holdings determines reachability; the function itself performs no cap and is the canonical place where oversized payments should be rejected with `NotEnoughFunds` — instead the guard is the overflow site. Conditional on such amounts reaching the API, the overflow is deterministic.

### Recommendation
Use checked arithmetic throughout: compute `payment_sat` with `checked_add`/`try_fold`, compute `needed_fee`/`fee_with_change` with `checked_mul`, and rewrite the guard as `input_sat.checked_sub(payment_sat).and_then(|r| r.checked_sub(needed_fee))` returning `NotEnoughFunds` on failure. Apply the same pattern in `fee()`.

### Proof of Concept
```rust
// payments: three outputs whose summed value wraps u64 to 0
let payments = vec![
  (addr(), u64::MAX),
  (addr(), u64::MAX),
  (addr(), 2),      // >= DUST? adjust to DUST so the dust check passes
];
// payment_sat wraps to ~0; `input_sat < payment_sat + needed_fee` is false,
// the NotEnoughFunds error is skipped, checked_sub sees a tiny wrapped sum,
// and a change output ~= input_sat is appended. fee() then underflows.
```
With `payments = [(a, u64::MAX), (b, u64::MAX), (c, DUST)]`, `sum::<u64>()` wraps to `DUST - 2`; the funds check passes, `tx_outs` holds two `u64::MAX` outputs, and `SignableTransaction::fee()` panics on `sum(inputs) - sum(outputs)` — or, if each wrapped intermediate stays small, yields a signed transaction unspendable on-chain.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-156)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-187)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-215)
```rust
    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
```

**File:** networks/bitcoin/src/wallet/send.rs (L227-234)
```rust
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
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
