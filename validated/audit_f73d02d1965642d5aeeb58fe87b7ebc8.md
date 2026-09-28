### Title
Bitcoin transactions can be signed with a fee below the requested and relay-required rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` adds a caller-controlled `OP_RETURN` output before calculating the transaction's fee, but its size calculation ignores that output. The resulting transaction can therefore be signed and returned even though its actual fee rate is below `fee_per_vbyte`, and in some cases below Bitcoin's minimum relay fee.

### Finding Description
When `data` is supplied, `SignableTransaction::new` appends a zero-valued `OP_RETURN` output to `tx_outs` at `networks/bitcoin/src/wallet/send.rs:193-201`. [1](#0-0) 

The subsequent weight and virtual-size calculation passes only the payment outputs and does not include `tx_outs` or the `OP_RETURN` output. [2](#0-1) 

The too-low virtual size is then used both for the caller-requested fee and for the minimum-relay-fee check. [3](#0-2) 

The change calculation has the same omission because it again passes only `payments` to `calculate_weight_vbytes`. [4](#0-3) 

The final transaction nevertheless includes the `OP_RETURN` output, so its serialized size and required relay fee are larger than the values used for validation. [5](#0-4) 

### Impact Explanation
A transaction containing `data` can consume substantially more virtual bytes than accounted for in `needed_fee`. With a low requested fee rate, the transaction can pass `TooLowFee` validation while still being too cheap for standard relay. At higher rates, the transaction is relayable but pays less than the requested `fee_per_vbyte`, increasing confirmation delay and potentially stalling dependent wallet operations.

### Likelihood Explanation
The `data` parameter is a public input to `SignableTransaction::new`, and up to 80 bytes are explicitly accepted. Any caller supplying a data-bearing transaction triggers the discrepancy; no malicious validator, peer, key holder, or invalid curve input is required.

### Recommendation
Calculate weight and virtual size from the complete output set actually placed in the transaction, including the `OP_RETURN` output, both with and without change. Alternatively, extend `calculate_weight_vbytes` to accept `data` or generic `TxOut`s rather than only `(ScriptBuf, u64)` payment pairs.

### Proof of Concept
For one Taproot input, one P2TR payment, no change, and an 80-byte `data` payload:

1. `tx_outs` receives both the payment and an approximately 82-byte `OP_RETURN` output. [1](#0-0) 
2. `calculate_weight_vbytes(1, payments, None)` estimates a transaction containing only the payment output. [2](#0-1) 
3. With `fee_per_vbyte = 1`, `needed_fee` is based on the smaller payment-only virtual size, while the actual transaction is roughly 80 bytes larger. [3](#0-2) 
4. If the payment-only estimate is approximately 111 vbytes and the serialized transaction is approximately 193 vbytes, `needed_fee = 111` passes a minimum-fee estimate based on 111 vbytes despite the actual transaction needing approximately 193 satoshis for 1 sat/vbyte relay.
5. The signed transaction is therefore produced with an actual fee materially below the requested fee rate, and potentially below the relay minimum.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L193-201)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-212)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-233)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L245-254)
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
```
