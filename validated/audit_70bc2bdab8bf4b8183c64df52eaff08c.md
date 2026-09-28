### Title
Fee and weight checks computed on a transaction missing the OP_RETURN output - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the Illuminate issue where a bound was enforced against the wrong quantity (`assets` instead of `shares`), `SignableTransaction::new` enforces the minimum-relay-fee and maximum-weight bounds against a synthetic transaction that omits the `OP_RETURN` data output, while the transaction that is actually signed includes it. The check is applied to a different object than the one being committed to.

### Finding Description
`SignableTransaction::new` appends the data output to `tx_outs` before computing the fee: [1](#0-0) 

but `calculate_weight_vbytes` is only passed `payments`, never the `OP_RETURN` output: [2](#0-1) 

The same omission occurs in the change path (`calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at line 226). Consequently:

- `needed_fee = fee_per_vbyte * vbytes` and the `DEFAULT_MIN_RELAY_TX_FEE` check are computed over `vbytes` that exclude the data output (up to ~90 extra bytes / ~90 vbytes of unaccounted size).
- The `weight > MAX_STANDARD_TX_WEIGHT` check at line 241 uses `weight` that excludes the data output.
- `fee()` and the change amount are derived from this underestimated `needed_fee`, so the transaction always pays exactly the underestimated fee: `input_sat - payment_sat - needed_fee` goes to change, leaving `fee() == needed_fee`.

### Impact Explanation
When `data` is supplied, the signed transaction's true feerate is `needed_fee / actual_vbytes`, strictly less than `fee_per_vbyte`. At the boundary `fee_per_vbyte = 1` sat/vB, the `TooLowFee` check passes on the underestimated size, yet the actual feerate falls below `DEFAULT_MIN_RELAY_TX_FEE`, producing a signed transaction that will not relay — outputs are committed to a plan that cannot confirm, and the coordinator's `Eventuality(txid)` will never be fulfilled. The `MAX_STANDARD_TX_WEIGHT` check can likewise pass a transaction that exceeds the standard limit once the data output is counted, signing an unbroadcastable transaction. This is a wrong-quantity bound check: the constraint is validated against inputs (payments) rather than the actual signed artifact, matching the shares-vs-assets class.

### Likelihood Explanation
`SignableTransaction::new` is the single construction path for all spends of the vault key in `networks/bitcoin`; any caller passing `data` (the API explicitly supports up to 80 bytes and tests exercise it) triggers the underestimation. It manifests whenever the requested `fee_per_vbyte` is within ~`80/tx_vbytes` of the relay floor, or when the transaction is near `MAX_STANDARD_TX_WEIGHT` (e.g., ~520-input consolidation transactions this design targets).

### Recommendation
Include the `OP_RETURN` output in the weight/vbytes estimation. Restructure `calculate_weight_vbytes` to take the full `tx_outs` list (payments + data output + optional change), or pass `data` through and push the `OP_RETURN` `TxOut` inside the estimator, so `needed_fee`, the minimum-fee check, and the `MAX_STANDARD_TX_WEIGHT` check are all evaluated on the transaction that is actually signed.

### Proof of Concept
In `SignableTransaction::new`, construct a transaction with `data = Some(vec![0; 80])` and `fee_per_vbyte = 1` (or the minimum passing value). `needed_fee` is `vbytes(without OP_RETURN)` sats; the signed `tx` includes the OP_RETURN output, so `tx.vsize()` exceeds the estimated `vbytes` by ~90, making the true feerate < 1 sat/vB — below `DEFAULT_MIN_RELAY_TX_FEE` — while `TooLowFee` was never raised. Concretely: `weight`/`vbytes` are computed from `payments` at `send.rs:204` after the data output was already pushed to `tx_outs` at `send.rs:195-201`, and the returned `SignableTransaction` embeds `tx_outs` (`send.rs:245-255`), so the fee bound was checked on different bytes than those committed to the sighash.

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
