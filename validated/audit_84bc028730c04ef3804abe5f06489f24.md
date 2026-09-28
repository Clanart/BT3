### Title
OP_RETURN data output omitted from transaction weight/fee accounting causes underpriced transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

`SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs` appends the `OP_RETURN` data output to `tx_outs` (lines 194-202) but computes the transaction weight and vbytes from `payments` only (line 204, and again at lines 225-226 for the change case). The `data` output — up to 80 bytes plus output overhead — is never counted in `calculate_weight_vbytes`, so `needed_fee` and the `TooLowFee` check are computed against an understated virtual size. The actual signed transaction is larger and therefore has a lower effective fee rate than `fee_per_vbyte` requested — an accounting desync between the "locked" inputs and the "unlocked" (charged) fee, analogous to the lock/unlock accounting corruption of CVE-2017-18221.

### Finding Description

The fee accounting is assembled asymmetrically:

1. `tx_outs` is populated from `payments`, then an `OP_RETURN` output carrying up to 80 bytes of caller-controlled `data` is pushed (send.rs:194-202).
2. `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` is called with `payments`, which does not contain the data output (send.rs:204). Inside `calculate_weight_vbytes`, the transaction's `output` list is built solely from `payments` plus optional `change` (send.rs:85-99).
3. `needed_fee = fee_per_vbyte * vbytes` and the `DEFAULT_MIN_RELAY_TX_FEE` floor check both use this understated `vbytes` (send.rs:206-213).
4. The change path repeats the same call (send.rs:225-226), so the change-amount computation `input_sat - payment_sat - fee_with_change` also ignores the data output's size.

Because `data` length is attacker-influenced input (it is an arbitrary `Vec<u8>` up to 80 bytes, gated only by `TooMuchData` at line 171), the undercount is up to roughly 90+ vbytes per transaction.

### Impact Explanation

The transaction that is actually signed and broadcast is larger than the one that was priced. Two concrete harms:

- At low `fee_per_vbyte`, the effective fee rate of the real transaction can fall below Bitcoin Core's default minimum relay fee even though `new()` passed its `TooLowFee` check. The transaction will be rejected by peer mempools or never confirm, while the selected inputs remain committed to this exact transaction (`Prevouts::All`, `TapSighashType::Default` in `TransactionSignMachine::sign`, send.rs:375-390). The signer machines produce shares bound to this underpriced TX, so funds are effectively held unspendable until a new signing round re-selects inputs — matching the "funds reported spendable that are not"/accounting-DoS class.
- When no change is created, the overage still lands in the fee, but the reported `needed_fee()`/`fee()` no longer reflects the requested rate, corrupting downstream accounting that assumes `fee ≈ fee_per_vbyte * vbytes`.

### Likelihood Explanation

Any caller that supplies `data` (e.g., a user-initiated out-instruction carrying metadata) triggers the discrepancy. The effect is deterministic — the miscount does not depend on racing or malicious validators, only on `data.is_some()`. Whether it causes a non-relayable transaction depends on the margin between `fee_per_vbyte` and the relay floor, so at common fee rates the transaction still relays; the accounting error is unconditional but the DoS outcome is conditional on tight fee margins.

### Recommendation

Include the `OP_RETURN` output in the weight/vbytes computation: either pass the fully assembled `tx_outs` (or `payments` plus the data output) into `calculate_weight_vbytes`, or add the data output's serialized size to the template transaction. Apply the same fix to the change-branch call so `fee_with_change` also accounts for the data output.

### Proof of Concept

```rust
// networks/bitcoin/src/wallet/send.rs logic reproduction:
// inputs = one ReceivedOutput of 100_000 sats, payments = one 50_000 sat payment,
// data = 80 bytes, fee_per_vbyte = 1, change = None.

// 1) SignableTransaction::new pushes the OP_RETURN output into tx_outs
//    (send.rs:194-202): tx now has 2 outputs.
// 2) calculate_weight_vbytes(tx_ins.len(), payments, None) (send.rs:204)
//    builds a template with only the payment output — the ~89 vbyte
//    OP_RETURN output is absent, so vbytes is understated by ~89.
// 3) needed_fee = 1 * vbytes is ~89 sats too low.
// 4) Actual broadcast TX (with OP_RETURN) has effective rate
//    needed_fee / actual_vbytes < 1 sat/vbyte, possibly below
//    DEFAULT_MIN_RELAY_TX_FEE, yet no TooLowFee error is raised because
//    that check at send.rs:211 also used the understated vbytes.
```

Asserting `SignableTransaction::fee() / actual_signed_tx_vbytes < fee_per_vbyte` whenever `data.is_some()` demonstrates the miscount without any private-key material. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Note: I verified the weight-calculation omission directly in `send.rs`. I did not fully confirm whether upstream callers can attach arbitrary `data` from unauthenticated external inputs within this snapshot, so the reachability assessment rests on `data` being an untrusted, caller-supplied field.

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

**File:** networks/bitcoin/src/wallet/send.rs (L194-213)
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-390)
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
```
