### Title
Fee/weight estimation omits the OP_RETURN data output, producing underpriced or unbroadcastable transactions and silently overpaying change — (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
The original bug class is "the estimation step uses different parameters than the execution step, so the computed amount diverges from reality and leftover value is stranded/lost." The direct analog in Serai's in-scope code is `SignableTransaction::new` / `calculate_weight_vbytes` in `networks/bitcoin/src/wallet/send.rs`: the weight and fee of the transaction are computed from `payments` only, while the actual signed transaction additionally contains the OP_RETURN `data` output. The quoted/executed mismatch means the real transaction is larger than the one whose weight was priced.

### Finding Description
`SignableTransaction::new` builds `tx_outs` from `payments` and then appends an OP_RETURN output when `data` is present (`send.rs:194-202`). However, both weight estimations call `Self::calculate_weight_vbytes(tx_ins.len(), payments, ...)` (`send.rs:204`, `send.rs:225-226`), and `calculate_weight_vbytes` constructs the template transaction's output list exclusively from `payments` (`send.rs:85-93`) — the `data` output is never included. Consequences:

1. `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`, `send.rs:227`) is computed on a transaction that is up to ~80 bytes (≈ up to ~90 vbytes for a max-size OP_RETURN) smaller than the real one. The signed transaction therefore pays a lower sat/vbyte rate than requested.
2. The `TooLowFee` minimum-relay check (`send.rs:211-213`) is evaluated against the underestimated vbytes, so a transaction can pass the check while its true fee rate is below `DEFAULT_MIN_RELAY_TX_FEE`, making it non-relayable by default policy.
3. The change decision (`send.rs:224-234`) uses `fee_with_change` computed without the data output. The change output receives `input_sat - payment_sat - fee_with_change`, i.e. the same satoshis are sent to change regardless of the larger real transaction — the discrepancy is silently absorbed as additional fee and, more importantly, the effective fee rate is wrong. Since signing commits via `Prevouts::All` (`send.rs:375`) and `TapSighashType::Default`, the resulting signature cannot be patched; the entire FROST signing round produces an artifact whose fee is not the intended one.

### Impact Explanation
A transaction produced by a completed threshold signing ceremony can be unbroadcastable (real fee rate below the minimum relay fee) or pay an unintended fee, mirroring the report's "quote says X, execution costs Y, the difference is lost/stranded." While not permanent loss of funds (the inputs remain spendable by re-signing), the signed transaction is unusable, wasting a distributed signing round and stalling whatever protocol operation depended on it. The value difference (fee mispricing) is not recoverable from the signed artifact.

### Likelihood Explanation
Reachable whenever `data` is `Some` and `fee_per_vbyte` is at/near the protocol minimum, or when precision matters. `data` carries protocol payloads up to 80 bytes; if an unprivileged party's action (e.g., a deposit/registration flow) influences the data length or triggers transaction construction at minimum fee rates, they can push the real fee rate below relay minimum. The bug is deterministic — it does not depend on adversarial timing, only on `data.is_some()`.

### Recommendation
Include the OP_RETURN output in `calculate_weight_vbytes` (e.g., pass the full intended `tx_outs`/data length into the estimation, or price `data.len()` explicitly), and re-check `TooLowFee` and the change/leftover computation against the final, complete output set — i.e., use the same parameters for the estimate as for execution, per the original report's mitigation.

### Proof of Concept
1. Call `SignableTransaction::new` with one input of 100,000 sats, `payments = [(script, 50_000)]`, `data = Some(vec![0u8; 80])`, `change = Some(change_script)`, and `fee_per_vbyte` chosen so `fee_per_vbyte * vbytes` equals exactly `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`.
2. The `TooLowFee` check at `send.rs:211` passes because `needed_fee` meets the minimum for the underestimated `vbytes`.
3. The resulting transaction is ~90 vbytes larger than estimated (OP_RETURN output ≈ `data.len() + ~11` bytes of script+amount overhead). Its actual fee rate is `needed_fee / real_vbytes < minimum`, so Bitcoin Core nodes will not relay it; the FROST-completed transaction (`TransactionSignMachine::sign` → `complete`) commits to this fee via `Prevouts::All` and cannot be altered, yielding an unusable signed transaction — the analog of collateral stranded after a misquoted swap. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** networks/bitcoin/src/wallet/send.rs (L194-235)
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
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-391)
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
```
