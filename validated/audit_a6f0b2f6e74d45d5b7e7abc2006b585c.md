### Title
Change outputs between 546 and 9,999 sats are created by `SignableTransaction` but never credited by the processor's scanner — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the SizeCredit finding — where a minimum-amount bound was enforced against the wrong semantic value (`params.amount` interpreted as credit even when it was cash under `exactAmountIn`) — Serai's Bitcoin wallet enforces its minimum-output ("dust") bound against a threshold that does not match the threshold at which outputs are actually recognized. `SignableTransaction::new` creates a change output whenever the leftover amount is `>= DUST` where `DUST = 546` [1](#0-0) [2](#0-1) , yet the processor's `Network` implementation defines `DUST = 10_000` and its scanner only registers outputs with `amount >= N::DUST` [3](#0-2) [4](#0-3) . The two constants apply the same check ("is this output worth tracking") to the same value (the on-chain output amount), but disagree on the bound — the same class of inconsistent-minimum validation as the report.

### Finding Description
`SignableTransaction::new` pushes a change `TxOut` whenever `input_sat - payment_sat - fee_with_change >= 546` [5](#0-4) . The processor consumes `bitcoin_serai::wallet::SignableTransaction` directly for its multisig sends [6](#0-5) . However, the processor's scanner drops any output below `N::DUST = 10_000` sats [7](#0-6) . Consequently, any transaction whose leftover lands in `[546, 10_000)` produces a real, signed, broadcast change output — spendable by the multisig's offset key — that the processor will never detect, never add to its scheduler inputs, and effectively burns. The wallet's comment even acknowledges the bound is a policy choice not tied to consensus [8](#0-7) , while the network-level constant was deliberately raised "by an order of magnitude" to 10,000 [9](#0-8)  without updating the wallet-side bound.

### Impact Explanation
Funds are sent on-chain but reported as not received / not spendable by the accounting layer: the change is committed into the transaction the threshold group signs (`Prevouts::All` commits to outputs implicitly through the tx being signed) [10](#0-9) , yet `Scanner::scan_transaction`/`get_outputs` never surface it. The sats are stranded at a multisig-controlled address until manual recovery. This is a concrete loss-of-funds-accounting issue, matching the accepted impact class "funds reported received that are not spendable" (here: funds actually received but never reported).

### Likelihood Explanation
The leftover amount is `inputs - payments - fee`. Payment amounts and input selection derive from user deposits and withdrawal requests, so an unprivileged depositor can pick deposit values such that, after scheduled payments, the residual falls inside the 9,454-sat-wide gap. No privileged access is needed — only control over transaction amounts sent to the multisig address, the same reachability as a user crafting `params.amount` in the original report.

### Recommendation
Align the two bounds: either raise `DUST` in `networks/bitcoin/src/wallet/send.rs` to 10,000 (or take it as a parameter supplied by the caller), or lower the scanner's threshold. The minimum check must be applied against the same semantic quantity — the value below which an output will never be creditable — at both creation and scan time.

### Proof of Concept
Conceptual: call `SignableTransaction::new(inputs, payments, Some(change_addr), None, fee)` such that `input_sat - payment_sat - fee_with_change` equals, e.g., 5,000 sats. The function returns `Ok` with a 5,000-sat change `TxOut` [2](#0-1) . After signing and broadcasting, the processor's scan loop evaluates `output.balance().amount.0 >= 10_000` as false and silently drops it [4](#0-3) . The 5,000 sats are stranded at the multisig change address with no `ReceivedOutput` ever emitted.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L28-32)
```rust
// https://github.com/bitcoin/bitcoin/blob/306ccd4927a2efe325c8d84be1bdb79edeb29b04/src/policy/policy.cpp#L26-L63
// As the above notes, a lower amount may not be considered dust if contained in a SegWit output
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-391)
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
```

**File:** processor/src/networks/bitcoin.rs (L25-28)
```rust
  wallet::{
    tweak_keys, p2tr_script_buf, ReceivedOutput, Scanner, TransactionError,
    SignableTransaction as BSignableTransaction, TransactionMachine,
  },
```

**File:** processor/src/networks/bitcoin.rs (L629-638)
```rust
    5000 sat/kilo-vbyte = 5 sat/vbyte
    5 * 57 = 285 sats/spent-output

    Even if an output took 100 bytes (it should be just ~29-43), taking 400 weight units, adding
    100 vbytes, tripling the transaction size, then the sats/tx would be < 1000.

    Increase by an order of magnitude, in order to ensure this is actually worth our time, and we
    get 10,000 satoshis.
  */
  const DUST: u64 = 10_000;
```

**File:** processor/src/multisigs/scanner.rs (L562-566)
```rust
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
```
