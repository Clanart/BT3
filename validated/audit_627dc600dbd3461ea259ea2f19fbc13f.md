### Title
Unchecked u64 arithmetic overflow in payment/fee summation allows a remote denial of service via a crafted payment amount - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` sums attacker-influenced payment amounts and multiplies an attacker-influenced fee rate using plain `+` and `*` on `u64`. A payment amount at or near `u64::MAX` (which passes the only check, `amount >= DUST`) causes `payment_sat + needed_fee` (and `payment_sat` summation itself across multiple payments) to overflow, panicking in debug builds and wrapping in release builds — where the wrap lets the insufficient-funds check pass and produces a transaction whose outputs exceed its inputs, which then panics on subtraction underflow in `SignableTransaction::fee`. [1](#0-0) [2](#0-1) 

### Finding Description
The Ghostscript report's bug class is a memory-safety fault reachable by a crafted input file, yielding denial of service. Serai is memory-safe Rust, so the concrete analog of "crafted bytes drive the parser/computation out of bounds" is arithmetic overflow on unchecked `u64` operations reachable from public transaction/payment data.

In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150`):

- Line 165-169 only validates `*amount < DUST` (`DUST = 546`, line 32). There is no upper bound on a payment amount.
- Line 187: `let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();` — summing `u64` amounts overflows for adversarial inputs (e.g., two payments of `2^63`).
- Line 206: `let mut needed_fee = fee_per_vbyte * vbytes;` — unchecked multiplication.
- Line 215: `if input_sat < (payment_sat + needed_fee)` — addition overflow; the sole guard against spending more than the inputs.
- Line 228: `input_sat.checked_sub(payment_sat + fee_with_change)` — the inner addition overflows before `checked_sub` can help.
- Line 137-141, `fee()`: `sum(inputs) - sum(outputs)` panics on underflow if an overflowing construction slipped through.

In debug builds every one of these panics. In release builds the wraparound makes `input_sat < wrapped_value` false, so a `SignableTransaction` is produced whose declared outputs exceed its inputs; any subsequent `fee()` call then panics, and signing/broadcasting the malformed transaction fails downstream. [3](#0-2) 

### Impact Explanation
A crafted withdrawal/payment amount causes the processor to panic while constructing or inspecting a `SignableTransaction`, crashing the signing task and aborting the batch — a denial of service on the signing pipeline, matching the report's availability-only impact. The release-build wraparound variant additionally produces a structurally invalid transaction (outputs > inputs) that wastes a signing attempt. No key material or funds are directly exposed.

### Likelihood Explanation
Payments originate from user-initiated transfer instructions reaching the processor, so an unprivileged user can supply the amount. Only a single payment of `u64::MAX`-scale magnitude (or a large `fee_per_vbyte`/`vbytes` product) is required. Likelihood is moderate: it requires the integrator path to forward unvalidated amounts into `SignableTransaction::new`, but no threshold compromise, malicious validator, or leaked key is needed — the bytes are public message data.

### Recommendation
- Bound payment amounts to Bitcoin's money range (`<= 21_000_000 * 100_000_000` sats) alongside the `DUST` check.
- Replace `sum::<u64>()`, `payment_sat + needed_fee`, `payment_sat + fee_with_change`, and `fee_per_vbyte * vbytes` with `checked_add`/`checked_mul`/`checked_sum`, mapping overflow to `TransactionError::NotEnoughFunds`/`TooLowFee`.
- Make `fee()` use `checked_sub` or compute via `i128`/`saturating` arithmetic, since the fee relationship is already guaranteed by construction.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// With one payment of u64::MAX sats (>= DUST, so it passes line 166):
let payments = [(p2tr_script_buf(key).unwrap(), u64::MAX)];
// input_sat is bounded by real on-chain value (< 21M BTC).
// Line 215: input_sat < (u64::MAX + needed_fee) -> u64 overflow panic (debug)
//           or wraps to a small value, passing the check (release).
SignableTransaction::new(inputs, &payments, None, None, FEE);
// In release, construction succeeds; then tx.fee() underflows at line 139:
//   sum(prevouts) - sum(outputs)  -> panic on subtraction underflow.
```

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-235)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

    if payments.is_empty() && change.is_none() && data.is_none() {
      Err(TransactionError::NoOutputs)?;
    }

    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }

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
    }
```
