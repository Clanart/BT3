### Title
OP_RETURN `data` output excluded from fee/weight calculation causes the constructed transaction to pay a lower effective fee rate than specified — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The external report describes a class where the amount *credited/accounted* exceeds the amount *actually received* because a fee is silently taken out of the transfer. The analogous shape in `bitcoin-serai` is in `SignableTransaction::new`: the transaction's actual weight exceeds the weight the fee calculation accounted for, because the OP_RETURN `data` output is appended to the transaction *before* — but *not included in* — the vsize computation. The builder therefore believes the transaction achieves the requested `fee_per_vbyte` rate when it does not. [1](#0-0) 

### Finding Description
`SignableTransaction::new` pushes the OP_RETURN output into `tx_outs` at lines 194–202, then calls `calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204. `calculate_weight_vbytes` builds a mock transaction containing only `payments` (and optionally `change`); it never receives the `data` argument, so the up-to-80-byte OP_RETURN output contributes nothing to `weight`/`vbytes`. [2](#0-1) 

Consequences:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206) underestimates the true required fee by `fee_per_vbyte * (~9 + data.len())` vbytes — up to ~89 vbytes unaccounted.
2. The `TooLowFee` guard at line 211 compares against `DEFAULT_MIN_RELAY_TX_FEE * vbytes` — again the *underestimated* vbytes — so a transaction can pass the minimum-relay-fee check while its real feerate is below the relay minimum. [3](#0-2) 
3. The same unaccounted output is omitted from the `MAX_STANDARD_TX_WEIGHT` check at line 241 and from the `fee_with_change` computation at lines 225–232, meaning `change` may be calculated against an understated fee (slightly overpaying change/underpaying fee), and an oversized transaction can pass the standardness check. [4](#0-3) 

Like the fee-on-transfer bug — where the pool credits the nominal amount while a smaller amount actually arrives — the protocol here accounts a nominal feerate while the actual transaction delivers a strictly lower one. `fee()` (lines 138–141) reports the nominal `inputs − outputs` fee, masking the shortfall because the *denominator* (vsize) was wrong, not the fee value itself. [5](#0-4) 

### Impact Explanation
A `SignableTransaction` built with `data: Some(_)` pays an effective feerate strictly below the caller-specified `fee_per_vbyte`. When `fee_per_vbyte` is at or near the relay minimum (the only enforced floor), the resulting signed transaction's real feerate falls below `DEFAULT_MIN_RELAY_TX_FEE` and will be rejected by Bitcoin nodes' mempool policy. For the Serai processor, a non-relaying withdrawal/forward transaction means funds are committed (inputs consumed in the plan, signatures produced via `TransactionMachine`) but the payment is never delivered — the exact "funds believed moved that are not spendable/delivered" failure mode. Even above the relay floor, the transaction silently pays less than the intended feerate, degrading confirmation priority in a way the `needed_fee`/`fee()` accessors cannot reveal. [6](#0-5) 

### Likelihood Explanation
The defect is unconditional in code: any caller passing `data` hits it. Its production reachability depends on whether `SignableTransaction::new` is invoked with `data: Some` — the single processor call site is in `processor/src/networks/bitcoin.rs` (I did not confirm whether it ever supplies `data`; inbound `data` is extracted via `extract_serai_data`, which is a receive-side path). If `data` is always `None` in production today, this is a latent bug in a public API rather than an actively exploitable path, which lowers likelihood to Medium. The miscalculation itself requires no attacker sophistication — just a non-None `data` argument reaching the constructor.

### Recommendation
Include the OP_RETURN output in the weight/vsize estimate. Either pass the constructed `data` output (or `tx_outs` including the OP_RETURN) into `calculate_weight_vbytes`, or compute the weight from the fully assembled `Transaction` once `tx_outs` is finalized — e.g., build the candidate `tx` first (with and without change) and derive `weight`/`vbytes` from it directly, ensuring the `TooLowFee`, `NotEnoughFunds`, change, and `MAX_STANDARD_TX_WEIGHT` checks all use the true size.

### Proof of Concept
```rust
// networks/bitcoin context; assumes `output` is a scanned ReceivedOutput.
let data = vec![0u8; 80];
let fee_per_vbyte = 1; // sat/vB

// This succeeds: TooLowFee is checked against vbytes that exclude the OP_RETURN.
let tx = SignableTransaction::new(
    vec![output],
    &payments,          // payments summing to input - needed_fee
    None,
    Some(data),
    fee_per_vbyte,
).unwrap();

// tx.needed_fee() == 1 * vsize_without_opreturn
// tx.transaction().vsize() includes the ~89-vbyte OP_RETURN output.
// effective_feerate = tx.fee() / tx.transaction().vsize() < 1 sat/vB
// => below DEFAULT_MIN_RELAY_TX_FEE; the signed TX is rejected by node mempools
//    even though the builder believed it met the minimum.
```

Caveat: I was unable to verify whether the production call site in `processor/src/networks/bitcoin.rs` ever passes `data: Some(_)` to `SignableTransaction::new`; if it always passes `None`, this finding is a latent library defect reachable only through the public `data` parameter rather than through an active processor path.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L62-99)
```rust
  fn calculate_weight_vbytes(
    inputs: usize,
    payments: &[(ScriptBuf, u64)],
    change: Option<&ScriptBuf>,
  ) -> (u64, u64) {
    // Expand this a full transaction in order to use the bitcoin library's weight function
    let mut tx = Transaction {
      version: Version(2),
      lock_time: LockTime::ZERO,
      input: vec![
        TxIn {
          // This is a fixed size
          // See https://developer.bitcoin.org/reference/transactions.html#raw-transaction-format
          previous_output: OutPoint::default(),
          // This is empty for a Taproot spend
          script_sig: ScriptBuf::new(),
          // This is fixed size, yet we do use Sequence::MAX
          sequence: Sequence::MAX,
          // Our witnesses contains a single 64-byte signature
          witness: Witness::from_slice(&[vec![0; 64]])
        };
        inputs
      ],
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

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
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

**File:** networks/bitcoin/src/wallet/send.rs (L223-243)
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
    }

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-398)
```rust
    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
  }
```
