### Title
`SignableTransaction::new` omits the `OP_RETURN` data output from fee estimation, producing transactions which may pay below the minimum relay fee and be unbroadcastable — ([File: networks/bitcoin/src/wallet/send.rs](https://github.com/Annirich/serai--021/blob/main/networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the reference bug (funds sent into a contract that cannot receive them → funds stuck), `SignableTransaction::new` computes the transaction's required fee and the minimum-relay-fee check over a transaction skeleton that excludes the `OP_RETURN` data output. When `data` is supplied, the actually-signed transaction is larger than estimated, so it pays less than the requested fee rate and can fall below `DEFAULT_MIN_RELAY_TX_FEE`, rendering the fully-signed transaction unbroadcastable and the consumed `ReceivedOutput`s unmovable. [1](#0-0) 

### Finding Description
In `SignableTransaction::new`, the `OP_RETURN` output carrying `data` is pushed to `tx_outs` at lines 194–202, *before* the fee is estimated. However, `calculate_weight_vbytes` is invoked with only `payments` (line 204) — the data output is never part of the weight/vbyte estimate, and the same omission applies to the change-output recalculation at lines 224–227 (`calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`). [2](#0-1) 

Consequences:

1. `needed_fee = fee_per_vbyte * vbytes` undercharges by the full serialized size of the `OP_RETURN` output (≈ 94 extra vbytes for an 80-byte payload: `8` value + `1` length + `~83` script, times 4 weight units / 4). [3](#0-2) 
2. The `TooLowFee` guard at line 211 compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes` using the *underestimated* `vbytes`. A caller picking a fee rate that just passes this check produces a real transaction whose effective fee rate is below the node minimum relay fee. [4](#0-3) 
3. Because `fee()` is defined as `sum(prevouts) - sum(outputs)` (lines 138–141), there is no post-construction correction — the transaction is signed (via `TransactionSignMachine::sign`, which commits to `self.tx.output` through the BIP-341 sighash with `Prevouts::All`) with the deficient fee baked in. [5](#0-4) 

### Impact Explanation
Any caller that attaches `data` (an `OP_RETURN` payload, a publicly-specifiable field of the transaction) receives a `SignableTransaction` that, once signed through `TransactionMachine`, either pays a fee rate strictly lower than requested or — at the boundary — fails the mempool's minimum relay fee check and cannot be broadcast at all. The inputs (`ReceivedOutput`s) committed to the sighash are then locked to a transaction the network rejects: like the reference issue where ETH sent to `QVBaseStrategy` cannot be received/spent, funds here are allocated to a spend that cannot confirm. The signature commits to the exact output set and prevouts, so the underpriced transaction cannot be amended without re-running the threshold signing protocol. [6](#0-5) 

### Likelihood Explanation
The `data` field is attacker-influenceable transaction data (arbitrary caller-supplied bytes up to 80, gated only by `TooMuchData` at line 171), and the fee miscalculation is deterministic whenever `data.is_some()` — no race or special chain state is required. Triggering the outright unbroadcastable case additionally requires the chosen `fee_per_vbyte` to land the true fee rate under the relay minimum, which is a plausible configuration rather than an adversarial edge case; the guaranteed outcome in all cases is a signed transaction paying less than the specified rate. [7](#0-6) 

### Recommendation
Include the `OP_RETURN` data output in the weight/vbyte estimation. Either extend `calculate_weight_vbytes` to accept the data output (or the fully-built `tx_outs` list) so the estimate reflects the real transaction, or move the `tx_outs.push` for the data output after constructing the estimate transaction directly from `tx_outs`. Re-run the estimate when the change output is added so both code paths account for every output. [8](#0-7) 

### Proof of Concept
```rust
// networks/bitcoin context; conceptually, inside SignableTransaction::new:
// payments = [(script, 10_000)], data = Some(vec![0u8; 80]), change = None,
// fee_per_vbyte chosen so that needed_fee == DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000
// exactly (boundary of the TooLowFee check).

// Line 195-202: tx_outs = [payment, OP_RETURN(80 bytes)]  -- ~94 vbytes of output
// Line 204:     vbytes estimated over [payment] only      -- OP_RETURN excluded
// Line 211:     needed_fee == min_relay * vbytes_est / 1000  -> passes TooLowFee
// Result: actual tx is ~94 vbytes larger; actual feerate < 1 sat/vbyte.
// TransactionSignMachine signs sighashes committing to all prevouts/outputs;
// the signed tx is rejected by send_raw_transaction as below min relay fee.
// The ReceivedOutputs consumed as inputs are locked to an unbroadcastable TX.
```

Root cause confirmation: the estimate at line 204 uses `payments` (which excludes `data`), while the final `tx.output` includes the `OP_RETURN` pushed at lines 194–202, and `needed_fee`/`TooLowFee` are both computed from the underestimated `vbytes`. [9](#0-8)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L62-99)
```rust
  fn calculate_weight_vbytes(
    inputs: usize,
    payments: &[(ScriptBuf, u64)],
    change: Option<&ScriptBuf>,
  ) -> (u64, u64) {
    // Expand this a full transaction in order to use the bitcoin library's weight function
    let mut tx = Transaction {
      version: Version(2),
      lock_time: LockTime::ZERO,
      input: vec![
        TxIn {
          // This is a fixed size
          // See https://developer.bitcoin.org/reference/transactions.html#raw-transaction-format
          previous_output: OutPoint::default(),
          // This is empty for a Taproot spend
          script_sig: ScriptBuf::new(),
          // This is fixed size, yet we do use Sequence::MAX
          sequence: Sequence::MAX,
          // Our witnesses contains a single 64-byte signature
          witness: Witness::from_slice(&[vec![0; 64]])
        };
        inputs
      ],
      output: payments
        .iter()
        // The payment is a fixed size so we don't have to use it here
        // The script pub key is not of a fixed size and does have to be used here
        .map(|payment| TxOut {
          value: Amount::from_sat(payment.1),
          script_pubkey: payment.0.clone(),
        })
        .collect(),
    };
    if let Some(change) = change {
      // Use a 0 value since we're currently unsure what the change amount will be, and since
      // the value is fixed size (so any value could be used here)
      tx.output.push(TxOut { value: Amount::ZERO, script_pubkey: change.clone() });
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-173)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L194-234)
```rust
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
