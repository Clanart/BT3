### Title
Unchecked wrapping arithmetic on attacker-controlled payment amounts lets the multisig sign a consensus-invalid transaction (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The reported bug class is arithmetic performed without a safe-math library, allowing over/underflow. The same class exists in `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`, which sums payment amounts and computes fees with plain `u64` `sum()`, `+`, and `*` operations [1](#0-0) . These amounts originate from untrusted withdrawal/transfer instructions that an unprivileged user causes the threshold multisig to sign. Because Rust release builds wrap on overflow, a crafted set of payments whose total exceeds `u64::MAX` wraps `payment_sat` to a small value, defeating the `input_sat < payment_sat + needed_fee` solvency check [2](#0-1) .

### Finding Description
`SignableTransaction::new` computes `payment_sat` as an unchecked `sum::<u64>()` over caller-supplied payment amounts (line 187), `needed_fee` as `fee_per_vbyte * vbytes` (line 206), and the solvency check as `input_sat < (payment_sat + needed_fee)` (line 215), none of which are checked operations. The change calculation uses `input_sat.checked_sub(payment_sat + fee_with_change)`, but the inner `payment_sat + fee_with_change` is itself an unchecked addition that can wrap before the `checked_sub` ever runs (line 228) [3](#0-2) . The processor reaches this code via `Bitcoin::make_signable_transaction`, which passes plan payments (derived from user `InInstruction`s) straight into `BSignableTransaction::new` [4](#0-3) .

With `sum(payments)` wrapping to a small residue `r`, the check `input_sat < r + needed_fee` passes, the change computation `input_sat.checked_sub(r + fee_with_change)` succeeds with an inflated change value, and `Ok(SignableTransaction)` is returned for a transaction whose actual outputs vastly exceed its inputs. The threshold then produces a valid FROST signature over a sighash for a transaction Bitcoin consensus will never accept — a signature on an unintended message — and downstream `SignableTransaction::fee()` computes `sum(prevouts) - sum(outputs)` where the wrapped output sum produces a garbage fee that is subtracted from the instruction balance in `processor/src/multisigs/mod.rs:884`, panicking the node [5](#0-4) [6](#0-5) .

### Impact Explanation
An unprivileged user submits payment instructions whose amounts are individually valid `u64`s (each ≥ `DUST`) but sum past `2^64`. The multisig signs a consensus-invalid transaction (unintended message signing), and the processor either panics in `fee()`/`amount.0 -= tx.0.fee()` or permanently stalls the plan, halting processing of the multisig's queue — a liveness failure of the signing pipeline rather than a rejected error.

### Likelihood Explanation
Medium. Triggering requires only control of payment amounts summing over `u64::MAX` across the payments in one plan, which the scheduler can aggregate from multiple instructions; no key material, collusion, or privileged access is needed. Actual exploitation also depends on the build wrapping (default release behavior) rather than panicking on overflow.

### Recommendation
Use checked arithmetic throughout `SignableTransaction::new` and `fee()`: `checked_add`/`checked_sum` for `input_sat`/`payment_sat`, `checked_mul` for `fee_per_vbyte * vbytes`, and `checked_sub` for `fee()`, returning `TransactionError` variants on failure instead of wrapping or panicking [7](#0-6) .

### Proof of Concept
Construct a `Plan`/payments slice `[ (addr, u64::MAX - 100), (addr, u64::MAX - 100), (addr, 3000) ]` (each ≥ `DUST`) against inputs totaling e.g. 1 BTC. `payment_sat` wraps to `(2*u64::MAX - 200 + 3000) mod 2^64 = 2797`. The check `input_sat < 2797 + needed_fee` passes; `SignableTransaction::new` returns `Ok` with ~`2^65` satoshis of outputs against ~10^8 of inputs. `attempt_sign` then yields a FROST signature over this unspendable sighash, and `tx.fee()`/`instruction.balance.amount.0 -= tx.0.fee()` panics.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-234)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();

    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();

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

**File:** processor/src/multisigs/mod.rs (L883-885)
```rust
                if let Some(tx) = &tx {
                  instruction.balance.amount.0 -= tx.0.fee();

```
