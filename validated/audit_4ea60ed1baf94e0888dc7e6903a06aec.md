### Title
Unchecked `u64` payment arithmetic allows a malformed Bitcoin transaction to be signed - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` calculates the aggregate input amount, aggregate payment amount, and required fee using unchecked `u64` addition and multiplication. An unprivileged party that can request transaction data to be signed can make `payment_sat + needed_fee` wrap below the available input total, causing malformed outputs to pass the balance check and subsequently be incorporated into a Taproot transaction that Serai signs. [1](#0-0) 

### Finding Description
The public constructor accepts caller-provided `payments`, sums their `u64` amounts with `sum::<u64>()`, and then performs `payment_sat + needed_fee` without `checked_add`, `checked_mul`, or a Bitcoin maximum-money bound. In a release build, the expressions wrap modulo `2^64`; in a debug build, they panic. [2](#0-1) 

For example, payments of `u64::MAX` and `546` satoshis both pass the per-payment dust check, but their wrapped total is only `545` satoshis. A modest fee therefore leaves `input_sat < payment_sat + needed_fee` false, even though the actual encoded transaction contains an astronomically large, consensus-invalid output. [3](#0-2) 

After construction succeeds, `multisig` creates one FROST machine per input, `sign` derives each Taproot sighash from the malformed transaction, and `complete` inserts valid Schnorr signatures into every input witness. [4](#0-3) [5](#0-4) [6](#0-5) 

### Impact Explanation
This can cause Serai to produce a valid threshold signature over attacker-chosen transaction bytes that should have been rejected by the wallet's own insufficient-funds logic. The resulting transaction is consensus-invalid because a `TxOut` can encode more than Bitcoin's maximum supply, but the signer has still signed an unintended message and emitted a transaction whose semantics differ from the checked totals. [7](#0-6) 

The same unchecked arithmetic also makes `needed_fee`, `fee()`, and change calculation depend on wrapped values, allowing incorrectly computed fees and balances to be represented as successful construction. [8](#0-7) [9](#0-8) 

### Likelihood Explanation
The vulnerable path is reachable through public transaction-construction inputs: the attacker only needs to cause a signing flow to include crafted payment amounts; they do not need a key share, validator access, malformed curve encoding, or leaked secret. [10](#0-9) 

Triggering the balance-check bypass requires only two payment entries, while producing a fully signed transaction additionally requires the normal threshold signing set to participate. The impact is bounded by Bitcoin consensus rejecting impossible output values, so this is a Medium-severity signing-integrity analog rather than a direct remote code execution issue. [3](#0-2) [11](#0-10) 

### Recommendation
Use checked arithmetic for all monetary calculations:

- Accumulate inputs and payments with `checked_add` or `try_fold`.
- Calculate `fee_per_vbyte * vbytes` with `checked_mul`.
- Calculate `payment_sat + needed_fee` with `checked_add`.
- Reject each payment and each scanned input above Bitcoin's maximum money supply.
- Make `fee()` return `Result<u64, TransactionError>` instead of subtracting potentially wrapped totals.

These checks should occur before any output is inserted into `tx_outs` and before the transaction is exposed to `multisig`, `sign`, or `complete`. [12](#0-11) 

### Proof of Concept
Conceptually, with one valid `ReceivedOutput` worth `1_000` sats and a normal positive fee rate, the following release-build flow succeeds:

```rust
let payments = vec![
    (attacker_script_a.clone(), u64::MAX),
    (attacker_script_b.clone(), 546),
];

// input_sat = 1_000
// payment_sat = u64::MAX + 546 = 545 (mod 2^64)
// payment_sat + needed_fee remains below 1_000 for typical fees.
let signable = SignableTransaction::new(
    vec![valid_received_output],
    &payments,
    None,
    None,
    fee_per_vbyte,
)?;

let machine = signable.multisig(&keys).unwrap();
// Threshold signing then signs both attacker-controlled malformed outputs.
```

In a debug build the same values panic at the overflow; in a release build the wrapped total bypasses `NotEnoughFunds`, demonstrates the integer-overflow root cause, and reaches the transaction-signing path. [13](#0-12) [4](#0-3) [14](#0-13)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L132-140)
```rust
  /// The actual fee this transaction will use is `sum(inputs) - sum(outputs)`.
  pub fn needed_fee(&self) -> u64 {
    self.needed_fee
  }

  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-255)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

    if payments.is_empty() && change.is_none() && data.is_none() {
      Err(TransactionError::NoOutputs)?;
    }

    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }

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

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
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
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L355-397)
```rust
  fn sign(
    mut self,
    commitments: HashMap<Participant, Self::Preprocess>,
    msg: &[u8],
  ) -> Result<(TransactionSignatureMachine, Self::SignatureShare), FrostError> {
    if !msg.is_empty() {
      panic!("message was passed to the TransactionSignMachine when it generates its own");
    }

    let commitments = (0 .. self.sigs.len())
      .map(|c| {
        commitments
          .iter()
          .map(|(l, commitments)| (*l, commitments[c].clone()))
          .collect::<HashMap<_, _>>()
      })
      .collect::<Vec<_>>();

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

**File:** networks/bitcoin/src/wallet/send.rs (L413-427)
```rust
  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
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
