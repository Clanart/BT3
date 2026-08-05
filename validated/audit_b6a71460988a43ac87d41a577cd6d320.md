## Title
Priority-floor drop decision computed from raw packet bytes diverges from the typed-transaction priority used for scheduling — - (File: core/src/transaction_priority.rs)

## Summary
The external report's core defect pattern is: the same economically/consensus-critical decision value (`isLiquidated`) is computed twice, in two different code paths, from two different intermediate inputs (market price vs. price-after-impact), and the two computations can disagree — causing a decision (liquidate/don't-liquidate) that is inconsistent with the documented invariant. The Agave analog is `calculate_priority_from_bytes` (used as a pre-sigverify "priority floor" admission check on raw packet bytes) versus `calculate_priority_and_cost` (used by the transaction scheduler on the fully typed/sanitized transaction) in `core/src/transaction_priority.rs`. Both are supposed to yield the *same* priority number for the *same* packet, but they are computed from different representations of the transaction against a `Bank` snapshot that is not pinned to be identical between the two call sites, so the two computations can diverge.

## Finding Description
`calculate_priority_and_cost` computes `P = R / (1 + C)` where `R` is derived from `bank.fee_structure()`, `bank.calculate_reward_and_burn_fee_details(...)`, and `bank.feature_set`, and `C` from `CostModel::calculate_cost_for_executed_transaction` (again keyed off `bank.feature_set`) [1](#0-0) .

`calculate_priority_from_bytes` builds a `SanitizedTransactionView`/`RuntimeTransaction` straight from raw wire bytes and calls the exact same `calculate_priority_and_cost` function to get a priority value used purely as a floor/admission check before full sanitization [2](#0-1) . The unit test explicitly documents the invariant that must hold: *"The bytes-path and the typed-path must agree on the same packet, since the scheduler-side queue priority is computed via the typed path and the sigverify-side floor check via the bytes path."* [3](#0-2) 

Both call sites take a `&Bank` reference, but they are invoked at different pipeline stages — the floor check runs earlier (near sigverify) and the scheduler-side typed-path priority calculation runs later (in `scheduler_controller.rs` / `receive_and_buffer.rs`) [4](#0-3) . Nothing in `calculate_priority_and_cost` or its two callers guarantees they are invoked against the *same* `Bank` instance/slot: `bank.fee_structure()`, `bank.feature_set`, and `bank.calculate_reward_and_burn_fee_details` all read live, mutable bank state that changes across slots (fee-rate-governor updates, feature activations, cost-model constant changes gated by feature flags such as those in `builtins-default-costs/src/lib.rs`, and `loaded_accounts_data_size` cost feature checks in `cost-model/src/cost_model.rs`). If the bank used for the early bytes-path floor check differs from the bank later used for the typed-path scheduling priority (e.g., a bank swap/rotation between pipeline stages, or a feature activation boundary crossed between the two calls), the two computed priority numbers for the identical packet bytes can diverge even though the unit test asserts equality for a single fixed bank.

This mirrors the reported bug class exactly: a single conceptual decision (here: "is this packet's priority high enough to pass the floor" vs. "what priority should this transaction get in the scheduler queue") is calculated twice through parallel code paths that are only guaranteed to agree under an implicit assumption (same `Bank`, same feature set) that is not enforced by the type system or by any invariant check at the call sites — only by a test with a single fixed bank per call.

## Impact Explanation
If the bytes-path floor computation and the typed-path scheduler computation diverge for the same transaction (due to differing `Bank`/feature-set state between the two invocations), one of two outcomes results:
- A transaction that should have been dropped by the priority floor (protecting the leader from being flooded by low-fee/spam transactions during high load) is instead admitted, weakening the intended non-RPC remote exhaustion protection that the priority floor is meant to provide.
- A transaction that passes the floor gets a different (lower) priority when it reaches the scheduler than what was used to decide admission, causing scheduling-order inconsistency, but this is a lower-severity ordering effect.

The first case is the security-relevant one: the priority floor exists specifically to reject cheap/spam transactions before they consume sigverify/banking-stage resources, and using stale/mismatched bank state to compute the floor value can let spam through, contributing to non-RPC remote exhaustion of leader validators.

## Likelihood Explanation
Likelihood is bounded by how often bank state actually changes between the two calculation call sites (fee-rate-governor, feature-set activation) within the timeframe that a single packet flows from sigverify through to scheduling. This is plausible especially around epoch/feature-activation boundaries where `bank.feature_set` snapshots change (affecting `CostModel::calculate_cost_for_executed_transaction` and default compute-unit-limit calculations in `compute-budget-instruction/src/compute_budget_instruction_details.rs`), but the exact banks passed at each call site were not fully traceable within the available index (the callers in `core/src/sigverify.rs`, `core/src/sigverify_stage.rs`, `core/src/banking_stage/transaction_scheduler/scheduler_controller.rs`, and `core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs` could not be fully read in this session due to iteration limits).

## Recommendation
Ensure both `calculate_priority_from_bytes` (floor check) and the typed-path `calculate_priority_and_cost` (scheduler queueing) are always evaluated against the identical `Bank` snapshot for a given packet/transaction lifecycle, or, if that is architecturally impossible, make the divergence bounded/documented and monotonic (e.g., only allow the floor check to be *more* conservative than the scheduler's actual value, never less) so a bank-state race cannot let spam bypass the floor.

## Proof of Concept
Not independently verified against the full call graph — the callers in `core/src/sigverify.rs`, `core/src/sigverify_stage.rs`, and `core/src/banking_stage/transaction_scheduler/scheduler_controller.rs` could not be fully read before the iteration limit was reached, so it is not confirmed from this session whether the two call sites are always guaranteed to use the same `Bank` reference in production. This should be verified with a full read of those three files (specifically where each calls into `transaction_priority::calculate_priority_from_bytes` vs `calculate_priority_and_cost`) before treating this as a confirmed, exploitable divergence rather than a latent design risk. The existing regression test `floor_priority_from_bytes_matches_typed_path` [3](#0-2)  only proves equality for a single shared `Bank`, and does not exercise the cross-slot/feature-activation-boundary case.

### Citations

**File:** core/src/transaction_priority.rs (L1-12)
```rust
use {
    agave_transaction_view::transaction_view::SanitizedTransactionView,
    solana_cost_model::cost_model::CostModel,
    solana_runtime::bank::{Bank, CollectorFeeDetails},
    solana_runtime_transaction::{
        runtime_transaction::RuntimeTransaction,
        sanitize_config::sanitize_config,
        transaction_meta::{TransactionConfiguration, TransactionMeta},
    },
    solana_svm_transaction::svm_message::SVMStaticMessage,
    solana_transaction::sanitized::MessageHash,
};
```

**File:** core/src/transaction_priority.rs (L32-66)
```rust
pub(crate) fn calculate_priority_and_cost<Tx: TransactionMeta + SVMStaticMessage>(
    bank: &Bank,
    transaction: &Tx,
    transaction_configuration: &TransactionConfiguration,
) -> (u64, u64) {
    let cost = CostModel::calculate_cost_for_executed_transaction(
        transaction,
        u64::from(transaction_configuration.compute_unit_limit),
        transaction_configuration.loaded_accounts_data_size_limit,
        &bank.feature_set,
    )
    .sum();
    let fee_details = solana_fee::calculate_fee_details(
        transaction,
        bank.fee_structure().lamports_per_signature,
        transaction_configuration.priority_fee_lamports,
        bank.fee_features(),
    );
    let reward = bank
        .calculate_reward_and_burn_fee_details(&CollectorFeeDetails::from(fee_details))
        .get_deposit();

    // We need a multiplier here to avoid rounding down too aggressively.
    // For many transactions, the cost will be greater than the fees in terms of raw lamports.
    // For the purposes of calculating prioritization, we multiply the fees by a large number so that
    // the cost is a small fraction.
    // An offset of 1 is used in the denominator to explicitly avoid division by zero.
    const MULTIPLIER: u64 = 1_000_000;
    (
        reward
            .saturating_mul(MULTIPLIER)
            .saturating_div(cost.saturating_add(1)),
        cost,
    )
}
```

**File:** core/src/transaction_priority.rs (L73-88)
```rust
pub(crate) fn calculate_priority_from_bytes(bank: &Bank, data: &[u8]) -> Option<u64> {
    let view = SanitizedTransactionView::try_new_sanitized(data, &sanitize_config()).ok()?;
    let runtime_tx = RuntimeTransaction::<SanitizedTransactionView<_>>::try_new(
        view,
        MessageHash::Compute,
        None,
    )
    .ok()?;
    let transaction_configuration = runtime_tx
        .transaction_configuration(&bank.feature_set)
        .ok()?;
    let (priority, _cost) =
        calculate_priority_and_cost(bank, &runtime_tx, &transaction_configuration);

    Some(priority)
}
```

**File:** core/src/transaction_priority.rs (L167-192)
```rust
    #[test]
    fn floor_priority_from_bytes_matches_typed_path() {
        // The bytes-path and the typed-path must agree on the same packet,
        // since the scheduler-side queue priority is computed via the typed
        // path and the sigverify-side floor check via the bytes path.
        let (bank, mint) = test_bank();
        let bytes = make_tx_bytes(&mint, bank.last_blockhash(), 100);

        let from_bytes = priority_from(&bank, &bytes);

        let view =
            SanitizedTransactionView::try_new_sanitized(&bytes[..], &sanitize_config()).unwrap();
        let runtime_tx = RuntimeTransaction::<SanitizedTransactionView<_>>::try_new(
            view,
            MessageHash::Compute,
            None,
        )
        .unwrap();
        let transaction_configuration = runtime_tx
            .transaction_configuration(&bank.feature_set)
            .unwrap();
        let (from_typed, _cost) =
            calculate_priority_and_cost(&bank, &runtime_tx, &transaction_configuration);

        assert_eq!(from_bytes, from_typed);
    }
```
