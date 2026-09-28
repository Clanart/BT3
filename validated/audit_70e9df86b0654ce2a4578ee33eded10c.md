### Title
OP_RETURN data output excluded from weight/vsize and fee calculation lets `SignableTransaction::new` produce under-funded or non-standard transactions that cannot be broadcast - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report's bug class — an internal accounting/consistency check that disagrees with the actual value transferred, causing the operation to become impossible to complete — maps onto `SignableTransaction::new` in bitcoin-serai. The transaction's weight and vsize are estimated with `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, but `payments` does not include the OP_RETURN output that is unconditionally appended to `tx_outs` beforehand. Both the fee calculation and the `MAX_STANDARD_TX_WEIGHT` check therefore run against a transaction smaller than the one actually signed and broadcast, producing transactions the Bitcoin network will reject — leaving vault inputs unspendable by that plan, analogous to positions that cannot be unwound.

### Finding Description
`SignableTransaction::new` builds the real outputs first — payments plus an OP_RETURN output carrying up to 80 bytes of caller-supplied `data` — at `send.rs:194-202`. Only afterwards, at line 204, does it compute the size:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` expands a mock `Transaction` from the provided `payments` slice (`send.rs:85-93`), so the OP_RETURN output appended to `tx_outs` is never represented in `weight` or `vbytes`. Consequences:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206) and `fee_with_change = fee_per_vbyte * vbytes_with_change` (line 227) are both computed on a vsize that omits the OP_RETURN output (~13–90+ vbytes for an 80-byte payload). The change output value is likewise computed as `input_sat - (payment_sat + fee_with_change)` (line 228), so the *actual* fee paid, `sum(inputs) - sum(outputs)` (`fee()`, line 139), equals the underestimated `needed_fee` — while the real transaction is larger. The effective fee rate is therefore strictly below `fee_per_vbyte` and can fall below `DEFAULT_MIN_RELAY_TX_FEE`, causing mempool rejection despite the `TooLowFee` check passing.
2. `weight` used for the `TooLargeTransaction` check at line 241 (`weight > MAX_STANDARD_TX_WEIGHT`) omits the OP_RETURN output's weight, so a transaction that genuinely exceeds the 400,000 WU standardness limit passes the check and is signed. The min-relay-fee check at line 211 is computed against the same underestimated `vbytes`.

The resulting `SignableTransaction` is what every FROST participant signs via `TransactionSignMachine::sign` / `taproot_key_spend_signature_hash` over `Prevouts::All` (`send.rs:373-390`), so the under-funded/non-standard transaction is the one actually produced by `TransactionSignatureMachine::complete`.

### Impact Explanation
A transaction built through this path either pays a fee rate lower than the node's minimum relay fee (standard mempool policy rejection, "min relay fee not met") or exceeds `MAX_STANDARD_TX_WEIGHT` ("tx-size" policy rejection). In both cases the signed transaction cannot be broadcast: the multisig's inputs are locked behind a plan that can never complete on-chain — the same "position cannot be unwound / funds frozen" outcome as the reference bug, reachable whenever `data` (which callers control) is non-trivial or the size is borderline.

### Likelihood Explanation
Any call to `SignableTransaction::new` with `data: Some(_)` triggers the miscalculation. Whether it causes rejection depends on how close the true fee rate lands to policy minimums and how close the weight is to the standardness cap; at low `fee_per_vbyte` (e.g., 1 sat/vB, near `DEFAULT_MIN_RELAY_TX_FEE`) the added ~90 vbytes can drop the effective rate below the relay floor, and padding `payments`/inputs to near `MAX_STANDARD_TX_WEIGHT` deterministically produces a non-standard transaction.

### Recommendation
Include the OP_RETURN output in the size estimation — e.g., pass the fully constructed `tx_outs` (payments + OP_RETURN + optional change) into `calculate_weight_vbytes` instead of `payments`, or add the data output to the mock transaction explicitly. Recompute `needed_fee`, the change value, and the `MAX_STANDARD_TX_WEIGHT` check against that complete output set so the signed transaction's actual vsize matches what was paid for.

### Proof of Concept
```rust
// inputs: a single ReceivedOutput worth `v` sats
// fee_per_vbyte = 1 sat/vB (>= min relay for the *estimated* size)
let payments = vec![(p2tr_script_buf(key).unwrap(), DUST)];
let data = Some(vec![0u8; 80]); // maximal OP_RETURN payload

let tx = SignableTransaction::new(
  inputs, &payments, Some(change_addr.clone()), data.clone(), 1,
).unwrap();

// tx.needed_fee() == 1 * vbytes_estimated_without_opreturn
// Actual vsize is larger by the OP_RETURN output (~14 + 82 bytes).
let actual_fee = tx.fee(); // == needed_fee (underestimated)
let actual_vsize = tx.transaction().vsize() as u64; // larger than estimated
assert!(actual_fee < actual_vsize); // effective rate < 1 sat/vB -> relay rejection
```

At line 204 the estimator receives `payments`, while the OP_RETURN `TxOut` was already pushed at `send.rs:195-201`; no code path ever re-measures the final `tx_outs` before the `TooLargeTransaction` check at line 241, so the discrepancy is guaranteed whenever `data` is provided. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** networks/bitcoin/src/wallet/send.rs (L193-213)
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
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }
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
