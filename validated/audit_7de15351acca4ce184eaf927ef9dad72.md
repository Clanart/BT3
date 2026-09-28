### Title
Attacker-controlled UTXO set silently converts change into excess fee with no bound (fee/change dust math) - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the Beanstalk report — where an attacker manipulates on-chain state (deltaB) to force a penalty on a victim's convert with no user-set tolerance — `SignableTransaction::new` computes the transaction against an attacker-influenceable input set (`ReceivedOutput`s created by anyone who sends to the publicly derivable deposit script) and, when the post-fee remainder falls below `DUST` (or `checked_sub` fails), silently drops the change output so the entire remainder is burned as fee. The actual `fee()` can therefore exceed the quoted `needed_fee()` with no error and no maximum-tolerable-fee check.

### Finding Description
`Scanner::scan_transaction` accepts any output paying to a registered script_pubkey, regardless of value ( [1](#0-0) ). Since `p2tr_script_buf(key)` is publicly computable, an unprivileged party can create `ReceivedOutput`s of arbitrary value (including dust) that become `inputs` to `SignableTransaction::new` ( [2](#0-1) ).

In `new`, the no-change fee is computed first and only bounds the check `input_sat < payment_sat + needed_fee` ( [3](#0-2) ). Then, if change exists, it is only emitted when `input_sat - (payment_sat + fee_with_change) >= DUST`; otherwise the change output is silently omitted while `needed_fee` stays at the smaller no-change value ( [4](#0-3) ). The signed transaction then pays `fee() = input_sat - sum(outputs)`, which can exceed `needed_fee()` — exactly the "penalty exceeds what was quoted, no tolerance bound" shape ( [5](#0-4) ).

By dusting the vault/deposit script with small outputs, an attacker shifts `input_sat` relative to `payment_sat` and `fee_with_change`, steering a victim spend into the branch where change is dropped and the leftover (plus the extra per-input fee the attacker's dust inputs added) is paid to miners.

### Impact Explanation
The signer commits — via `Prevouts::All` and `TapSighashType::Default` — to a transaction whose actual fee exceeds the `needed_fee()` the plan was approved against ( [6](#0-5) ). Funds up to just under `DUST` plus the incremental fee weight of each injected dust input are burned to miners per transaction, and repeated dusting forces this on every spend that lands near the boundary. This is "penalty applied due to attacker-influenced state with no user tolerance," matching the report's class.

### Likelihood Explanation
Sending outputs to a known P2TR script requires only a normal Bitcoin transaction — fully unprivileged. Whether a given spend lands in the drop-change branch depends on the victim's payment amounts, so each instance is opportunistic and bounded (~sub-`DUST` remainder plus injected-input fee inflation). Medium-low likelihood, bounded per-instance loss → Medium/Low-Medium severity.

### Recommendation
As in the report's fix (`maximumTolerablePenalty`), add a caller-specified bound: after computing the final output set, require `self.fee() <= needed_fee + max_additional_fee` (or simply reject when the change would be below `DUST`), and/or filter out `ReceivedOutput`s whose value is below the marginal fee cost of spending them before constructing the transaction.

### Proof of Concept
1. Compute `p2tr_script_buf(vault_key)` and send a transaction creating outputs of small value to that script.
2. `Scanner::scan_transaction` returns them as `ReceivedOutput`s; the spend plan includes them as inputs.
3. Choose/await a payment amount such that `0 < input_sat - payment_sat - fee_with_change < DUST` (or `input_sat < payment_sat + fee_with_change` while `input_sat >= payment_sat + needed_fee`).
4. `SignableTransaction::new` succeeds, omits the change output, and `tx.fee()` = `input_sat - payment_sat` > `needed_fee()`; `TransactionSignMachine::sign` signs a sighash committing to that excess fee with no error or tolerance check ( [4](#0-3) ).

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
```

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
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

**File:** networks/bitcoin/src/wallet/send.rs (L204-221)
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
