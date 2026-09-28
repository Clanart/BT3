### Title
Dust-sized change is silently converted to miner fees - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` drops a requested change output whenever its computed value is below `DUST`, but still constructs and signs the payment transaction. The unreturned change is consequently added to the miner fee instead of being returned to the wallet. [1](#0-0) 

### Finding Description
`SignableTransaction::new` accepts a requested change script and calculates `value = input_sat - payment_sat - fee_with_change`. [2](#0-1) 

If `value` is between `1` and `DUST - 1`, the function does not append the change output, does not revert, and does not otherwise signal that nonzero funds were left unallocated. [3](#0-2) 

The resulting transaction contains only the requested payment outputs and optional data output, while its spendable input value remains unchanged. [4](#0-3) 

Because `needed_fee` remains the fee calculated for the transaction without change, the actual signed transaction fee becomes `needed_fee + omitted_change`; this is reflected by `fee()`, which subtracts actual transaction outputs from the previous outputs. [5](#0-4) 

### Impact Explanation
An attacker who can influence the payment amount in a transaction proposed for threshold signing can choose that amount so the residual is nonzero but below `DUST`. [6](#0-5) 

The signing flow commits to the resulting transaction outputs through Taproot sighashes, so the threshold signature authorizes the omitted change and increased miner fee. [7](#0-6) 

Each affected transaction can burn up to 545 satoshis, and the condition can be repeated across transactions and inputs when an attacker can repeatedly shape proposed payment amounts. [8](#0-7) 

### Likelihood Explanation
The trigger only requires a payment amount satisfying `1 <= sum(inputs) - sum(payments) - fee_with_change < 546`; payment amounts and the presence of a change script are supplied to the public constructor. [6](#0-5) 

The constructor explicitly proceeds when the dust-sized change cannot be represented, so no malformed encoding, invalid signature, or protocol violation is required. [9](#0-8) 

### Recommendation
When `change` is `Some`, compute the residual using the transaction weight including change and reject the transaction if the residual is nonzero but below `DUST`. [1](#0-0) 

Alternatively, require callers to explicitly opt into donating dust-sized change to fees, and expose that donation as a distinct `TransactionError` or explicit transaction field rather than silently producing a signable transaction. [10](#0-9) 

### Proof of Concept
The following scenario demonstrates the condition using a hypothetical one-input transaction where the transaction with change has a 154-vbyte size and `fee_per_vbyte = 1`:

```rust
let input_value = 10_000;
let fee_with_change = 154;
let payment = input_value - fee_with_change - 300; // 9_546
let residual = input_value - payment - fee_with_change;

assert_eq!(residual, 300);
assert!(residual < DUST);
```

Calling `SignableTransaction::new` with that payment and `Some(change)` computes `value == 300`, skips the `value >= DUST` branch, and produces a transaction whose only payment output is 9,546 satoshis. [1](#0-0) 

The signed transaction then pays `10_000 - 9_546 = 454` satoshis as a fee even though the requested 1-sat/vbyte fee was only 154 satoshis for the change-bearing transaction. [5](#0-4)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L30-32)
```rust
// This doesn't bother with delineation due to how marginal these values are, and because it isn't
// worth the complexity to implement differentation
pub const DUST: u64 = 546;
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

**File:** networks/bitcoin/src/wallet/send.rs (L187-202)
```rust
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-255)
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
