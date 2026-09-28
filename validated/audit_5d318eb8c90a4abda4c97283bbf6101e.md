### Title
Arithmetic underflow panic in `Scheduler::created_output` when the on-chain branch output is smaller than the expected amount due to stale fee estimation - (File: processor/src/multisigs/scheduler/utxo.rs)

### Summary

The external report's bug class — a subtraction between two quantities priced under inconsistent sources (a fresh spot price for gating vs. a stale cached price for the debt) causing an arithmetic underflow that reverts execution — has a direct analog in Serai's UTXO scheduler. `created_output` computes `actual - expected` in plain `u64` arithmetic. `expected` is the branch output amount planned under a fee estimate taken at one block (`needed_fee` → `median_fee(block_number)`), while `actual` is the value the transaction was actually built with, potentially under a different (fresh) median fee sampled from a later block. If the actual output is smaller than expected, the subtraction underflows and panics, killing the processor task mid-confirmation-handling.

### Finding Description

`SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs` computes `needed_fee` and the change amount from the fee rate passed in. The processor obtains that rate via `Bitcoin::median_fee`, which computes the median per-vbyte fee of `block.txdata[1..]` at a specific block number (`processor/src/networks/bitcoin.rs:388-415`), and `make_signable_transaction` samples it for the supplied `block_number` (`processor/src/networks/bitcoin.rs:429-452`). `prepare_send` in `processor/src/networks/mod.rs` documents the intended flow: call `needed_fee` first, amortize, then call `signable_transaction` — two separate calls that can each sample a different `median_fee` if the block number differs or the mempool/block contents changed.

When a created branch output is later observed on-chain, `Scheduler::created_output` (`processor/src/multisigs/scheduler/utxo.rs:463-524`) is invoked with `expected` (the amount the plan was amortized to) and `actual` (the output's real value):

```rust
// processor/src/multisigs/scheduler/utxo.rs:483-485
// Amortize the fee amongst all payments underneath this branch
{
  let mut to_amortize = actual - expected;
```

If `actual < expected` — which occurs whenever the fee used to build the transaction exceeded the fee assumed when the plan's payments were amortized (a "volatile market"/fresh-vs-stale divergence identical in shape to the Ditto bug) — `actual - expected` underflows. Since this is `u64` arithmetic, Rust panics in all build modes, unwinding through confirmation handling for a transaction that is already on-chain.

The same stale-vs-fresh inconsistency appears in `prepare_send` itself: `theoretical_change_amount` (`mod.rs:435-437`) is computed from input/payment sums, while `on_chain_expected_change` (`mod.rs:580-583`) subtracts the freshly sampled `tx_fee`, and `SignableTransaction::new` may also silently drop the change output (`send.rs:224-234`) when `input_sat.checked_sub(payment_sat + fee_with_change)` is `None` or below `DUST` — desynchronizing the scheduler's expectation from what the transaction actually produced.

### Impact Explanation

A panic in `created_output` aborts the processor's handling of a confirmed Bitcoin transaction. The branch output exists on-chain but its child payments are never re-planned, stalling the multisig scheduler and effectively freezing fund movement for that key (liveness failure / DoS), analogous to the Ditto liquidation revert blocking timely liquidations. It does not directly leak key material, so this is a Medium-severity availability bug.

### Likelihood Explanation

An unprivileged party can influence `median_fee` by sending ordinary high-fee Bitcoin transactions that get mined; `median_fee` scans `block.txdata[1..]` and takes the median. Because fee sampling happens at two distinct points (estimation in `needed_fee` vs. construction in `signable_transaction`/`prepare_send` across different block numbers), a rising fee environment — which any user can help induce — makes `actual < expected` reachable without any validator misconduct. Internal timing changes (retries after `signable_transaction` returned `None`, delayed broadcasts) reach the same state without an attacker.

### Recommendation

Replace `actual - expected` with a checked computation handling both directions:

```rust
let to_amortize = expected.saturating_sub(actual); // fee deficit to amortize into child payments
// or, if the surplus/deficit sign is intended the other way, use
// actual.checked_sub(expected) and handle the None case by dropping/clamping payments
```

Additionally, `prepare_send`/`signable_transaction` should pin the fee sampled at `needed_fee` time (pass it through rather than re-sampling `median_fee`), so `expected` and `actual` are priced under one consistent fee — mirroring the report's recommendation to use one consistent price (or a `min`) across the calculation.

### Proof of Concept

1. A plan is created with a branch output; `needed_fee` at block `N` returns fee `F1`, and payments are amortized so the queued `expected` amount is `E`.
2. Before `signable_transaction` executes (or on a retry), an unprivileged user's high-fee transactions are mined in block `N+k`; `median_fee` now returns `F2 > F1`.
3. The transaction is built with the higher fee, so the branch output is created with `actual < expected`.
4. `Scheduler::created_output` is called with `expected = E`, `actual = Some(A)`, `A < E`; line 485 computes `actual - expected`, underflows, and panics — halting processing of the confirmed transaction and stalling all child payments.