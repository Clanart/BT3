### Title
Fee and weight accounting in `SignableTransaction::new` ignores the OP_RETURN data output, underpaying the fee and overstating change - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` appends an `OP_RETURN` output carrying up to 80 bytes of caller-supplied `data` to the transaction's outputs, yet computes `weight`, `vbytes`, `needed_fee`, the change amount, and the `MAX_STANDARD_TX_WEIGHT` check using only the `payments` and `change` outputs. The data output's ~10–90+ vbytes are never accounted for, so the transaction pays a lower fee rate than requested and credits too much to change. This is the same bug class as the reference report: an accounting value (`needed_fee`/`change`) is computed against the wrong amount (outputs excluding the data output instead of the actual full output set).

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` pushes the OP_RETURN output into `tx_outs` before any sizing is done: [1](#0-0) 

but then calls `calculate_weight_vbytes(tx_ins.len(), payments, None)` and later `(tx_ins.len(), payments, Some(&change))` — `payments`, not `tx_outs`: [2](#0-1) [3](#0-2) 

`calculate_weight_vbytes` builds its measurement transaction's `output` list solely from `payments` plus optional `change`; there is no parameter or path for the data output: [4](#0-3) 

Consequences within the same function:

- `needed_fee = fee_per_vbyte * vbytes` undercharges by `fee_per_vbyte * vbytes_of(OP_RETURN output)` (~11–30 vbytes for 1–80 bytes of data: output base + script length + pushdata).
- `Err(TooLowFee)` is checked against this under-measured `vbytes`, so a transaction can pass the minimum-relay-fee check while its true fee rate is below it.
- The change output value `input_sat - (payment_sat + fee_with_change)` is too large by the uncharged fee — directly analogous to `totalBorrowed -= lostAmt` instead of the full owed amount: the debit to the payer is computed from an incomplete output set, so the excess silently becomes change instead of fee.
- The `weight > MAX_STANDARD_TX_WEIGHT` check uses the data-less `weight`, so a transaction can exceed the standardness limit and still be produced.
- `NotEnoughFunds` is evaluated against the understated `needed_fee`, allowing construction of a transaction that may not relay.

`fee()` at lines 138–141 correctly returns `sum(inputs) - sum(outputs)` for the final transaction, so `fee() < needed_fee` whenever data is present and change is created, confirming the discrepancy: [5](#0-4) 

### Impact Explanation
An unprivileged party who controls the `data` fed into `SignableTransaction::new` (transaction data they cause to be signed — e.g., arbitrary `InInstruction`-derived payloads or inscriptions embedded via the documented data path) can produce a signed transaction whose real fee rate is materially below the `fee_per_vbyte` requested and below the enforced minimum relay fee. Such a transaction may not propagate or confirm, freezing the spent inputs, and the multisig will have already committed to it via `TransactionSignMachine::sign` at lines 373–397. Additionally the change output is overpaid (or created when it should have been dust), misreporting the recovered funds — the "wrong amount subtracted" accounting flaw mapped onto Serai's fee/change math. Worst case, an 80-byte payload also bypasses the standardness weight bound.

### Likelihood Explanation
Reaching this requires only calling `SignableTransaction::new` with non-`None` `data`, which is a public API accepting caller-supplied bytes (up to 80 bytes per the `TooMuchData` check at lines 171–173). No key access, collusion, or validator misbehavior is needed; any flow that lets an external party choose or influence the OP_RETURN data triggers deterministic underpayment proportional to data size and fee rate. The miscalculation is unconditional once `data.is_some()`.

### Recommendation
Include the OP_RETURN output in the measurement transaction. Pass the already-built `tx_outs` (or the `data` script) into `calculate_weight_vbytes` at both call sites (lines 204 and 225–226) so `weight`, `vbytes`, `needed_fee`, the change subtraction, and the `MAX_STANDARD_TX_WEIGHT` check reflect the true output set. Alternatively, add a `data: Option<&ScriptBuf>` parameter to `calculate_weight_vbytes` and push the OP_RETURN `TxOut` there alongside payments and change.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs context
let data = vec![0u8; 80]; // maximum allowed by the TooMuchData check
let tx = SignableTransaction::new(
  inputs, payments, Some(change), Some(data), fee_per_vbyte,
).unwrap();

// The actual TX has the OP_RETURN output, but needed_fee was computed without it.
assert!(tx.transaction().output.iter().any(|o| o.script_pubkey.is_op_return()));

// The real fee paid is less than the fee that was "needed" for the requested rate,
// and less than needed_fee() claims once change is present:
//   tx.fee() == needed_fee - fee_per_vbyte * vbytes(op_return_output)
assert!(tx.fee() < tx.needed_fee());
// With data near 80 bytes, weight also under-reports by ~90+ WU vs the built tx.
```

Root cause in one line: `Self::calculate_weight_vbytes(tx_ins.len(), payments, ...)` is invoked with `payments` while the signed transaction's outputs are `tx_outs`, which additionally contains the OP_RETURN output — the accounted output set never matches the real one.

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

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
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
      }
    }
```
