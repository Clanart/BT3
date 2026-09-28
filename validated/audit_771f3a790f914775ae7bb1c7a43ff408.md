### Title
OP_RETURN data output omitted from fee/weight calculation produces under-priced or oversized transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes an `OP_RETURN` output carrying up to 80 bytes of data onto `tx_outs`, but computes the transaction weight and vbytes via `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which only models `payments` (and optionally change). The `OP_RETURN` output is never included in the weight estimate, so `needed_fee` and the change-amount check are computed against a smaller transaction than the one actually signed and broadcast.

### Finding Description
In `SignableTransaction::new`, the data output is appended before the fee is calculated:

- `networks/bitcoin/src/wallet/send.rs:194-202` pushes `TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(data) }` into `tx_outs`.
- `networks/bitcoin/src/wallet/send.rs:204` then calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — the `payments` slice does not include the OP_RETURN output, and `calculate_weight_vbytes` (lines 62-99) builds its model transaction solely from `payments` plus optional change.
- The same omission occurs in the change branch at `send.rs:225-227`, where `fee_with_change` is computed from a model that still lacks the data output.

The result: `needed_fee = fee_per_vbyte * vbytes` is computed on a vsize that excludes the OP_RETURN output (~9–91+ bytes of real vsize for up to 80 bytes of data). Since the change value is set to `input_sat - payment_sat - fee_with_change` (`send.rs:228-233`), the actual fee paid equals this under-estimated `fee_with_change` — the transaction is signed with a lower feerate than the caller requested. Two consequences:

1. If the caller's `fee_per_vbyte` was chosen to just satisfy `DEFAULT_MIN_RELAY_TX_FEE` (checked at `send.rs:211` against the underestimated vbytes), the real transaction can fall below the minimum relay feerate and be rejected by the mempool — the signed transaction spends confirmed inputs that the network will not confirm, leaving the multisig's funds stuck.
2. More generally, every transaction carrying an OP_RETURN silently pays a lower effective feerate than requested, degrading confirmation reliability exactly when `data` is used (Serai's data-carrying burns/mints).

### Impact Explanation
An attacker who can influence the `data` field (e.g., user-supplied memo/metadata attached to a withdrawal, which is precisely what the `data` parameter exists for) forces the threshold multisig to sign transactions whose true feerate is lower than the rate the scheduler/intent specified — potentially below relay minimums. This yields transactions that will not propagate or confirm while consuming the input UTXOs' outpoints, causing funds to be reported as spent/scheduled yet unmovable without re-planning — the Serai analog of the reported "value accounting exploited to drain/stick funds" class. It is a deterministic miscalculation in consensus-relevant fee math, not a timing or policy edge case.

### Likelihood Explanation
Triggers whenever `SignableTransaction::new` is called with `data: Some(..)` and a modest `fee_per_vbyte`. The mispricing is largest with the maximum 80-byte payload; the relay-rejection case requires the target feerate to be near the minimum, which is a routine configuration for cost-sensitive batching. Requires no collusion or privileged position — only the ability to have data attached to a payment.

### Recommendation
Include the OP_RETURN output in the weight model. Change `calculate_weight_vbytes` to take the full `tx_outs` (or an extra `data_len`/`outputs` parameter), and pass the outputs including the OP_RETURN `TxOut` at both call sites (`send.rs:204` and `send.rs:225`). Alternatively, compute the fee once from the fully constructed `tx` (`tx.weight()`) after all outputs — payments, data, and change — are known, restructuring so the change existence decision re-runs the weight check with the change output included.

### Proof of Concept
```rust
// networks/bitcoin: construct two identical transactions, one with data
let fee_per_vbyte = 1; // 1 sat/vbyte, near minimum relay
let payments = [(p2tr_script_buf(key).unwrap(), DUST)];

let tx_no_data = SignableTransaction::new(inputs.clone(), &payments, None, None, fee_per_vbyte).unwrap();
let tx_with_data = SignableTransaction::new(
    inputs, &payments, None, Some(vec![0u8; 80]), fee_per_vbyte,
).unwrap();

// BUG: needed_fee ignores the ~91-byte OP_RETURN output
assert_eq!(tx_no_data.needed_fee(), tx_with_data.needed_fee());
// Yet the real signed TX is larger: effective feerate < 1 sat/vbyte,
// potentially below DEFAULT_MIN_RELAY_TX_FEE -> mempool rejection while inputs are consumed.
```

cite: [1](#0-0)

### Citations

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
