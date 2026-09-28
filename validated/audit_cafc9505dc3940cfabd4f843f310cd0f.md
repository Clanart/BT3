### Title
Unbounded fee-rate input can make threshold wallets sign excessive-fee transactions or abort spending - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
`SignableTransaction::new` accepts `fee_per_vbyte` without an upper bound and directly multiplies it by the transaction’s virtual size. It only enforces the Bitcoin relay minimum, not a protocol- or caller-defined maximum fee. A manipulated fee estimate can therefore cause the wallet to commit most or all available input value to miner fees, or reject otherwise spendable inputs.

### Finding Description
`fee_per_vbyte` is accepted as a constructor argument and converted into `needed_fee` by unchecked multiplication at `networks/bitcoin/src/wallet/send.rs:150-156` and `networks/bitcoin/src/wallet/send.rs:204-212`. [1](#0-0) [2](#0-1) 

The only fee validation is a lower-bound comparison against `DEFAULT_MIN_RELAY_TX_FEE`; there is no maximum fee, maximum fee rate, fee-to-payment ratio, or explicit caller confirmation of the resulting fee. [3](#0-2) 

If enough input value exists, the transaction is constructed and later threshold-signed; its signatures commit to the fee indirectly through the complete transaction digest using `TapSighashType::Default`. [4](#0-3) 

### Impact Explanation
This is the Serai analogue of a missing minimum-output/slippage bound: an economically hostile execution parameter is accepted without a protection limit. A public party capable of influencing the fee estimate supplied to transaction construction can cause the multisig to sign a transaction paying an excessive miner fee, permanently destroying wallet value, or can force repeated `NotEnoughFunds` failures and prevent withdrawals. [5](#0-4) 

Unlike a rejected malformed transaction, the resulting transaction is internally consistent and valid for broadcast. Once signed and mined, the excess fee is unrecoverable. [6](#0-5) 

### Likelihood Explanation
The vulnerable path is reached whenever transaction construction uses an attacker-influenced fee rate, such as a median/recent-block fee oracle populated by public transactions. The attacker does not need validator access, key material, malformed curve encodings, or control of the wallet API; they only need to influence the fee rate used for a transaction containing multisig inputs. [1](#0-0) 

The issue is worsened by the unchecked products `fee_per_vbyte * vbytes` and `fee_per_vbyte * vbytes_with_change`; sufficiently large values can overflow before returning an ordinary error. [7](#0-6) 

### Recommendation
Add an explicit `max_fee` or `max_fee_per_vbyte` bound, preferably derived from a bounded fee-estimation policy rather than one directly manipulable sample. Use checked arithmetic for fee and payment totals, reject fees above an absolute ceiling and above a configured fraction of spendable input value, and require callers to sign off on unusually high fees rather than silently incorporating them into the threshold-signed transaction.

### Proof of Concept
For a transaction with one 1,000,000-satoshi input, one 100,000-satoshi payment, a change script, and an estimated 200 virtual bytes:

```rust
SignableTransaction::new(
  inputs,
  &[(payment_script, 100_000)],
  Some(change_script),
  None,
  4_000, // manipulated fee estimate, sat/vbyte
)?;
```

The constructor computes an approximately `800_000`-satoshi fee because `4_000 * 200 = 800_000`. Since `1_000_000 >= 100_000 + 800_000`, the transaction is accepted rather than rejected as economically unreasonable. The resulting signed transaction commits about 80% of the input value to fees. A larger manipulated estimate instead produces `NotEnoughFunds` or an arithmetic overflow, preventing otherwise valid spends. [8](#0-7)

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L204-233)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L245-255)
```rust
    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
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
