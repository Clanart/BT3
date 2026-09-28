### Title
OP_RETURN data output is omitted from fee and weight accounting - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
`SignableTransaction::new` adds a caller-controlled `OP_RETURN` output to the transaction before estimating the transaction's virtual size, but the estimator only receives `payments`, not the full output list. Consequently, `needed_fee`, change calculation, and the maximum-weight check all omit the serialized `OP_RETURN` output.

### Finding Description
`tx_outs` receives the `OP_RETURN` output at lines 193-202. The subsequent calls to `calculate_weight_vbytes` pass only `payments`, so the reconstructed transaction used for `weight` and `vbytes` does not contain that output. This affects both the initial estimate and the estimate including change.

The issue is reachable through the public `SignableTransaction::new` API by supplying `data: Some(...)`. Up to 80 bytes are accepted at lines 171-173.

### Impact Explanation
When a change output is used, its value is calculated as `input_sat - payment_sat - fee_with_change`. Since `fee_with_change` omits the `OP_RETURN` output's size, the change output receives the amount that should have funded those bytes, and the signed transaction's actual fee rate is lower than requested.

A transaction near the minimum relay fee can therefore fall below the required rate and be rejected. At the upper bound, the `MAX_STANDARD_TX_WEIGHT` check also uses the underestimated weight, potentially producing a non-standard transaction after the `OP_RETURN` output is included.

### Likelihood Explanation
Any caller that supplies transaction data and requests change can trigger the miscalculation. The omitted cost is roughly 11 vbytes plus the pushed data length, so the largest accepted payload can omit approximately 90 vbytes from the estimate.

### Recommendation
Calculate weight and virtual size from the exact candidate `tx_outs`, including the `OP_RETURN` output, for both the no-change and with-change cases. Alternatively, pass the constructed data output into `calculate_weight_vbytes`. Add a regression test asserting:

```rust
needed_fee == fee_per_vbyte * signed_transaction.vsize() as u64
```

for `data: Some(vec![0; 80])` and a change address.

### Proof of Concept
The vulnerable sequence is:

1. `data` is converted into an `OP_RETURN` `TxOut` and pushed into `tx_outs`.
2. `calculate_weight_vbytes(tx_ins.len(), payments, None)` is called without that output.
3. When change exists, `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` again omits it.
4. `tx_outs`, including the uncounted output, is stored in the returned transaction.

Relevant code: `networks/bitcoin/src/wallet/send.rs` lines 193-234.

A regression-style assertion is:

```rust
let signable = SignableTransaction::new(
  inputs,
  &payments,
  Some(change_script),
  Some(vec![0; 80]),
  fee_per_vbyte,
).unwrap();

// Reconstruct signed vsize by adding the 64-byte Taproot witness
// to each input of signable.transaction().
let signed_vsize = reconstructed_signed_tx.vsize() as u64;

// This currently fails: needed_fee was calculated without OP_RETURN.
assert_eq!(signable.needed_fee(), fee_per_vbyte * signed_vsize);
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L193-207)
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

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-234)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-254)
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
```
