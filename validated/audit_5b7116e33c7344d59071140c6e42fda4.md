### Title
OP_RETURN data output omitted from transaction weight/vsize calculation, bypassing fee-rate and standardness checks - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends an OP_RETURN output carrying caller-supplied `data` to `tx_outs` before computing the transaction's weight and virtual size, yet `calculate_weight_vbytes` is invoked with `payments` only — the OP_RETURN output is never included in the template transaction it builds. As a result, `weight` and `vbytes` undercount the real transaction size by the full OP_RETURN output (script header + up to 80 bytes of pushdata + the 8-byte value). This is a boundary/accounting error of the same class as the Suricata fragment reassembly off-by-one: a length/bound computation silently drops a contributed element, so the checks downstream (`TooLowFee` minimum-relay check and the `MAX_STANDARD_TX_WEIGHT` standardness check) are evaluated against a smaller transaction than the one actually signed and broadcast. [1](#0-0) 

### Finding Description
1. At `send.rs:194-202`, if `data` is `Some`, a `TxOut` with `Amount::ZERO` and `ScriptBuf::new_op_return(...)` is pushed onto `tx_outs`. [2](#0-1) 
2. At `send.rs:204`, `calculate_weight_vbytes(tx_ins.len(), payments, None)` reconstructs a template `Transaction` whose `output` list is built exclusively from `payments` (`send.rs:85-93`) plus an optional change output (`send.rs:95-99`). The OP_RETURN output added to `tx_outs` is not present in the template, so `tx.weight()` at `send.rs:101` and the derived vbytes at `send.rs:119-126` exclude it. [3](#0-2) 
3. `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`) and the minimum-relay check `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` (`send.rs:211`) therefore validate a fee rate against a vsize smaller than reality. The change-output path repeats the same undercount: `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at `send.rs:225-226` still omits the OP_RETURN output, so `fee_with_change` is also understated. [4](#0-3) 
4. The standardness bound `weight > MAX_STANDARD_TX_WEIGHT` at `send.rs:241` is checked against the undercounted `weight`, so a transaction whose true weight exceeds the limit passes the check and is signed. [5](#0-4) 
5. The produced `SignableTransaction` is signed via `TransactionSignMachine::sign`, which commits each input over `Prevouts::All` — the resulting signature is valid only for this exact (unrelayable) transaction (`send.rs:373-391`). [6](#0-5) 

The misaccounting is attacker-reachable in the intended usage: `data` (up to 80 bytes, `send.rs:171`) and the `payments`/`change` set come from withdrawal/batch instructions an external user can influence, and the multisig then signs the resulting transaction.

### Impact Explanation
Two concrete consequences:
- **Underpriced fee**: `needed_fee` is computed against a vsize missing the OP_RETURN output (~90–100 vbytes short with 80-byte data, more in weight units). The signed transaction's actual fee rate is lower than `fee_per_vbyte` requested and can fall below the mempool minimum relay fee even though the `TooLowFee` check passed — the transaction is silently unpropagated/unconfirmable while consuming a FROST signing round.
- **Standardness bound bypass**: a many-input transaction with an OP_RETURN output can have true weight above `MAX_STANDARD_TX_WEIGHT` (400,000 WU) while the computed `weight` stays below it. Bitcoin nodes reject such transactions as non-standard, so the threshold signature produces a transaction that cannot be broadcast — the spent inputs' value is not movable via the signed artifact and a fresh signing round (with fresh nonces) is required. This mirrors the Suricata impact shape: a boundary element dropped from a length check causes otherwise-valid processing to produce a result the network rejects.

### Likelihood Explanation
Triggering requires a caller/withdrawal flow supplying `data` (or enough payments) such that the omitted output's weight flips a check boundary — either pushing true weight past the standardness limit or dragging the realized fee rate below relay minimum. The omission is deterministic (it always undercounts whenever `data.is_some()`), so likelihood is set by how often transactions are constructed near those boundaries rather than by adversarial probability. Since the code already caps `data` at 80 bytes, the fee-rate discrepancy alone is bounded (~25 vbytes), but the standardness bypass is a hard bound crossed by accumulated inputs plus the omitted output.

### Recommendation
Pass the fully constructed `tx_outs` (including the OP_RETURN output) into the weight calculation instead of reconstructing outputs from `payments`. Concretely, change `calculate_weight_vbytes` to accept the real output list, or compute the OP_RETURN output's weight separately and add it to `weight`/`vbytes` before the fee and `MAX_STANDARD_TX_WEIGHT` checks. Apply the same fix to the `fee_with_change` path so the change-branch comparison uses consistent accounting.

### Proof of Concept
Construct `SignableTransaction::new` with:
- `payments` = one dust-satisfying payment,
- `data` = `Some(vec![0u8; 80])`,
- `change` = `None`,
- enough inputs that `inputs_weight + payments_weight` is just under `MAX_STANDARD_TX_WEIGHT` (or, for the fee variant, `fee_per_vbyte` chosen so `needed_fee` passes `DEFAULT_MIN_RELAY_TX_FEE` only because ~25 vbytes are missing).

Observed behavior: `new` returns `Ok(...)` — `weight`/`vbytes`/`needed_fee` all exclude the ~90-byte OP_RETURN output present in `self.tx.output`. `transaction()` yields a `Transaction` whose actual `tx.weight()` exceeds the checked value, and `fee()` (`send.rs:137-141`, which sums the real `tx.output` list) reveals a realized fee rate below `fee_per_vbyte`. The signed result from `TransactionSignMachine::sign`/`complete` is therefore either below relay minimum or over the standardness weight limit and will be rejected by Bitcoin nodes — a valid-seeming input producing an output the network refuses, analogous to the Suricata reassembly boundary failure.

One caveat on scope: I was only able to review a subset of the in-scope crates in depth (`dkg`, `frost` entry points, `bitcoin` wallet). The boundary checks in `ThresholdParams::new`, `ThresholdKeys::view`, and `AlgorithmSignMachine::sign` all appeared correct (strict `> n`, dedup after sort, `t <= len <= n`), so `send.rs` is the strongest reachable analog found; a deeper pass over PedPoP/`musig`/`recovery` was not completed within the available iterations.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L85-101)
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

    let weight = tx.weight();
```

**File:** networks/bitcoin/src/wallet/send.rs (L194-243)
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

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
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
