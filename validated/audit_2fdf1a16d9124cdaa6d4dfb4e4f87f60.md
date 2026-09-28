### Title
`SignableTransaction::new` omits the OP_RETURN output from the fee/weight calculation, undercharging fees for data-bearing transactions - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` builds `tx_outs` including a user-supplied OP_RETURN `data` output, but computes `needed_fee` and the minimum-relay-fee check from `calculate_weight_vbytes(tx_ins.len(), payments, None)`, which serializes only `payments` and never sees the OP_RETURN output. Analogous to the Particle finding (a user-supplied quantity incorrectly counted toward the expected swap output bound), here a user-supplied output is *not* counted toward the expected transaction size, so the transaction is signed and published with a fee rate below the requested `fee_per_vbyte` — potentially below the mempool minimum relay fee.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed into `tx_outs` before the weight calculation [1](#0-0) , yet the weight/vbytes are derived from `payments` alone [2](#0-1) . `calculate_weight_vbytes` reconstructs the transaction exclusively from `payments` and the optional change script [3](#0-2) , so the up-to-80-byte `data` payload plus the `TxOut` overhead (~91+ bytes, ≈ 23+ vbytes) is never weighed.

The same omission affects the change path: `fee_with_change` is also computed from `payments` [4](#0-3) , so the change amount is computed against an underestimated fee and the actual paid fee `input_sat - output_sat` equals the underestimated `needed_fee` [5](#0-4) . Worse, the minimum relay fee gate uses the same underestimated `vbytes` [6](#0-5) , so a transaction carrying `data` can pass `TooLowFee` validation while its *actual* fee rate is below `DEFAULT_MIN_RELAY_TX_FEE` — it will be silently rejected by peers on broadcast.

### Impact Explanation
Any caller supplying `data` to `SignableTransaction::new` produces a transaction whose real feerate is `needed_fee / (vbytes + ~23+)` instead of `fee_per_vbyte`. When `fee_per_vbyte` is at or near the relay minimum (the code explicitly supports enforcing that bound), the signed transaction is non-relayable: the threshold-signed transaction cannot confirm, and the selected inputs remain committed to a transaction the network will not propagate. This is a public-inputs-reachable correctness bug in the fee formula — the produced TX is not spendable/confirmable as constructed — matching the accepted impact class of an incorrect transaction/fee formula caused by miscounting user-provided data.

### Likelihood Explanation
`data` is a first-class parameter of `SignableTransaction::new` and the code path is unconditional once `data.is_some()`. The underestimation is deterministic (~23+ vbytes for a full 80-byte payload); whether it crosses the relay floor depends on the configured `fee_per_vbyte`, but every data-bearing transaction pays a strictly lower effective feerate than requested, and low-feerate transactions face eviction/non-propagation.

### Recommendation
Pass the full output set (including the OP_RETURN output) into the weight calculation. Concretely, compute weight/vbytes from `&tx_outs` (or a `payments`-plus-data structure) in both the initial calculation and the change calculation at `send.rs:204` and `send.rs:225-226`, so `needed_fee` reflects the true transaction size, and perform the `TooLowFee` check against that true vsize.

### Proof of Concept
In `networks/bitcoin/tests/wallet.rs` style:

```rust
// SignableTransaction::new(inputs, &payments, change, Some(vec![0; 80]), fee_per_vbyte)
// Build the same TX twice: once with data, once without.
let tx_no_data = SignableTransaction::new(inputs.clone(), &payments, None, None, FEE).unwrap();
let tx_data =
  SignableTransaction::new(inputs, &payments, None, Some(vec![0u8; 80]), FEE).unwrap();

// The fee is identical even though the transaction is ~91 bytes larger:
assert_eq!(tx_no_data.needed_fee(), tx_data.needed_fee());
// tx_data.transaction().vsize() > tx_no_data.transaction().vsize()
// => effective feerate = needed_fee / vsize < FEE for the data-bearing TX.
// With FEE = DEFAULT_MIN_RELAY_TX_FEE / 1000, the data TX passes the
// TooLowFee check yet relays below the minimum fee rate.
```

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L85-99)
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

**File:** networks/bitcoin/src/wallet/send.rs (L194-202)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-206)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
```

**File:** networks/bitcoin/src/wallet/send.rs (L211-213)
```rust
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-228)
```rust
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
```
