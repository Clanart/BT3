### Title
`SignableTransaction::new` omits the `OP_RETURN` data output from fee/weight calculation, so the signed transaction underpays the requested fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The audit report's bug class is "an action is charged (or not charged) a fee incorrectly" — fee accounting that does not match the actual cost of the operation. The direct analog in Serai's in-scope code is in `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`: the `OP_RETURN` output created from the user-supplied `data` parameter is added to the real transaction outputs but is never included in `calculate_weight_vbytes`, so `needed_fee` (and the min-relay-fee check) are computed against a smaller virtual size than the transaction actually has.

### Finding Description
`SignableTransaction::new` pushes an `OP_RETURN` output onto `tx_outs` when `data` is supplied (lines 194–202), but the fee is computed at line 204 via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, which builds its dummy transaction only from `payments` (lines 85–93). The data output's weight (up to ~90 WU+ for an 80-byte push plus output overhead) is never counted. The same omission applies to the `fee_with_change` computation at lines 225–227. Consequently:

- `needed_fee = fee_per_vbyte * vbytes` uses an underestimated `vbytes`, so the effective fee rate of the constructed transaction is strictly lower than the caller-requested `fee_per_vbyte`.
- The `TooLowFee` check at line 211 compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the same underestimated `vbytes`, so a transaction can pass the check yet actually be below the minimum relay fee once the uncounted `OP_RETURN` output is included.
- The change branch deducts `fee_with_change` (also underestimated), leaving the change output slightly larger than the requested rate would allow — the difference is implicitly donated to the change output rather than the fee, so `fee()` (lines 138–141) still reports the low `needed_fee`.

This is reachable entirely through the public input `data: Option<Vec<u8>>` to `SignableTransaction::new`, which is fed by untrusted/protocol-supplied transaction parameters.

### Impact Explanation
The threshold signing flow (`TransactionMachine` → `TransactionSignMachine::sign` → `TransactionSignatureMachine::complete`) produces a fully valid, signed Bitcoin transaction whose actual fee rate is lower than the rate the coordinator requested and, in the worst case, below `DEFAULT_MIN_RELAY_TX_FEE`. Such a transaction will not relay or confirm despite being correctly signed, stalling a spend of vault/multisig funds until the fee shortfall is recognized and a new signing round is performed (the inputs remain locked to that intent). Additionally, `needed_fee()` misreports the fee the transaction requires, corrupting any accounting that relies on it.

### Likelihood Explanation
Any caller that attaches `data` (an `OP_RETURN`) to a send triggers the miscalculation deterministically — no race or adversarial timing needed. The magnitude grows with the data length (up to 80 bytes, ~23 vbytes uncounted). Whether it drops below min-relay depends on how close `fee_per_vbyte` is to the minimum; for fee rates near the floor it is reliably triggered. Impact is a stalled/underpriced spend rather than fund theft, which fits Medium.

### Recommendation
Include the `OP_RETURN` output in the weight calculation: pass the full output list (payments plus the data output) into `calculate_weight_vbytes`, e.g., by constructing the dummy `tx.output` from `tx_outs` (after the `OP_RETURN` push) rather than from `payments` alone, in both the no-change and change call sites.

### Proof of Concept
```rust
// In networks/bitcoin/src/wallet/send.rs, SignableTransaction::new:
let data = vec![0u8; 80]; // max allowed
// tx_outs gets a 0-value OP_RETURN output (lines 194-202),
// but:
let (_weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
// builds the weight dummy only from `payments` (lines 85-93) and `change`,
// never from the OP_RETURN output. For 80 bytes of data, the real tx is
// ~23 vbytes larger than `vbytes`.
// needed_fee = fee_per_vbyte * vbytes is therefore too small, and the
// DEFAULT_MIN_RELAY_TX_FEE check at line 211 uses the same low vbytes,
// so a near-minimum fee_per_vbyte yields a signed tx under the relay floor.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** networks/bitcoin/src/wallet/send.rs (L194-204)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-232)
```rust
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
```
