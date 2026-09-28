### Title
`SignableTransaction::new` computes weight, vbytes, `needed_fee`, and the `MAX_STANDARD_TX_WEIGHT` bound on a transaction that omits the OP_RETURN `data` output - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes an OP_RETURN output carrying caller-supplied `data` into `tx_outs` before measuring the transaction, but both calls to `calculate_weight_vbytes` only receive `payments` (never the data output). The fee (`needed_fee`), the minimum-relay-fee check, and the maximum-standard-weight check are therefore all computed against a smaller transaction than the one actually produced — the same bug class as MIMO M-05, where a quantity was calculated on the wrong base amount. The signed transaction pays the intended absolute fee but a lower effective feerate than `fee_per_vbyte`, and the weight bound can be satisfied even when the real transaction exceeds `MAX_STANDARD_TX_WEIGHT`.

### Finding Description
The data output is appended at send.rs:194-202, yet weight/vbyte measurement at send.rs:204 (no change) and send.rs:225-227 (with change) builds the template transaction solely from `payments`. `needed_fee = fee_per_vbyte * vbytes` (send.rs:206/227) thus prices a transaction missing up to 80 bytes of pushed data plus OP_RETURN script/output overhead (~10 bytes of output + script wrapper, i.e. roughly 90+ bytes / ~360 weight units / ~90 vbytes). Since `fee()` is `sum(inputs) - sum(outputs)` and the data output is zero-valued, the real transaction ships with the underestimated fee, so its actual feerate is `needed_fee / real_vbytes < fee_per_vbyte`.

The same omission affects:
- the minimum relay fee check at send.rs:211 — a transaction constructed at exactly `fee_per_vbyte` can fall below the node's minimum effective feerate and be rejected/non-propagated,
- the `MAX_STANDARD_TX_WEIGHT` check at send.rs:241 — a transaction at the boundary (up to 520 inputs) can pass while the real weight exceeds the standardness limit, producing a transaction the network will not relay.

`data` is attacker-influencable transaction metadata (it is the same channel used for `RefundableInInstruction` OP_RETURN payloads) and requires no privilege to size up to the 80-byte cap checked at send.rs:171.

### Impact Explanation
Transactions including `data` underpay relative to the requested feerate, or become non-standard/unrelayable in edge cases (boundary weight, minimum-feerate constructions). The multisig signs and the wallet reports a valid transaction whose inputs are consumed, yet the transaction may fail to propagate or confirm — funds appear sent but the transaction stalls, mirroring the MIMO finding's "unpredictable result for both parties / user unexpectedly harmed" outcome. If the transaction never confirms, the consumed UTXOs remain locked from the coordinator's accounting perspective until replaced or abandoned.

### Likelihood Explanation
Any call to `SignableTransaction::new` with `data = Some(..)` triggers the miscalculation deterministically; the severity of the outcome depends on how close the requested feerate is to relay minimums and how close the transaction is to the weight limit. The miscalculation itself is unconditional for all data-carrying transactions.

### Recommendation
Include the OP_RETURN output in the weight/vbyte measurement, e.g. extend `calculate_weight_vbytes` to accept the fully-constructed output list (payments + optional data output + optional change), or pass the `data` length into the template so the measured `Transaction` matches `tx_outs` exactly. Recompute `vbytes`, `needed_fee`, and `weight` over the final output set before the checks at send.rs:211 and send.rs:241.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// In SignableTransaction::new:

if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(/* up to 80 bytes */),
  })
}

// Weight is measured WITHOUT the data output:
let (mut weight, vbytes) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, None); // payments only
let mut needed_fee = fee_per_vbyte * vbytes;
```
A concrete demonstration: call `SignableTransaction::new(inputs, &payments, None, Some(vec![0u8; 80]), fee_per_vbyte)`. `tx.weight()` of the resulting `SignableTransaction.tx` exceeds `weight` used for `needed_fee` by ~360 weight units (~90 vbytes), so the effective feerate is ~90 vbytes' worth of fee short of `fee_per_vbyte`. With `fee_per_vbyte` at the minimum relay rate, or with enough inputs that `weight` just passes `MAX_STANDARD_TX_WEIGHT`, the produced transaction is underpaid or non-standard while `new` returns `Ok`. [1](#0-0) [2](#0-1)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L194-206)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-243)
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

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
