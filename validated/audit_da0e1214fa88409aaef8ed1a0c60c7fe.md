### Title
Unchecked `u64` satoshi arithmetic in `SignableTransaction` permits overflow/underflow panics and fee miscalculation - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
CVE-2022-36402 is an integer-overflow-to-DoS bug class: arithmetic on untrusted quantities overflows, corrupting subsequent logic and crashing the process. The direct analog in Serai is the Bitcoin wallet's transaction constructor `SignableTransaction::new`, which performs plain (non-checked, non-saturating) `u64` arithmetic on amounts and fee rates that originate from plan/payment data: `sum::<u64>()` over inputs, `sum::<u64>()` over payments, `fee_per_vbyte * vbytes`, and `payment_sat + needed_fee`. Overflows panic in debug builds and silently wrap in release builds, producing a transaction whose stated outputs and implied fee disagree with the inputs committed via `Prevouts::All`.

### Finding Description
In `SignableTransaction::new`, all monetary arithmetic is unchecked:

- `input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>()` — unbounded sum of prevout values [1](#0-0) 
- `payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>()` — unbounded sum of requested payment amounts [2](#0-1) 
- `needed_fee = fee_per_vbyte * vbytes` — unchecked multiplication of a caller-supplied fee rate [3](#0-2) 
- `input_sat < (payment_sat + needed_fee)` — unchecked addition in the solvency check [4](#0-3) 
- `fee()` subtracts summed output value from summed input value with plain `-`, which underflows if the constructed outputs ever exceed the inputs [5](#0-4) 

The change-path addition is also partially unchecked: `input_sat.checked_sub(payment_sat + fee_with_change)` uses `checked_sub` on the outer subtraction, but the inner `payment_sat + fee_with_change` can itself overflow before the subtraction [6](#0-5) 

Only the dust bound (`*amount < DUST`, `value >= DUST`) and the weight limit are checked; no total-amount bound exists.

### Impact Explanation
Two failure modes, both matching the CVE's overflow-to-DoS shape:

1. Denial of service: in builds with `overflow-checks` enabled (debug, and any release profile that keeps them), `sum::<u64>()`, `payment_sat + needed_fee`, `fee_per_vbyte * vbytes`, or the subtraction in `fee()` panic, killing the signing/processor task handling the plan.
2. Incorrect transaction construction (release): a wrapped `payment_sat` or `needed_fee` makes the solvency check `input_sat < payment_sat + needed_fee` pass when the true payment total exceeds `input_sat`. The resulting transaction commits to `Prevouts::All` while its outputs sum to more than the inputs — an invalid, unspendable transaction that still consumes a signing round, or a transaction that pays a nonsensical fee.

Because `needed_fee` is never re-validated against `input_sat - payment_sat` after the change logic rewrites it (`needed_fee = fee_with_change` only runs when change is emitted), a wrapped intermediate value propagates into `SignableTransaction.needed_fee` and is returned by `needed_fee()` to callers accounting for fees [7](#0-6) 

### Likelihood Explanation
Payment amounts and fee rates are protocol inputs delivered via plans, and inputs are `ReceivedOutput`s built from Bitcoin transactions any party can send to the multisig address. An unprivileged user who can cause a plan with a large `amount` or large `fee_per_vbyte` to be scheduled — or who deposits outputs whose summed values combine badly with the payment sum — reaches this arithmetic with no key material and no validator status. Triggering it requires values large enough to overflow `u64` or to make the intermediate sums overflow, which constrains practical exploitability on real BTC amounts (bounded above by ~2.1e15 satoshis on-chain for inputs), keeping this at Medium rather than High; the fee-rate multiplication and payment-sum overflow have no such on-chain bound.

### Recommendation
Use `checked_add`/`checked_mul`/`checked_sub` (or `u128`/`Amount`-checked arithmetic) for `input_sat`, `payment_sat`, `payment_sat + needed_fee`, `payment_sat + fee_with_change`, `fee_per_vbyte * vbytes`, and `fee()`. Reject overflows with `TransactionError::NotEnoughFunds`/`TooLowFee`-style errors instead of panicking or wrapping. Also bound `payment.1` individually to `Amount::MAX_MONEY`-consistent values before summation.

### Proof of Concept
Conceptual, against `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`:

```rust
// inputs: two ReceivedOutputs with value = u64::MAX/2 + 1 each (constructed for test)
let inputs: Vec<ReceivedOutput> = vec![out_a, out_b];
// input_sat: sum::<u64>() overflows -> panic (debug) or wraps to 1 (release)
// payments: a single payment with amount u64::MAX - 1000
let payments = &[(script, u64::MAX - 1000)];
// payment_sat + needed_fee overflows -> wrap -> solvency check passes incorrectly,
// or the sum() itself panics
let _ = SignableTransaction::new(inputs, payments, Some(change_script), None, u64::MAX);
// Alternatively: fee_per_vbyte = u64::MAX, vbytes > 1 ->
//   needed_fee = fee_per_vbyte * vbytes overflows -> panic / wrap to small fee
//   -> TooLowFee check on a wrapped value is meaningless
```

Exact file/function: `SignableTransaction::new`, `fee`, `calculate_weight_vbytes` usage — `networks/bitcoin/src/wallet/send.rs:138-141, 175-235`.

Note on confidence: I verified the unchecked arithmetic directly in `send.rs`. I could not fully confirm the upstream bound on payment amounts/fee rates in the scheduler (out of the in-scope paths), so the "public input" reachability rests on payments/fee fields being controllable via plan data; if the substrate layer strictly bounds amounts to total supply, the input-sum overflow is unreachable on real funds, but the `fee_per_vbyte * vbytes` and `payment_sat + needed_fee` overflows remain reachable for arbitrary u64 fields.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L129-135)
```rust
  /// Returns the fee necessary for this transaction to achieve the fee rate specified at
  /// construction.
  ///
  /// The actual fee this transaction will use is `sum(inputs) - sum(outputs)`.
  pub fn needed_fee(&self) -> u64 {
    self.needed_fee
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-175)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-187)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-206)
```rust
    let mut needed_fee = fee_per_vbyte * vbytes;
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-221)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-234)
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
```
