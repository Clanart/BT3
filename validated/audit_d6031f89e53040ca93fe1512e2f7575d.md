### Title
Fee calculation omits the OP_RETURN data output, under-charging the declared fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The H-01 bug class is a charge applied on the wrong branch: a fee is deducted in a code path where it doesn't belong and skipped where it does. In `SignableTransaction::new`, the fee is computed from a weight estimate built only from `payments`, while the OP_RETURN output appended for `data` is never included in either weight calculation. The result is the mirror-image of the Putty bug: a fee that should be charged (for the bytes of the OP_RETURN output) is not charged.

### Finding Description
`SignableTransaction::new` pushes an OP_RETURN `TxOut` onto `tx_outs` when `data` is `Some` (send.rs:194-202), but both calls to `calculate_weight_vbytes` pass `payments` — which excludes that output — as the output list (send.rs:204 and send.rs:226). `needed_fee` and `fee_with_change` are therefore computed on a vbyte count that omits the OP_RETURN output (~83 bytes + output overhead for an 80-byte payload).

The minimum-relay-fee check `needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` (send.rs:211) also uses this under-estimated `vbytes`, so it can pass while the real transaction's sat/vbyte rate falls below the relay minimum. `fee()` (send.rs:138-141) correctly reports inputs − outputs, so the discrepancy is directly observable. The `data` argument is attacker-controlled input, making this reachable by any party who causes the wallet to build a transaction carrying data.

### Impact Explanation
The transaction signs and broadcasts paying strictly less fee than `fee_per_vbyte` specifies. With `fee_per_vbyte` at or near 1 sat/vbyte, adding ~90 unpaid vbytes drops the effective rate below Bitcoin Core's 1 sat/vb minimum relay fee, so the transaction is rejected by peers and the inputs are effectively frozen until rebuilt — funds reported as sent are not spendable. Even when relay succeeds, the protocol's intended fee rate is silently violated, analogous to Putty's "leaked value" from a missing fee deduction.

### Likelihood Explanation
Triggered whenever `data` is `Some` (any non-empty OP_RETURN payload up to 80 bytes) together with a low `fee_per_vbyte`. No collusion or privileged position is required; `data` and `fee_per_vbyte` are ordinary public constructor arguments.

### Recommendation
Include the OP_RETURN output in the weight estimate — e.g., compute weight from the actual `tx_outs` (payments + OP_RETURN) rather than `payments` alone, or add the data output to the list passed to `calculate_weight_vbytes` at send.rs:204 and send.rs:226 — and evaluate the minimum-fee check against the final output set.

### Proof of Concept
```rust
// Conceptual: one P2TR input of 100_000 sats, one payment, 80-byte OP_RETURN, 1 sat/vb
let payments = vec![(addr(), 50_000)];
let data = Some(vec![0u8; 80]);
let tx = SignableTransaction::new(inputs, &payments, None, data, 1).unwrap();
// needed_fee was computed on vbytes WITHOUT the OP_RETURN output (~200 vb -> ~200 sats)
// The real tx.vsize() is ~291 vb, so effective rate ~0.69 sat/vb < 1 sat/vb minimum relay fee
// -> bitcoin nodes reject the transaction despite TooLowFee check having passed
assert!(tx.fee() < tx.transaction().vsize() as u64); // under-paid
```

Relevant code: [1](#0-0)  and [2](#0-1) .

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L194-212)
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
