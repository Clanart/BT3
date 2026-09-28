### Title
`SignableTransaction::new` omits the OP_RETURN data output from fee and weight calculations, producing transactions that underpay the requested fee rate and can exceed `MAX_STANDARD_TX_WEIGHT` - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report concerns a fixed gas/weight assumption that does not account for variable-length message data. The Serai analog lives in `bitcoin-serai`'s transaction builder: `SignableTransaction::new` accepts an arbitrary `data` payload and appends it as an OP_RETURN output, yet both the fee estimation (`calculate_weight_vbytes`) and the standardness weight check are computed over a transaction that excludes that output. The result is a transaction whose actual size exceeds what was accounted for, analogous to a fixed-gas cross-chain message whose real cost depends on list length.

### Finding Description
In `SignableTransaction::new`, when `data` is `Some`, an OP_RETURN `TxOut` is pushed onto `tx_outs` before the fee is computed [1](#0-0) . However, the weight/vbyte estimate is then computed as `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — it passes `payments` (which do not include the OP_RETURN output) rather than `tx_outs` [2](#0-1) . `calculate_weight_vbytes` builds the template transaction's `output` field exclusively from `payments`, so the OP_RETURN output's weight is never included [3](#0-2) .

The same stale `weight` (and, when a change output is added, `weight_with_change`, also computed without the data output) is used for the `MAX_STANDARD_TX_WEIGHT` check [4](#0-3) . The change-output decision also uses `fee_with_change` computed without the data output, so `needed_fee` returned to callers is systematically too low whenever `data` is present.

An OP_RETURN output carrying up to 80 bytes of pushdata (`TooMuchData` rejects 81+ bytes) adds roughly 8 (amount) + ~3 (script length/opcode) + 80 payload ≈ 91 non-witness bytes ≈ ~364 weight units and ~91 vbytes that are entirely unaccounted for.

### Impact Explanation
Two concrete consequences, both reachable by any caller of `SignableTransaction::new` supplying `data` (public input — arbitrary bytes the caller chooses, which the threshold signers then sign):

1. **Underpaid fee.** `needed_fee = fee_per_vbyte * vbytes` omits ~91 vbytes, so the actual fee rate of the broadcast transaction is strictly below the caller-specified `fee_per_vbyte`. The `TooLowFee` guard can pass while the real transaction's effective rate falls below the relay minimum, producing a transaction that fails to propagate and is lost — the direct analog of the report's "cross-chain deployment lost" outcome.
2. **Oversized non-standard transaction.** A transaction whose true weight is within ~364 WU above `MAX_STANDARD_TX_WEIGHT` passes the `TooLargeTransaction` check yet is rejected by every standard node on relay, stranding the spend after the threshold signature was produced.

### Likelihood Explanation
Requires a transaction to carry OP_RETURN `data` (supported explicitly by the API, per the `data` parameter and the 80-byte cap) and either a tight fee margin or a near-max-size transaction. The fee underpayment occurs deterministically on *every* transaction with data, so at least the underpayment is guaranteed whenever the feature is used; the standardness overflow additionally needs a large transaction.

### Recommendation
Compute weight/vbytes from the actual output set being constructed. Either pass `tx_outs` (including the OP_RETURN output) to `calculate_weight_vbytes`, or build the template transaction from the final `tx_outs` plus the candidate change output, so that `needed_fee`, the `TooLowFee` check, the change-dust decision, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the real serialized transaction.

### Proof of Concept
In `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs):

```rust
// data output is added to tx_outs (line ~194-202)
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) });
}

// but the estimate uses `payments`, NOT `tx_outs` (line 204)
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
// ...
// weight check uses the underestimate (line 241)
if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) { ... }
```

Concretely: construct inputs whose total leaves a transaction of weight `MAX_STANDARD_TX_WEIGHT - 100`, call `SignableTransaction::new(inputs, &payments, change, Some(vec![0u8; 80]), fee_per_vbyte)`. The returned `SignableTransaction` passes the weight check while the signed transaction's actual weight is `MAX_STANDARD_TX_WEIGHT + ~264` WU — unrelayable. Likewise `tx.fee() / tx.vsize()` after signing is measurably below the requested `fee_per_vbyte`, confirming the fee underestimate.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L85-94)
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

**File:** networks/bitcoin/src/wallet/send.rs (L225-243)
```rust
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
