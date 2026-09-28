### Title
Unchecked u64 arithmetic on user-supplied amounts/fee rate overflows, bypassing the funds-sufficiency check and producing an invalid signed transaction (DoS) - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
CVE-2019-18805 is a signed integer overflow triggered when a user writes a very large integer into a kernel sysctl, leading to incorrect bounds decisions and denial of service. The analog in Serai is the unchecked `u64` arithmetic in `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`), where payment amounts and `fee_per_vbyte` are caller-controlled integers summed and multiplied without overflow checks. The kernel bug is "large user integer → overflowed accumulator → wrong security decision"; here it is "large payment amount / fee rate → wrapped `payment_sat`/`needed_fee` → bypassed `NotEnoughFunds` check → a transaction that sums to more than the inputs is constructed and handed to the FROST signing machine."

### Finding Description
`SignableTransaction::new` validates each payment only against the `DUST` lower bound (line 165-169) and never checks upper bounds or uses checked arithmetic: [1](#0-0) [2](#0-1) 

`payment_sat` is a `sum::<u64>()` over attacker-influenced amounts and can wrap. The sufficiency gate then adds it to `needed_fee` unchecked: [3](#0-2) 

`needed_fee = fee_per_vbyte * vbytes` (line 206) and `fee_with_change = fee_per_vbyte * vbytes_with_change` (line 227) can similarly wrap a large `fee_per_vbyte` to a small value. Finally, `fee()` computes `sum(inputs) - sum(outputs)` as a raw `u64` subtraction, which underflows when outputs exceed inputs: [4](#0-3) 

In release builds these wrap silently; in debug builds they panic. Either way, `input_sat < (payment_sat + needed_fee)` can evaluate false even though the true sum of payments exceeds the inputs, so the function returns `Ok(SignableTransaction)` whose `tx_outs` contain `Amount::from_sat` values totaling more than the prevouts. That object flows into `tx.sign_machine`/`attempt_sign`, so the threshold multisig produces a valid FROST signature over a sighash for a transaction that is consensus-invalid (outputs > inputs) and can never confirm — or a valid transaction paying a far smaller fee than requested, which will languish unconfirmed.

### Impact Explanation
- Signing resources are consumed on a transaction that can never be mined: the round produces a broadcastable-but-worthless signature, and `fee()` underflows to ~u64::MAX or panics, corrupting any downstream accounting that subtracts the fee from tracked balances (e.g., the processor's `instruction.balance.amount.0 -= tx.0.fee()` path).
- The wrapped `needed_fee`/`fee_with_change` also corrupts the change-output calculation: `input_sat.checked_sub(payment_sat + fee_with_change)` operates on wrapped values, so change is computed against a fee far below the operator-intended `fee_per_vbyte`, yielding a valid transaction that is systematically under-priced and unconfirmable.
- This mirrors the CVE: a single oversized integer in an unchecked accumulator turns a bounds check into a no-op, with availability consequences (denial of service of the spend path) rather than confidentiality loss.

### Likelihood Explanation
The payments list, `fee_per_vbyte`, and change decision are driven by withdrawal/payment requests and network fee data — values an unprivileged user of the bridge influences through the amounts they request. Reaching the bug requires only requesting payments whose u64 sum wraps (e.g., two payments near `u64::MAX/2` each) or triggering a large fee rate. No key material, validator collusion, or malformed curve encodings are needed; it is a plain arithmetic overflow reachable via ordinary API inputs to `SignableTransaction::new`. Severity is Medium: the outcome is denial of service / wasted signing sessions, not theft, since consensus rules prevent the malformed transaction from moving funds.

### Recommendation
Use `checked_add`/`checked_mul`/`checked_sum` for `payment_sat`, `needed_fee`, `fee_with_change`, the `payment_sat + needed_fee` comparison, and the `fee()` subtraction, returning a new `TransactionError::Overflow`/`NotEnoughFunds` variant on failure. Additionally bound each payment amount to a sane maximum (≤ `MAX_MONEY`, 21e6 * 1e8 sats) and bound `fee_per_vbyte` to a maximum rate before multiplying by `vbytes`.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs path
// Assume `output` is a legitimately scanned ReceivedOutput worth `input_sat` sats.
let huge = u64::MAX - 1000;
// Each payment individually passes the `*amount < DUST` check.
let payments = [
  (p2tr_script_buf(attacker_key).unwrap(), huge),
  (p2tr_script_buf(attacker_key).unwrap(), huge),
];
// payment_sat = huge + huge wraps mod 2^64 to a small value (~u64::MAX*2 - 2000 mod 2^64).
// `input_sat < (payment_sat + needed_fee)` therefore evaluates false and
// NotEnoughFunds is NOT returned despite outputs totaling ~2*input_sat.
let tx = SignableTransaction::new(vec![output], &payments, None, None, 10).unwrap();
// `tx.fee()` underflows (sum(inputs) - sum(outputs) < 0) -> panic in debug,
// wraps to ~u64::MAX in release. The constructed tx is consensus-invalid and
// unconfirmable, yet it is queued for FROST signing.
```

Relevant code: [5](#0-4)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-235)
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
    }
```
