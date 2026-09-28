### Title
Sub-dust change is silently burned as excess miner fee instead of reducing the payment set or erroring — ([File: networks/bitcoin/src/wallet/send.rs](https://github.com/Kirstentat/serai--024/blob/main/networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` decides whether the change output "should" exist by checking a computed leftover value against `DUST`, mirroring the Ajna bug where `fromBucketPrice >= lup_` gated the fee instead of recording whether it was owed. When the leftover falls below `DUST` (or `checked_sub` fails entirely because `payment_sat + fee_with_change > input_sat`), the change output is silently dropped and the entire leftover is folded into the miner fee. The transaction therefore pays strictly more than the `needed_fee` the caller committed to, and in the `checked_sub == None` case it should have returned `NotEnoughFunds` but instead succeeds with an arbitrarily inflated fee.

### Finding Description
At `networks/bitcoin/src/wallet/send.rs:204-235`, the fee is first computed without change:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
if input_sat < (payment_sat + needed_fee) { Err(NotEnoughFunds)?; }
```

Then, conditionally:

```rust
if let Some(change) = change {
  let (weight_with_change, vbytes_with_change) = ...;
  let fee_with_change = fee_per_vbyte * vbytes_with_change;
  if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
    if value >= DUST { /* add change output, needed_fee = fee_with_change */ }
  }
}
```

Two failure modes of this state-dependent check:

1. **Silent overpayment (`value < DUST` or `checked_sub` returns `None`).** The change output is not pushed, `weight`/`needed_fee` keep their no-change values, but the actual fee — `fee() = sum(prevouts) - sum(outputs)` at line 138-141 — becomes `input_sat - payment_sat`, which exceeds `needed_fee` by the dropped leftover. `needed_fee()` (line 133) then misreports the fee the transaction will actually pay.
2. **`checked_sub` failure masks insufficient funds.** If `payment_sat + fee_with_change > input_sat` (i.e., funds suffice for a no-change tx but not for one with change), `checked_sub` returns `None` and construction succeeds, burning `input_sat - payment_sat` as fee — potentially far above the requested `fee_per_vbyte` rate — rather than erroring.

An unprivileged party who can influence the payment amounts in a transaction the wallet constructs (e.g., withdrawal amounts fed into `SignableTransaction::new`) can pick amounts so that `input_sat - payment_sat - fee_with_change` lands just under `DUST` (or negative), forcing the multisig to sign a transaction paying an inflated fee — analogous to Ajna's lender being double-charged because the check used a mutable price instead of recorded state.

### Impact Explanation
Loss of funds: every crafted payment schedule can burn up to ~`DUST - 1` sats plus the `fee_with_change - needed_fee` delta as unintended miner fee, and in the `checked_sub == None` branch the excess is unbounded up to the full leftover. Because this is signed by the FROST threshold (`TransactionMachine::preprocess`/`sign` at lines 297-398), the overpayment is consensus-valid and irreversible. `needed_fee()` underreports the real fee, so callers relying on it for accounting (the scheduler amortizes fees per payment) will misaccount.

### Likelihood Explanation
Deterministic: it requires only choosing payment amounts such that `input_sat - payment_sat` is within `[needed_fee, needed_fee + DUST)` — a range an attacker controlling withdrawal amounts can hit whenever input values are known (inputs are public on-chain). Repeatable across transactions.

### Recommendation
Decide the change output based on the requested fee rate, not on a post-hoc leftover comparison: compute `value = input_sat - payment_sat - fee_with_change` first; if `value >= DUST` add change, else if `input_sat - payment_sat - needed_fee` is acceptable add no change *and* keep `needed_fee` consistent with `fee()`, else return `NotEnoughFunds`. Never let `fee()` exceed `needed_fee` silently; assert `fee() == needed_fee` before returning the `SignableTransaction`.

### Proof of Concept
```rust
// inputs: one ReceivedOutput of 10_000 sats; payments: [(script, P)]; change: Some(addr);
// fee_per_vbyte chosen so vbytes_no_change = 100, vbytes_with_change = 143.
// Pick P = 10_000 - 143*fee_per_vbyte - 545  (leftover-with-change = 545 < DUST)
let tx = SignableTransaction::new(inputs, &[(script, P)], Some(change), None, fee_per_vbyte)?;
// No change output was added; needed_fee() reports 100*fee_per_vbyte,
// but tx.fee() == 10_000 - P == 143*fee_per_vbyte + 545  → overpaid by ~545 + 43*rate sats.
assert!(tx.fee() > tx.needed_fee());
// Second case: P = 10_000 - 100*fee_per_vbyte (exactly affordable without change).
// checked_sub fails on fee_with_change → no error, fee() == 100*fee_per_vbyte + 0 leftover...
// and with P one sat higher it still succeeds, paying more than needed_fee instead of
// returning NotEnoughFunds.
```
Relevant code: `networks/bitcoin/src/wallet/send.rs:204-235` (conditional change insertion), `send.rs:133-141` (`needed_fee` vs `fee` divergence).