### Title
Signable transactions can commit to an unbounded fee when residual input value is not returned - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
`SignableTransaction::new` accepts a fee rate but no maximum absolute fee. If no change output is supplied, or the calculated change is below `DUST`, all remaining input value is silently converted into the transaction fee. The resulting transaction is subsequently signed without any check that its actual fee is within a caller-specified bound. [1](#0-0) [2](#0-1) 

### Finding Description
`ReceivedOutput::read` accepts externally supplied `offset`, `TxOut`, and `OutPoint` fields and constructs an object described as a spendable output. [3](#0-2) 

`SignableTransaction::new` derives `input_sat` from those supplied outputs, derives `needed_fee` only as `fee_per_vbyte * vbytes`, and rejects transactions whose fee is too low or whose inputs cannot cover the intended fee. [4](#0-3) 

However, when `change` is `None`, no output is added to collect the surplus. When `change` is `Some`, the output is added only if the residual is at least `DUST`; otherwise, the residual is also left as additional fee. [2](#0-1) 

The actual fee is explicitly `sum(inputs) - sum(outputs)`, which can therefore be arbitrarily larger than `needed_fee`. [5](#0-4) 

Before signing, `multisig` verifies only that each provided offset makes the threshold key derive the script in the supplied prevout; it does not validate the actual fee or reject an excessive fee. [6](#0-5) 

`TransactionSignMachine::sign` then signs each input’s Taproot sighash over the already-constructed transaction and `Prevouts::All`, committing the threshold signature to the excessive fee. [7](#0-6) 

### Impact Explanation
A caller can authorize a target fee rate while unknowingly signing a transaction with a materially larger absolute fee. Any surplus input value not captured by a payment, data output, or accepted change output is permanently paid to miners. [5](#0-4) [2](#0-1) 

This is a direct analog of the missing slippage-bound bug class: `fee_per_vbyte` bounds only the intended rate, while the economically important derived value—the absolute amount burned as fee—has no caller-specified upper bound. [8](#0-7) 

### Likelihood Explanation
The condition is reachable whenever an input set contains more value than the payments plus the intended fee and the caller either omits change or produces a change residual below `DUST`. [9](#0-8) 

Because `ReceivedOutput::read` accepts untrusted transaction data and `SignableTransaction::new` consumes its amount directly, an integrator accepting serialized outputs must independently inspect `fee()` before signing to avoid authorizing an excessive fee. [3](#0-2) [10](#0-9) 

### Recommendation
Add a `max_fee: u64` or equivalent slippage parameter to `SignableTransaction::new`, and reject construction when `self.fee()` would exceed that bound. [11](#0-10) 

The check should occur after the final output set is selected, so it covers both the no-change path and the path where a below-`DUST` residual is not returned as change. [12](#0-11) 

### Proof of Concept
For a group-owned input worth `100_000_000` sats, one payment worth `1_000` sats, and no change output, construction succeeds even though the fee is approximately `99_999_000` sats rather than `20 * vbytes`:

```rust
// networks/bitcoin/src/wallet/send.rs
let tx = SignableTransaction::new(
  vec![received_output],             // 100_000_000 sats controlled by the threshold key
  &[(payment_script, 1_000)],
  None,                              // no change output
  None,
  20,                                // intended fee rate only
).unwrap();

assert!(tx.fee() > tx.needed_fee());
assert_eq!(tx.fee(), 100_000_000 - 1_000);

// The only key-related check is that the offset derives the prevout script.
let machine = tx.multisig(&threshold_keys).unwrap();
```

The resulting signing flow then commits to the transaction’s sighashes and embeds the produced signatures as witnesses, leaving no final-stage rejection for the excessive fee. [7](#0-6) [13](#0-12)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L137-156)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }

  /// Create a new SignableTransaction.
  ///
  /// If a change address is specified, any leftover funds will be sent to it if the leftover funds
  /// exceed the minimum output amount. If a change address isn't specified, all leftover funds
  /// will become part of the paid fee.
  ///
  /// If data is specified, an OP_RETURN output will be added with it.
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-221)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-245)
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

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }

    Ok(SignableTransaction {
```

**File:** networks/bitcoin/src/wallet/send.rs (L270-282)
```rust
  /// Create a multisig machine for this transaction.
  ///
  /// Returns None if the wrong keys are used.
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
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

**File:** networks/bitcoin/src/wallet/send.rs (L417-427)
```rust
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }

    Ok(self.tx)
```

**File:** networks/bitcoin/src/wallet/mod.rs (L120-134)
```rust
  /// Read a ReceivedOutput from a generic satisfying Read.
  #[cfg(feature = "std")]
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
  }
```
