### Title
`SignableTransaction::new` omits the OP_RETURN data output from weight/fee calculation, producing transactions below the intended fee rate — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to a hardcoded-zero `minGasLimit` causing bridged messages to revert, `SignableTransaction::new` computes transaction weight and `needed_fee` from `payments` only, after already pushing a data-carrying `OP_RETURN` output into `tx_outs`. The actual signed transaction is larger than the transaction the fee was priced for, so the effective fee rate is lower than `fee_per_vbyte` and can fall below the Bitcoin minimum relay fee, causing the transaction to be rejected by the network.

### Finding Description
`SignableTransaction::new` accepts an optional `data: Option<Vec<u8>>` (up to 80 bytes) and appends an `OP_RETURN` output to `tx_outs` before computing fees [1](#0-0) . However, the weight/vbyte computation calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, passing `payments` — which does not include the OP_RETURN output [2](#0-1) . `calculate_weight_vbytes` builds a template transaction whose outputs are exactly `payments` plus optional change, so the ~10–91 vbytes of the OP_RETURN output are never counted [3](#0-2) .

The minimum-fee check and `needed_fee` are therefore computed against an underestimated `vbytes` [4](#0-3) . The same omission applies to the change path (`calculate_weight_vbytes(..., Some(&change))` still passes `payments` without the data output) and to the `MAX_STANDARD_TX_WEIGHT` check [5](#0-4) . Since `fee()` is defined as `sum(inputs) - sum(outputs)` and the OP_RETURN carries zero value, the absolute fee `needed_fee` is what gets paid, but it is spread over a transaction that is actually larger — the effective sat/vB is strictly below the caller's requested `fee_per_vbyte` [6](#0-5) .

### Impact Explanation
A transaction constructed with a `data` payload silently pays a lower fee rate than requested. If `fee_per_vbyte` is chosen at or near the minimum relay bound, the real transaction can fall below `DEFAULT_MIN_RELAY_TX_FEE` per-vbyte and be rejected by Bitcoin relay policy — the same operational failure class as the reported zero `minGasLimit` bridged-transaction revert: a security-relevant lower bound is computed against incomplete parameters, so the transaction fails instead of confirming. Additionally, the `MAX_STANDARD_TX_WEIGHT` bound can be exceeded by up to the size of the OP_RETURN output without `TooLargeTransaction` firing, since `weight` is also underestimated.

### Likelihood Explanation
This triggers on every `SignableTransaction` built with non-empty `data` — a deterministic miscalculation, not a probabilistic edge case. Whether the transaction is actually refused depends on how much headroom `fee_per_vbyte` had over the minimum; at higher fee rates the transaction still confirms but at an effective rate ~1–3% lower than intended (up to ~91 vbytes unpriced). It is a genuine accounting bug reachable purely through the public `SignableTransaction::new` API inputs (`data`), though the externally visible harm (relay rejection) is conditional on operating near minimum fee.

### Recommendation
Include the OP_RETURN output in the weight/vbyte template before computing fees: build `tx_outs` (payments + optional OP_RETURN) first, then pass a representation that accounts for all outputs to `calculate_weight_vbytes` — e.g., compute the OP_RETURN script size and add its fixed `TxOut` overhead plus script length to `weight`/`vbytes`, or restructure `calculate_weight_vbytes` to take the full output list (payments + OP_RETURN + change). Recompute `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check against the complete transaction.

### Proof of Concept
Conceptual trace over `crypto`/`networks/bitcoin` code (no external deps needed to confirm the arithmetic):

1. Call `SignableTransaction::new(inputs, payments, None, Some(vec![0u8; 80]), fee_per_vbyte)` where `inputs` cover `payments + fee` at exactly `fee_per_vbyte` for the payments-only size.
2. The OP_RETURN `TxOut` (`Amount::ZERO`, ~93-byte `script_pubkey` for 80 bytes of data) is pushed to `tx_outs` at send.rs:194–202.
3. `calculate_weight_vbytes(tx_ins.len(), payments, None)` at send.rs:204 builds a `Transaction` whose `output` list is only `payments` — the OP_RETURN output's ~100+ WU is absent, so `vbytes` and `needed_fee = fee_per_vbyte * vbytes` are understated.
4. The final `tx` returned at send.rs:245–251 contains all outputs including the OP_RETURN, but `needed_fee` was priced for the smaller transaction.
5. `fee()` = `input_sat - payment_sat` = `needed_fee`, but the real vsize is larger, so `fee / actual_vbytes < fee_per_vbyte`. Setting `fee_per_vbyte` just above the `DEFAULT_MIN_RELAY_TX_FEE` floor at send.rs:211 yields a transaction that passes the check yet would be under the minimum relay rate once broadcast.

Note: this assessment is based on the indexed code; I could not inspect the processor-side callers to confirm how `data` and `fee_per_vbyte` are chosen in production, which bounds the real-world exploitability of the fee shortfall.

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

**File:** networks/bitcoin/src/wallet/send.rs (L204-213)
```rust
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
