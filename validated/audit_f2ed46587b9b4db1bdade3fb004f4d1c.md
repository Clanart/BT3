### Title
`SignableTransaction::new` omits the OP_RETURN data output from weight/fee calculation, underpaying the fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the `LotteryMath.calculateNewProfit` bug — where a formula omitted applying the payout percentage and produced a wildly wrong `expectedRewardsOut` — `SignableTransaction::new` computes the transaction weight/vbytes (and therefore `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check) from a transaction that does **not** include the OP_RETURN output carrying `data`, even though that output is added to the real transaction. The formula is missing a term.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed into `tx_outs` before the weight calculation: [1](#0-0) 

```rust
    if let Some(data) = data {
      tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(...),
      })
    }

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

But `calculate_weight_vbytes` only builds a transaction out of `payments` and the optional `change` — the `data` output is never represented: [2](#0-1) 

The same omission occurs in the change-output path, which again calls `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` with no accounting for the OP_RETURN output: [3](#0-2) 

Consequences:

1. `needed_fee = fee_per_vbyte * vbytes` under-counts the vbytes by the size of the OP_RETURN output (~10 + data_len bytes, plus output overhead — up to ~100 vbytes for an 80-byte payload). The signed transaction therefore pays a lower effective fee rate than the caller-specified `fee_per_vbyte`.
2. The minimum-relay-fee check `needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` uses the same underestimated `vbytes`, so a transaction can pass the internal check while actually falling below the network's minimum relay fee once the real (larger) size is measured — a standardness rejection, meaning the constructed transaction would not propagate.
3. The `weight > MAX_STANDARD_TX_WEIGHT` check at line 241 also uses the underestimated `weight`, so a transaction near the limit plus a data output can exceed standard weight.

### Impact Explanation
Any caller that specifies `data` (an OP_RETURN payload) gets a `SignableTransaction` whose `needed_fee()` and `fee()` claim a fee rate that the real transaction does not achieve. The resulting signed transaction can be non-standard (below min relay fee or above standard weight), so funds locked in its outputs are effectively unspendable via that transaction — the transaction will be rejected by relaying nodes. This matches the accepted impact class of an incorrect verifier/fee formula reachable purely through public inputs (the `data` parameter).

### Likelihood Explanation
The bug triggers deterministically whenever `data` is `Some` — every byte of OP_RETURN payload is missing from the size accounting. The underpayment is bounded (~11–92+ vbytes depending on payload length), so whether the transaction drops below relay minimum depends on how close the requested `fee_per_vbyte` is to the floor; at `fee_per_vbyte = 1 sat/vbyte` the shortfall alone can push the effective rate under 1 sat/vbyte. Medium likelihood of producing a non-relayable transaction; bounded magnitude.

### Recommendation
Include the OP_RETURN output in the size estimation. Either pass the data length into `calculate_weight_vbytes` so it can add a `TxOut` with `ScriptBuf::new_op_return(PushBytesBuf)` (or an equivalently-sized script), or compute the weight over `tx_outs` directly after all outputs (payments, data, change) are known, recomputing when the change output is added.

```rust
// sketch: extend calculate_weight_vbytes to take the full output list,
// or add the OP_RETURN script to the mock transaction before weighing
if let Some(data) = &data {
  tx.output.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(
      PushBytesBuf::try_from(data.clone()).unwrap(),
    ),
  });
}
```

### Proof of Concept
```rust
// In networks/bitcoin/tests/wallet.rs style — no RPC needed to show the miscalculation
let tx_no_data = SignableTransaction::new(inputs.clone(), &payments, None, None, FEE).unwrap();
let tx_data = SignableTransaction::new(
    inputs.clone(), &payments, None, Some(vec![0; 80]), FEE,
).unwrap();

// needed_fee ignores the ~100-byte OP_RETURN output entirely
assert_eq!(tx_no_data.needed_fee(), tx_data.needed_fee());

// The actual signed transaction is larger, so fee()/vsize < FEE
let signed = sign(&keys, &tx_data);
let actual_rate = signed.fee() / signed.vsize() as u64; // < FEE
```

Additionally, `TransactionError::TooLowFee` can be bypassed: a `data`-carrying TX passes the internal min-fee check while its real fee rate is below `DEFAULT_MIN_RELAY_TX_FEE`, producing a transaction the Bitcoin network will not relay.

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
