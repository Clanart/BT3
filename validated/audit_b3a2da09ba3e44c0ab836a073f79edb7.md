### Title
Fee arithmetic integer overflow can produce a zero-fee Bitcoin transaction - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` calculates `needed_fee` with unchecked `u64` multiplication and later adds it to `payment_sat` using unchecked addition. [1](#0-0)  A public transaction-construction input can select a `fee_per_vbyte` large enough that the multiplication wraps in a release build, after which the addition can also wrap below the available input amount. [2](#0-1) 

### Finding Description
The vulnerability is the same integer-overflow class as CVE-2022-42265, applied to transaction-fee arithmetic rather than the NVIDIA kernel driver. `calculate_weight_vbytes` returns `vbytes`, `needed_fee` is assigned `fee_per_vbyte * vbytes`, and the solvency test compares `input_sat` with `payment_sat + needed_fee`. [1](#0-0) 

For an input paying exactly 546 satoshis to a 546-satoshi payment, let the calculated transaction size be `vbytes`, which is below 546 for a normal transaction. Choosing `fee_per_vbyte = u64::MAX` makes `needed_fee` equal `2^64 - vbytes` modulo `2^64`. The subsequent expression `payment_sat + needed_fee` then equals `546 - vbytes`, which is less than `input_sat`, so the insufficient-funds check passes even though the transaction has one 546-satoshi input and one 546-satoshi output. [3](#0-2) 

The resulting `SignableTransaction` records a wrapped `needed_fee`, while its real fee is zero because `fee()` subtracts the output total from the input total. [4](#0-3)  The transaction can then proceed through `multisig`, `preprocess`, `sign`, and `complete`, producing witness signatures for that transaction. [5](#0-4) 

### Impact Explanation
An attacker who supplies transaction-construction parameters can cause a signer to authorize a transaction whose fee and change calculations do not reflect the intended fee policy. The resulting signed Bitcoin transaction can have an actual fee substantially lower than requested—including zero—despite `needed_fee()` reporting a wrapped, extremely large value. [6](#0-5) 

This is a concrete signing of an unintended message because every Taproot input is signed against the transaction constructed with corrupted fee arithmetic through `taproot_key_spend_signature_hash`. [7](#0-6)  Depending on the selected values and whether change is present, the same issue can also create an unexpected change amount by overflowing `payment_sat + fee_with_change` before `checked_sub` is evaluated. [8](#0-7) 

### Likelihood Explanation
The arithmetic uses primitive `u64` operations without `checked_mul`, `checked_add`, or equivalent bounds checks. [1](#0-0)  `fee_per_vbyte` is an external constructor parameter, and no upper bound is enforced before multiplication. [9](#0-8) 

A low-fee or zero-fee transaction may be rejected by relay policy, but the signing result itself is still unintended and the API reports an internally inconsistent fee. In builds with overflow checks enabled, the same inputs panic, while optimized builds without overflow checks perform wrapping arithmetic.

### Recommendation
Use checked arithmetic for all fee and amount calculations in `SignableTransaction::new`:

```rust
let needed_fee = fee_per_vbyte
  .checked_mul(vbytes)
  .ok_or(TransactionError::FeeOverflow)?;

let required = payment_sat
  .checked_add(needed_fee)
  .ok_or(TransactionError::AmountOverflow)?;

if input_sat < required {
  return Err(TransactionError::NotEnoughFunds {
    inputs: input_sat,
    payments: payment_sat,
    fee: needed_fee,
  });
}
```

Apply the same pattern to `fee_with_change`, the change calculation, and `SignableTransaction::fee`, which currently subtracts sums without checking underflow. [4](#0-3) [8](#0-7) 

### Proof of Concept
The conceptual trigger is:

```rust
let input = 546u64;
let payment = 546u64;
let vbytes = 112u64; // representative one-input/one-output Taproot vsize
let fee_per_vbyte = u64::MAX;

let needed_fee = fee_per_vbyte.wrapping_mul(vbytes);
let required = payment.wrapping_add(needed_fee);

assert!(needed_fee > u64::MAX - 1_000);
assert!(required < input);
```

Under the same values, `needed_fee` becomes `2^64 - 112`, `required` becomes `434`, and the check at `input_sat < payment_sat + needed_fee` passes. [1](#0-0)  The constructed transaction nevertheless contains only a 546-satoshi input and a 546-satoshi payment output, so `fee()` reports zero. [4](#0-3)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L129-141)
```rust
  /// Returns the fee necessary for this transaction to achieve the fee rate specified at
  /// construction.
  ///
  /// The actual fee this transaction will use is `sum(inputs) - sum(outputs)`.
  pub fn needed_fee(&self) -> u64 {
    self.needed_fee
  }

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

**File:** networks/bitcoin/src/wallet/send.rs (L223-234)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
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
