### Title
OP_RETURN data output is excluded from weight/vsize and fee accounting, so the produced transaction pays less than the requested fee rate and evades the max-standard-weight check - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends the caller-supplied `OP_RETURN` output to `tx_outs` after computing the transaction weight/vbytes via `calculate_weight_vbytes`, which only models the payment outputs (and optionally change). The data-carrying output is therefore ignored — analogous to `msg.value` being dropped in a forwarded call — so `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check are computed over a transaction smaller than the one actually produced and signed.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` pushes the OP_RETURN output before the weight calculation, but `calculate_weight_vbytes` is invoked with only `payments` (and later `Some(&change)`), never including the OP_RETURN output: [1](#0-0) 

`calculate_weight_vbytes` builds a template `Transaction` containing only the payment `TxOut`s (plus a placeholder change output), so neither the OP_RETURN output's weight nor its script size is accounted for: [2](#0-1) 

The resulting `needed_fee` (`fee_per_vbyte * vbytes`) and the `weight > MAX_STANDARD_TX_WEIGHT` check both use this undercounted size: [3](#0-2) [4](#0-3) 

The actual signed transaction (with the OP_RETURN output included) is what `fee()` and consensus see: the absolute fee paid equals `needed_fee`, but it is spread over a larger real vsize, so the *effective* fee rate is strictly below the `fee_per_vbyte` the caller requested, and a transaction that is actually over the standard weight limit passes the size check.

### Impact Explanation
- The produced transaction's real fee rate is lower than requested. If `fee_per_vbyte` was chosen near `DEFAULT_MIN_RELAY_TX_FEE`, the actual transaction can fall below the node's minimum relay fee and be rejected/never propagate — funds are committed as inputs yet the signed transaction is unusable without re-signing.
- A transaction whose real weight exceeds `MAX_STANDARD_TX_WEIGHT` can pass the `TooLargeTransaction` check and be fully FROST-signed into a non-standard transaction that Bitcoin nodes will not relay or mine, burning the signing session and leaving the payment unexecuted.

### Likelihood Explanation
`SignableTransaction::new` takes `data: Option<Vec<u8>>` as a public argument; the OP_RETURN path is exercised whenever `data` is `Some`. The miscount is deterministic and proportional to the data length. Reachability requires an in-protocol caller to pass `Some(data)`; `processor/src/networks/bitcoin.rs::make_signable_transaction` currently passes `None`, so impact materializes only when a caller uses the data field — hence Medium rather than High. The oversized transaction is rejected only at broadcast time (after a completed threshold signature), so the failure mode is lost fee intent / unbroadcastable signed transaction rather than direct theft.

### Recommendation
Include the OP_RETURN output (and any other non-payment outputs) in `calculate_weight_vbytes` — e.g., pass the fully constructed `tx_outs` (or the data length) into the weight calculation so `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check reflect the transaction that is actually signed:

```rust
// compute weight over payments + OP_RETURN (+ change when present)
let (mut weight, vbytes) =
  Self::calculate_weight_vbytes(tx_ins.len(), &tx_outs_scripts_and_values, None);
```

Alternatively, add the OP_RETURN output to the payments slice used for sizing (its script length is fixed by `data.len()`), and re-derive `weight`/`vbytes` whenever `data.is_some()`.

### Proof of Concept
1. Call `SignableTransaction::new(inputs, payments, None, Some(vec![0u8; 80]), fee_per_vbyte)` with `fee_per_vbyte` set so `needed_fee` just passes the `DEFAULT_MIN_RELAY_TX_FEE` check.
2. The returned `SignableTransaction` contains `payments.len() + 1` outputs including an ~89-weight-unit OP_RETURN output, while `needed_fee` was computed for a transaction without it.
3. Complete signing via `multisig`/`preprocess`/`sign`/`complete` in `send.rs`; broadcast the result. Its real vsize is larger, so its effective fee rate is below `fee_per_vbyte` and potentially below the relay minimum — the transaction is rejected despite passing all internal checks. Similarly, crafting `payments`/`data` so real weight exceeds `MAX_STANDARD_TX_WEIGHT` while the computed `weight` does not, yields a signed non-standard transaction that nodes refuse.

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

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
