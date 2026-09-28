### Title
`SignableTransaction::new` omits the OP_RETURN output from the fee/weight calculation, under-allocating the transaction fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
When a caller supplies `data`, an OP_RETURN output is appended to `tx_outs`, but the weight/vbyte estimate used to derive `needed_fee` is computed only from `inputs`, `payments`, and `change` — the OP_RETURN output is never included in `calculate_weight_vbytes`. The transaction therefore pays a lower effective fee rate than requested, analogous to the report's "accounting total doesn't match what's actually transferred/committed" under-allocation bug class.

### Finding Description
`SignableTransaction::new` builds `tx_outs` including the OP_RETURN output, then calls `calculate_weight_vbytes(tx_ins.len(), payments, None)`, which reconstructs a transaction containing only the payment outputs (plus optional change). The OP_RETURN bytes (up to 80 bytes of data plus output overhead, roughly 13–100 vbytes) are excluded from `weight`/`vbytes`, so `needed_fee = fee_per_vbyte * vbytes` under-charges. The change amount is then computed as `input_sat - payment_sat - fee_with_change`, which is also based on the underestimated weight. The `TooLowFee` sanity check uses the same underestimated `vbytes`, so it cannot catch the shortfall. [1](#0-0) [2](#0-1) 

### Impact Explanation
The signed transaction is larger than the estimate by the size of the OP_RETURN output, so the real fee rate is `needed_fee / actual_vsize < fee_per_vbyte`. Consequences:

- A transaction intended to meet a target fee rate underpays miners; near relay/mining thresholds it can be evicted or never confirm, leaving the inputs' value unspendable through this plan until a replacement is built (the UTXOs remain locked against this plan's completion).
- The `TooLowFee` guard can pass while the real transaction falls below `DEFAULT_MIN_RELAY_TX_FEE` per actual vbyte, producing a non-relayable transaction.

Since `fee() = sum(inputs) - sum(outputs)` is correct, the excess simply becomes additional fee — the harm is the fee-rate shortfall, not direct loss. Rated Medium: public caller-controlled `data` input triggers it, but impact is a stuck/non-standard transaction rather than permanent loss.

### Likelihood Explanation
Reachable by any caller passing `Some(data)` to `SignableTransaction::new` with a `fee_per_vbyte` near the minimum. The in-tree processor currently passes `None` for `data` ( [3](#0-2) ), so the buggy path is only exercised by other consumers of `bitcoin_serai::wallet::SignableTransaction`.

### Recommendation
Include the OP_RETURN output in the weight estimate — e.g., extend `calculate_weight_vbytes` to accept the full `tx_outs` (or the `data` length), and pass the post-OP_RETURN output list for both the no-change and with-change estimates:

```rust
// in SignableTransaction::new, after pushing the OP_RETURN output:
let (mut weight, vbytes) =
    Self::calculate_weight_vbytes(tx_ins.len(), &tx_outs_as_scripts_amounts(), None);
```
Alternatively, construct the candidate `Transaction` once (including OP_RETURN) and reuse it inside `calculate_weight_vbytes` so the estimate can never diverge from the real output set.

### Proof of Concept
```rust
// A transaction with one input and an 80-byte OP_RETURN, no payments, no change
let inputs = vec![output]; // a ReceivedOutput worth V sats
let data = vec![0u8; 80];
let tx = SignableTransaction::new(inputs, &[], None, Some(data), FEE).unwrap();

// The produced transaction contains the OP_RETURN output, increasing its vsize
// by ~90+ vbytes, yet needed_fee was computed from the 1-input/0-output shell.
// Therefore tx.fee() / tx.transaction().vsize() < FEE, and for FEE near the
// minimum relay rate the real feerate can fall below DEFAULT_MIN_RELAY_TX_FEE.
```

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L193-212)
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

**File:** processor/src/networks/bitcoin.rs (L446-452)
```rust
    match BSignableTransaction::new(
      inputs.iter().map(|input| input.output.clone()).collect(),
      &payments,
      change.clone().map(Into::into),
      None,
      fee.0,
    ) {
```
