### Title
Attacker-controlled payment amounts overflow `payment_sat`, bypassing the `NotEnoughFunds` check and causing an underflow panic / unspendable transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The audit finding describes an unbounded draw on shared funds (`amoMinterBorrow` never checks `collateralAmount` against `balanceOf(pool) - unclaimedPoolCollateral`), which later makes accounting paths underflow and permanently DoS `mintDollar`, `collateralUsdBalance`, and `collectRedemption`. The analogous shape in Serai is `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`, which accepts a list of `(ScriptBuf, u64)` payments with only a lower-bound dust check, then sums them with plain `u64` addition. A crafted payment list can overflow `payment_sat` (or `payment_sat + needed_fee`), so the solvency check `input_sat < payment_sat + needed_fee` passes while the actual outputs exceed the inputs — producing a `SignableTransaction` whose `fee()` subtraction underflows (panic) and whose serialized transaction is consensus-invalid.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150`), each payment amount is checked only against the dust floor:

```rust
for (_, amount) in payments {
  if *amount < DUST {
    Err(TransactionError::DustPayment)?;
  }
}
```

(`send.rs:165-169`). There is no upper bound on any `amount`, and no bound on the number of payments beyond the later weight check. The amounts are then summed with wrapping `u64` semantics:

```rust
let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
```

(`send.rs:187`), and the solvency check is:

```rust
if input_sat < (payment_sat + needed_fee) { ... NotEnoughFunds ... }
```

(`send.rs:215-221`).

If `payment_sat` wraps (e.g. two payments of `u64::MAX / 2 + 1` each, or one payment of `u64::MAX` where `payment_sat + needed_fee` wraps), the comparison is evaluated against a small wrapped value, so `NotEnoughFunds` is not raised even though the real sum of outputs vastly exceeds `input_sat`. The change branch also computes `input_sat.checked_sub(payment_sat + fee_with_change)` (`send.rs:228`) where the inner `payment_sat + fee_with_change` can itself overflow-panic in debug builds or wrap in release. The resulting `SignableTransaction` stores `tx_outs` whose total exceeds `prevouts`, so `fee()` at `send.rs:138-141`:

```rust
self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
  self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
```

underflows — a panic in debug builds or a wrapped garbage fee in release — and `transaction()`/`txid()` yield a transaction that is consensus-invalid (outputs > inputs). Like the pool in the original report, the construction-time solvency invariant is asserted against a number that does not reflect the real obligation, so the failure surfaces later in the accounting path (`fee()`, broadcast) instead of at the boundary.

### Impact Explanation
`SignableTransaction::new` is the entry point used by the processor's signing pipeline for withdrawal payments, which originate from unprivileged user requests. A malformed payment set either panics the node constructing the transaction (integer overflow / subtraction underflow), or produces a `SignableTransaction` that passes `multisig()`/`preprocess()` and only fails when `fee()` is queried or the invalid transaction is broadcast — wedging the FROST signing round for that plan and burning the inputs' slot in the scheduler. This mirrors the reported DoS: an unchecked draw/obligation amount causes a downstream arithmetic failure that blocks the protocol's core functions.

### Likelihood Explanation
An unprivileged party who can influence the payment list (withdrawal requests with arbitrary `u64` amounts) can trigger this deterministically with public inputs — no collusion, leaked key, or malicious validator needed. It requires amounts that individually pass `>= DUST` but collectively overflow `u64`, which is trivially constructible. The practical impact is bounded by whether the upstream scheduler caps payment amounts, but within this crate the check is missing entirely, and debug/assertion-enabled builds panic outright.

### Recommendation
Bound-check each payment amount (e.g. `<= MAX_MONEY`, 21e6 * 1e8 sats, matching Bitcoin consensus) and accumulate `payment_sat` with `checked_add`/`try_fold`, returning `NotEnoughFunds` or a new `PaymentTooLarge` error on overflow. Likewise compute `payment_sat + needed_fee` and `payment_sat + fee_with_change` via `checked_add` before the comparisons at `send.rs:215` and `send.rs:228`, and have `fee()` use `checked_sub` so an invariant violation returns an error rather than panicking.

### Proof of Concept
Conceptual, against `SignableTransaction::new`:

```rust
// two inputs worth 200_000 sats total
let inputs = vec![received_output_a, received_output_b]; // sum = 200_000

// two dust-passing payments whose sum wraps u64
let payments = vec![
  (attacker_script.clone(), u64::MAX / 2 + 1),
  (attacker_script.clone(), u64::MAX / 2 + 1),
];

// payment_sat wraps to 1; 1 + needed_fee < 200_000, so NotEnoughFunds is not raised
let tx = SignableTransaction::new(inputs, &payments, None, None, 10).unwrap();

// Underflow: sum(prevouts) - sum(outputs) < 0 -> panic (debug) or garbage fee (release)
let _ = tx.fee();
```

In debug builds the overflow panics earlier, at `payment_sat`'s summation or at `payment_sat + needed_fee`. In release, the check is bypassed and the panic moves to `fee()`, or the caller obtains a consensus-invalid transaction paying ~1.8e19 sats of outputs from 2e5 sats of inputs.