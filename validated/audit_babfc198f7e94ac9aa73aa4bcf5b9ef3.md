### Title
Unchecked u64 arithmetic allows fee/funds accounting overflow in Bitcoin transaction construction - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` uses unchecked `u64` addition and multiplication when summing inputs, summing payments, calculating fees, and checking sufficient funds. In release builds these operations wrap, allowing attacker-controlled output values or fee parameters to bypass `NotEnoughFunds` and produce a transaction whose declared inputs, outputs, and fee are arithmetically inconsistent.

### Finding Description
Untrusted `ReceivedOutput` bytes can specify arbitrary `TxOut.value` values through `ReceivedOutput::read`, while `payments` and `fee_per_vbyte` are supplied as transaction-construction inputs. [1](#0-0)  The constructor then computes `input_sat`, `payment_sat`, and `needed_fee` using unchecked `sum`, `*`, and `+` operations. [2](#0-1) [3](#0-2) 

The same unchecked arithmetic is used when deciding whether to create change, and `fee()` later subtracts wrapped sums directly. [4](#0-3) [5](#0-4)  Once constructed, `TransactionSignMachine::sign` signs a Taproot sighash for each input of this malformed transaction. [6](#0-5) 

### Impact Explanation
An attacker who can feed crafted `ReceivedOutput` data or influence payment/fee parameters can cause the funds check to accept a transaction whose true mathematical output total exceeds its true input total. The resulting `SignableTransaction` is still eligible for `multisig`/`sign`, so participants can produce signature shares for a transaction and fee/change allocation that the constructor was supposed to reject. `fee()` can also report a wrapped, incorrect fee.

### Likelihood Explanation
Exploitation requires the attacker to control serialized `ReceivedOutput` values or transaction parameters passed into `SignableTransaction::new`. The affected APIs are directly exposed and the necessary wraparound can be triggered with ordinary-sized transaction structures; no malformed curve encodings, invalid participants, or protocol collusion are needed.

### Recommendation
Use checked arithmetic or `u128` accumulation for all input, payment, fee, and change calculations. Reject overflowing values before constructing `SignableTransaction`, and additionally bound each `TxOut.value` to Bitcoin's maximum money range where consensus validity is required.

### Proof of Concept
The following arithmetic demonstrates the wrapped funds check:

```rust
// networks/bitcoin/src/wallet/send.rs

// Two crafted ReceivedOutput values:
//   u64::MAX + 18001 == 18000 (mod 2^64)
let input_sat = u64::MAX.wrapping_add(18_001);

// 546 payment outputs of u64::MAX plus one output of 546:
//   546 * (u64::MAX) + 546 == 0 (mod 2^64)
let payment_sat = 0u64;

// For a transaction with vbytes == 18_000 and fee_per_vbyte == 1:
let needed_fee = 18_000u64;

// The real inequality is false, but the wrapped comparison passes:
assert!(input_sat >= payment_sat + needed_fee);
```

Here the wrapped `input_sat` equals `18000`, the wrapped payment total equals `0`, and the wrapped obligation equals `18000`, so `input_sat < payment_sat + needed_fee` is false even though the unwrapped payment total is `546 * 2^64`. The malformed transaction can then proceed to `multisig` and `TransactionSignMachine::sign`.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L122-133)
```rust
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-187)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-215)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L225-233)
```rust
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
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
