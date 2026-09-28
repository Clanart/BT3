### Title
u64 wraparound in payment/fee summation lets an under-funded, consensus-invalid Bitcoin transaction be threshold-signed - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` sums payment amounts into a `u64` (`payment_sat`) and then performs unchecked `u64` addition (`payment_sat + needed_fee`) to decide whether inputs cover the spend. A crafted set of payments whose amounts sum past `u64::MAX` wraps the accumulator to a small value, defeating the `NotEnoughFunds` check and the change calculation, producing a `SignableTransaction` that FROST signs even though its outputs exceed its inputs. This mirrors CVE-2014-0075's bug class: a maliciously sized "chunk" (here, per-output amounts) causes integer wraparound in length/funds accounting.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

- Line 187: `let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();` — unchecked summation of attacker-influenced `u64` amounts wraps modulo 2^64 in release builds.
- Line 206: `let mut needed_fee = fee_per_vbyte * vbytes;` — unchecked multiplication can wrap.
- Line 215: `if input_sat < (payment_sat + needed_fee)` — the solvency check uses the wrapped values; `payment_sat + needed_fee` can itself wrap.
- Line 228: `input_sat.checked_sub(payment_sat + fee_with_change)` — the `checked_sub` guards only the subtraction, not the wrapping addition inside it, so a wrapped `payment_sat` yields a near-`input_sat` change output.

The payments list originates from the Plan built from on-chain instructions (withdrawal amounts chosen by users), so the individual `u64` amounts and the number of payments are reachable via public inputs. There is no per-payment cap at `MAX_MONEY` (21M BTC) or any check that `sum(outputs) <= sum(inputs)` on the actual `tx_outs` vector — only the wrapped accumulators are compared.

### Impact Explanation
The resulting `Transaction` has outputs whose total value far exceeds the inputs (e.g., two outputs of `2^63` sats each against ordinary inputs). `SignableTransaction::multisig` (lines 273–285) and `TransactionSignMachine::sign` (lines 373–395) will still produce valid BIP-340 FROST signature shares over the sighashes of this transaction, and `complete` returns a fully signed transaction that Bitcoin consensus and standardness rules reject (`CTransaction::CheckTransaction`: output value out of range / negative fee). Consequences:

- The threshold validators burn a signing attempt, preprocess nonces, and an on-chain Plan/attempt slot on a transaction that can never confirm — a resource-consumption DoS of the signing pipeline (the same impact class as the Tomcat advisory).
- `SignableTransaction::fee` (lines 138–141) computes `sum(inputs) - sum(outputs)` with plain `u64` subtraction; on this transaction it wraps to a huge value, so any fee accounting/reporting downstream is corrupted.
- The reported `needed_fee`/`fee` no longer reflect reality, so fee-policy checks (`TooLowFee`) are bypassed.

### Likelihood Explanation
Triggering requires a Plan whose payment amounts sum to ≥ 2^64. Amounts are `u64` fields in decoded on-chain data; nothing in this function bounds them to the Bitcoin supply cap, so a malformed plan (e.g., `[(script, 2^63), (script, 2^63)]`) reaching `SignableTransaction::new` suffices. Whether upstream Serai code clamps payment amounts to real balances determines practical reachability — if amounts are ever taken from untrusted instruction data without a `MAX_MONEY` bound, an unprivileged user can submit such payments. Severity is Medium: the signed output is not a valid spend (no theft), but it deterministically wastes validator signing resources and corrupts fee computation — matching the advisory's DoS classification.

### Recommendation
- Validate each payment amount against `bitcoin::Amount::MAX_MONEY` (or the `Amount` checked constructors) before use.
- Replace `sum::<u64>()` for `payment_sat` and `input_sat` with `checked_add` accumulation, and use `checked_add`/`checked_mul` for `payment_sat + needed_fee` and `fee_per_vbyte * vbytes`.
- After building `tx_outs`, assert `sum(output values) <= input_sat` with checked arithmetic before returning the `SignableTransaction`, and make `fee()` use `checked_sub`.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs context
// Two payments whose sum wraps mod 2^64:
let payments = vec![
  (attacker_script.clone(), 1u64 << 63),       // 2^63 sats, >> 21M BTC
  (attacker_script.clone(), 1u64 << 63),       // payment_sat wraps to 0
];
// inputs: a single ordinary ReceivedOutput of, say, 100_000 sats
// change: Some(serai_script)
let stx = SignableTransaction::new(inputs, &payments, Some(change), None, 10)?;
// payment_sat == 0 (wrapped), so:
//   input_sat (100_000) < 0 + needed_fee  -> false, NotEnoughFunds skipped
//   change = input_sat - (0 + fee_with_change) -> ~99_xxx sats pushed as change
// stx.tx.output now totals ~2^64 sats; fee() wraps to u64::MAX - epsilon.
// stx.multisig(keys).preprocess(rng) / sign(...) produces real FROST shares
// over sighashes of a transaction Bitcoin will reject (bad-txns-out-toolarge).
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-191)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-234)
```rust
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
