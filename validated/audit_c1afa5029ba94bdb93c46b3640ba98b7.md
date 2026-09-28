### Title
OP_RETURN data is excluded from Bitcoin transaction fee and weight accounting - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` accepts up to 80 bytes of caller-controlled OP_RETURN data but calculates transaction weight, virtual size, minimum relay fee, and change as though that output does not exist. This is analogous to charging for a fixed or underestimated resource cost while producing a larger transaction: the signed transaction is charged less than the requested fee rate and can even fall below Bitcoin’s minimum relay fee.

### Finding Description
`SignableTransaction::new` appends an OP_RETURN output containing caller-provided `data` to `tx_outs` when `data` is present. [1](#0-0) 

Both calls to `calculate_weight_vbytes` pass only `payments` and optional `change`; neither representation contains the OP_RETURN output. [2](#0-1) [3](#0-2) 

The minimum-relay validation also uses the underestimated `vbytes`, so a transaction whose real serialized size exceeds the minimum-fee requirement can pass validation. [4](#0-3) 

The function then returns a transaction containing the previously omitted OP_RETURN output, while `needed_fee`, change, and the weight limit are based on the smaller transaction. [5](#0-4) 

The reported fee is the actual input-minus-output difference, meaning the missing OP_RETURN is not implicitly corrected during signing. [6](#0-5) 

### Impact Explanation
A caller who can cause data-bearing transactions to be created receives up to approximately 91 virtual bytes of transaction capacity without paying the configured `fee_per_vbyte` for it. At higher configured fee rates, this undercharges the transaction by hundreds or thousands of satoshis and gives the transaction’s beneficiary more funds than the intended fee policy permits.

At boundary fee rates, the same bug causes Bitcoin nodes to reject the signed transaction because validation uses the smaller estimated size while relay policy applies to the final size including OP_RETURN. This can stall withdrawals or other threshold-signed sends until the plan is reconstructed.

The issue is a resource-cost accounting mismatch rather than an invalid signature: the threshold signature signs the exact transaction object, but its fee, relay eligibility, size bound, and change were decided using a different, smaller transaction.

### Likelihood Explanation
The vulnerable input is directly reachable through the public `data: Option<Vec<u8>>` argument to `SignableTransaction::new`. Any nonzero payload up to the accepted 80-byte limit triggers the mismatch. [7](#0-6) 

No malformed encoding, malicious validator, leaked key, or special network condition is required. Exploitation for value requires the caller to benefit from the payment or otherwise have the omitted fee reflected in amounts it receives; exploitation for unavailability only requires a low configured fee rate or a transaction near a relay boundary.

### Recommendation
Include the OP_RETURN output in every transaction model used for weight, virtual-size, minimum-relay, change, and maximum-standard-weight calculations. This can be done by passing the complete output set—or an optional serialized data output—to `calculate_weight_vbytes`, rather than passing only `payments`.

Alternatively, build the candidate transaction with data before calculating `weight` and `needed_fee`, then repeat the calculation after deciding whether change can be added. Add regression coverage asserting that `needed_fee == fee_per_vbyte * signed_tx.vsize()` for zero-length and nonzero OP_RETURN payloads.

### Proof of Concept
For a one-input, one-payment transaction with an 80-byte OP_RETURN payload:

```rust
let data = vec![0u8; 80];
let signable = SignableTransaction::new(
    vec![input],
    &[(destination_script, input_value - estimated_fee)],
    None,
    Some(data),
    1,
)?;
```

`new` accepts the payload because its length is not greater than 80. [8](#0-7) 

`calculate_weight_vbytes` estimates a transaction with only the payment output, while the returned transaction additionally contains the OP_RETURN output. [9](#0-8) [1](#0-0) 

The OP_RETURN transaction output adds 8 value bytes, 1 script-length byte, a 1-byte OP_RETURN opcode, a 1-byte push opcode, and 80 payload bytes—about 91 base bytes or 364 weight units. Thus a request at `fee_per_vbyte = 1` can satisfy the internal minimum-fee check while producing a final transaction whose actual fee is roughly 91 satoshis below the relay minimum.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L85-94)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L193-202)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L204-207)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
```

**File:** networks/bitcoin/src/wallet/send.rs (L210-213)
```rust
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-233)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-255)
```rust
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
