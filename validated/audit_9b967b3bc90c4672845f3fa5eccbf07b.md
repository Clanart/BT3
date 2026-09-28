### Title

Fee-adjusted branch outputs drop all queued payments due to inverted subtraction - (File: processor/src/multisigs/scheduler/utxo.rs)

### Summary

Serai’s UTXO scheduler incorrectly computes the fee shortfall for a created branch output as `actual - expected`, rather than `expected - actual`. Since `actual` is normally less than `expected`, this either panics in debug builds or underflows to a near-`u64::MAX` value in release builds. The scheduler then treats the wrapped value as an unpayable fee and permanently drops all payments associated with the newly created branch output.

### Finding Description

When more payments are queued than fit in one transaction, `Scheduler::execute` moves excess payments into `queued_plans` and replaces them with a branch payment whose value equals their combined amount. [1](#0-0) 

Before signing, `prepare_send` amortizes transaction fees across payment amounts. For each branch payment, it records the original amount as `expected` and the post-fee amount as `actual`. [2](#0-1)  The resulting `post_fee_branches` are passed back to the scheduler through `created_output`. [3](#0-2) 

`created_output` removes the queued child payments and verifies that their sum equals `expected`. [4](#0-3)  It then attempts to amortize the fee using:

```rust
let mut to_amortize = actual - expected;
``` [5](#0-4) 

Because `actual` is the post-fee amount, it is expected to be smaller than `expected`. In a debug build, this subtraction panics. In a release build, it wraps to approximately `u64::MAX`. The following check compares the child-payment total against that wrapped amount and returns, permanently dropping the child payments:

```rust
if payments.iter().map(|payment| payment.balance.amount.0).sum::<u64>() < to_amortize {
  return;
}
``` [6](#0-5) 

The payments were already removed from `queued_plans`, so returning at this point loses their scheduling state rather than retrying them later. [4](#0-3) 

### Impact Explanation

An ordinary fee deduction on a valid branch transaction causes all child payments represented by that branch to be silently removed from scheduling. The branch output can later be received and stored as a UTXO, but the original payment obligations are no longer associated with it. In debug builds the processor can also panic when the branch is created.

This is a reachable accounting failure affecting Bitcoin transaction construction: the chain uses fees, meaning the actually received branch amount necessarily differs from the pre-fee expected amount. The resulting state corrupts the scheduler’s payment accounting and can prevent withdrawals represented by the branch from being paid.

### Likelihood Explanation

The vulnerable path is reached whenever a plan must create a branch because the number of payments exceeds `N::MAX_OUTPUTS`, which is 520 for Bitcoin. [7](#0-6) [8](#0-7) 

An unprivileged user does not need to control validators or nodes. They only need to cause enough public withdrawal/payment instructions to be scheduled across blocks so that a plan exceeds the output limit. Any nonzero transaction fee produces `actual < expected`, triggering the inverted subtraction.

### Recommendation

Reverse the subtraction so the fee shortfall is computed as:

```rust
let mut to_amortize = expected - actual;
```

The function should also explicitly reject `actual > expected` rather than relying on arithmetic behavior, since an actual branch output larger than the expected amount would indicate inconsistent scheduler state. Add a regression test where a branch has `expected = N` and `actual = N - fee`, then verify that the fee is amortized across the queued payments and that surviving payments are inserted into `plans` under `actual`.

### Proof of Concept

Consider a Bitcoin plan containing 521 payments. `execute` removes enough payments to fit within `MAX_OUTPUTS`, stores them in `queued_plans` under an `expected` amount, and inserts a branch payment for that amount. [7](#0-6) 

Suppose:

```text
expected = 100_000 sats
actual   = 99_000 sats
fee      = 1_000 sats
```

`created_output` removes the queued child payments totaling `100_000`, then computes:

```text
to_amortize = actual - expected
            = 99_000 - 100_000
```

In debug mode, this underflow panics. In release mode, it produces:

```text
to_amortize = 2^64 - 1_000
```

The subsequent comparison becomes:

```text
100_000 < 2^64 - 1_000
```

so the function returns before inserting the adjusted payments into `plans`. The child payments have already been popped from `queued_plans`, leaving the branch output without its corresponding payment obligations. The correct calculation should be:

```text
to_amortize = expected - actual
            = 100_000 - 99_000
            = 1_000
```

allowing the 1,000-sat fee to be distributed across the queued payments.

### Citations

**File:** processor/src/multisigs/scheduler/utxo.rs (L219-253)
```rust
    let mut add_plan = |payments| {
      let amount = payment_amounts(&payments);
      self.queued_plans.entry(amount).or_insert(VecDeque::new()).push_back(payments);
      amount
    };

    let branch_address = N::branch_address(self.key).unwrap();

    // If we have more payments than we can handle in a single TX, create plans for them
    // TODO2: This isn't perfect. For 258 outputs, and a MAX_OUTPUTS of 16, this will create:
    // 15 branches of 16 leaves
    // 1 branch of:
    // - 1 branch of 16 leaves
    // - 2 leaves
    // If this was perfect, the heaviest branch would have 1 branch of 3 leaves and 15 leaves
    while payments.len() > max {
      // The resulting TX will have the remaining payments and a new branch payment
      let to_remove = (payments.len() + 1) - N::MAX_OUTPUTS;
      // Don't remove more than possible
      let to_remove = to_remove.min(N::MAX_OUTPUTS);

      // Create the plan
      let removed = payments.drain((payments.len() - to_remove) ..).collect::<Vec<_>>();
      assert_eq!(removed.len(), to_remove);
      let amount = add_plan(removed);

      // Create the payment for the plan
      // Push it to the front so it's not moved into a branch until all lower-depth items are
      payments.insert(
        0,
        Payment {
          address: branch_address.clone(),
          data: None,
          balance: ExternalBalance { coin: self.coin, amount: Amount(amount) },
        },
```

**File:** processor/src/multisigs/scheduler/utxo.rs (L471-481)
```rust
    // Get the payments this output is expected to handle
    let queued = self.queued_plans.get_mut(&expected).unwrap();
    let mut payments = queued.pop_front().unwrap();
    assert_eq!(expected, payments.iter().map(|payment| payment.balance.amount.0).sum::<u64>());
    // If this was the last set of payments at this amount, remove it
    if queued.is_empty() {
      self.queued_plans.remove(&expected);
    }

    // If we didn't actually create this output, return, dropping the child payments
    let Some(actual) = actual else { return };
```

**File:** processor/src/multisigs/scheduler/utxo.rs (L483-489)
```rust
    // Amortize the fee amongst all payments underneath this branch
    {
      let mut to_amortize = actual - expected;
      // If the payments are worth less than this fee we need to amortize, return, dropping them
      if payments.iter().map(|payment| payment.balance.amount.0).sum::<u64>() < to_amortize {
        return;
      }
```

**File:** processor/src/networks/mod.rs (L504-515)
```rust
      // Note the branch outputs' new values
      let mut branch_outputs = vec![];
      for (initial_amount, payment) in initial_payment_amounts.into_iter().zip(&payments) {
        if Some(&payment.address) == Self::branch_address(key).as_ref() {
          branch_outputs.push(PostFeeBranch {
            expected: initial_amount,
            actual: if payment.balance.amount.0 == 0 {
              None
            } else {
              Some(payment.balance.amount.0)
            },
          });
```

**File:** processor/src/multisigs/mod.rs (L762-775)
```rust
        for branch in post_fee_branches {
          let existing = self.existing.as_mut().unwrap();
          let to_use = if key == existing.key {
            existing
          } else {
            let new = self
              .new
              .as_mut()
              .expect("plan wasn't for existing multisig yet there wasn't a new multisig");
            assert_eq!(key, new.key);
            new
          };

          to_use.scheduler.created_output::<D>(txn, branch.expected, branch.actual);
```

**File:** processor/src/networks/bitcoin.rs (L564-576)
```rust
// Bitcoin has a max weight of 400,000 (MAX_STANDARD_TX_WEIGHT)
// A non-SegWit TX will have 4 weight units per byte, leaving a max size of 100,000 bytes
// While our inputs are entirely SegWit, such fine tuning is not necessary and could create
// issues in the future (if the size decreases or we misevaluate it)
// It also offers a minimal amount of benefit when we are able to logarithmically accumulate
// inputs
// For 128-byte inputs (36-byte output specification, 64-byte signature, whatever overhead) and
// 64-byte outputs (40-byte script, 8-byte amount, whatever overhead), they together take up 192
// bytes
// 100,000 / 192 = 520
// 520 * 192 leaves 160 bytes of overhead for the transaction structure itself
const MAX_INPUTS: usize = 520;
const MAX_OUTPUTS: usize = 520;
```
