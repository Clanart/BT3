### Title
`SignableTransaction::new` omits the OP_RETURN output from weight/fee calculation, so `needed_fee`, the change amount basis, and the min-relay check are computed against a smaller transaction than the one actually signed - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The bug class from the external report — a returned/accounted amount derived from an estimate of state that does not reflect the actual operation — maps onto `SignableTransaction::new`. The fee and weight are computed by `calculate_weight_vbytes`, which is called with the `payments` list only (networks/bitcoin/src/wallet/send.rs:204 and send.rs:226). However, when `data` is supplied, an additional OP_RETURN output is pushed into `tx_outs` (send.rs:194-202) *before* the weight call but is never included in `payments`, so the fee-rate computation, the `TooLowFee` minimum-relay check (send.rs:211), the `MAX_STANDARD_TX_WEIGHT` check (send.rs:241), and the change-vs-fee split (send.rs:224-235) are all derived from a transaction that is smaller than the one the FROST multisig actually signs. [1](#0-0) 

### Finding Description
`SignableTransaction::new` builds the real output vector `tx_outs` including an OP_RETURN output carrying up to 80 bytes of `data` (send.rs:171, send.rs:194-202). It then calls `calculate_weight_vbytes(tx_ins.len(), payments, None)` — note `payments`, not `tx_outs` — so the OP_RETURN output (roughly 9 + data_len serialized bytes, ~100-380 weight units, i.e. ~25-95 vbytes) is excluded from `vbytes` (send.rs:204). [2](#0-1) 

When a `change` script is provided, the same omission occurs in `fee_with_change` (send.rs:225-227), and the change output value is set to `input_sat - payment_sat - fee_with_change` (send.rs:228-230). Because the actual fee is implicitly `inputs - outputs` (send.rs:138-141), the signed transaction pays exactly the underestimated `fee_with_change` — meaning the *effective* fee rate is lower than the `fee_per_vbyte` the caller requested and committed to. [3](#0-2) 

Worse, the minimum-relay guard compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the underestimated `vbytes` (send.rs:211), and `calculate_weight_vbytes` also undercounts for the `TooLargeTransaction` check (send.rs:241). A caller requesting exactly the minimum relay rate while attaching ~80 bytes of data produces a signed transaction whose true fee rate is below 1 sat/vbyte — below Bitcoin Core's default minimum relay fee — so it will not propagate or confirm. [4](#0-3) 

### Impact Explanation
`needed_fee()` is documented as "the fee necessary for this transaction to achieve the fee rate specified at construction" (send.rs:129-135), but the signed transaction achieves a strictly lower rate whenever `data` is present. The transaction is finalized by `TransactionSignatureMachine::complete` with BIP-341 key-spend signatures over the real outputs (send.rs:373-397, send.rs:417-428), so the mismatch is baked into a fully signed, consensus-valid transaction: the vault has spent its inputs and created outputs that cannot be mined at the intended relay rate. Accounting that relies on `needed_fee()`/`fee()` — e.g., a processor recording the fee it expects a completed eventuality to have paid — will be wrong, mirroring the original report's incorrect `amountBought`. The funds are recoverable only by re-signing a replacement transaction, which for a threshold multisig is a full new signing ceremony. [5](#0-4) 

### Likelihood Explanation
Reachable by any unprivileged party who can cause a Serai Bitcoin send carrying `data` (an OP_RETURN payload up to the 80-byte `TooMuchData` limit): such a user triggers `SignableTransaction::new` with `data.is_some()` and `change.is_some()`, which is the normal shape of protocol sends that both carry an instruction payload and return leftover inputs to a registered change offset (see the change-offset registration path in networks/bitcoin/src/wallet/mod.rs:180-196 and its use in tests at networks/bitcoin/tests/wallet.rs:230-241). The miscalculation is deterministic on every such call — no race or privileged access required. The severity is bounded (Medium): the fee is underpaid rather than overpaid, inputs aren't burned, and the check still overestimates safety in the no-change case — but it reliably produces under-rate or non-relaying transactions whenever data and change coexist. [6](#0-5) 

### Recommendation
Pass the complete `tx_outs` (including the OP_RETURN output) — not `payments` — into `calculate_weight_vbytes`, or push the data output into the payments list used for sizing before both fee computations at send.rs:204 and send.rs:226. Additionally, since the change output's value is fixed size, compute the final transaction's weight once after all outputs are known rather than maintaining two parallel estimates.

### Proof of Concept
```rust
// Conceptual: in SignableTransaction::new, with one input and one payment:
let data = vec![0u8; 80]; // 80-byte OP_RETURN payload, passes the TooMuchData check
let tx = SignableTransaction::new(
    inputs,                       // inputs worth payment + fee + dust-sized change
    &[(payment_script, 1000)],
    Some(change_script),          // change path exercises fee_with_change
    Some(data),                   // ~89-byte OP_RETURN output excluded from sizing
    1,                            // minimum relay rate: 1 sat/vbyte
).unwrap();

// tx.weight() (real, signed tx) exceeds the weight used internally by
// ~89 non-witness bytes (~356 WU / ~89 vbytes).
// tx.fee() == fee_with_change was sized for the smaller tx, so
// tx.fee() * 1000 / tx.weight().to_vbytes() < 1 sat/vbyte ->
// below DEFAULT_MIN_RELAY_TX_FEE despite passing the TooLowFee check,
// and needed_fee() reports an understated figure to callers.
```
The defect is visible purely in the code path: `tx_outs.push(TxOut { ... new_op_return ... })` (send.rs:195-201) executes before `calculate_weight_vbytes(tx_ins.len(), payments, None)` (send.rs:204), and `payments` never contains the data output.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L62-94)
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
```rust
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
  }
```
