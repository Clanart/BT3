Confirmed: `raise_account_cu_limit` (SIMD-0306) only appears in `feature-set/src/lib.rs` as a declared feature ID/description — it is never consumed by `cost_limits()`, `apply_cost_tracker_limits_for_active_features()`, or anywhere else in the runtime. The account-cost scaling that the test observes is entirely driven by `raise_block_limits_to_100m` alone.

### Title
Dead/unwired `raise_account_cu_limit` (SIMD-0306) feature gate creates a false source-of-truth for the per-account CU limit - ([File: runtime/src/slot_params.rs])

### Summary
Analogous to the OVM report, Agave defines two nominally independent gas/CU-limit knobs — the block-wide compute limit (SIMD-0286, `raise_block_limits_to_100m`) and the per-writable-account compute limit (SIMD-0306, `raise_account_cu_limit`) — but only one of them is actually wired into the enforcement code that computes `CostTrackerLimits`. The account limit is *derived arithmetically* from the block-limit feature flag alone.

### Finding Description
`SlotParams::cost_limits` takes a single boolean, `raise_block_limits_to_100m`, and scales **both** `account_cost` and `block_cost` by the same `100/60` ratio when that flag is active: [1](#0-0) 

This function is invoked from `Bank::apply_cost_tracker_limits_for_active_features`, which only reads `self.feature_set.snapshot().raise_block_limits_to_100m` and never references `raise_account_cu_limit` at all: [2](#0-1) 

Meanwhile, `raise_account_cu_limit::id()` is declared and documented as "SIMD-0306: Raise account CU limit to 40% max" in the feature table: [3](#0-2) 

but a full-repo search shows this feature ID is referenced **only** in that declaration file — it is never checked by `cost_limits()`, `apply_cost_tracker_limits_for_active_features()`, `CostTracker`, or any other consumer. The account-cost limit that Agave actually enforces (`CostTracker::would_fit` via `limits.account_cost`) is entirely a side effect of `raise_block_limits_to_100m`, not of the SIMD-0306 gate that is supposed to control it: [4](#0-3) 

The bank test `test_block_limits` inadvertently documents this: it activates only `raise_block_limits_to_100m` (never touching `raise_account_cu_limit`), yet asserts the account limit jumps to `MAX_WRITABLE_ACCOUNT_UNITS_SIMD_0306_ENABLED` (40_000_000) anyway: [5](#0-4) 

This mirrors the OVM report's exact broken invariant: two config values (per-account CU cap, per-block CU cap) that are supposed to be independently governed but have no enforced/consistent single source of truth — here it is worse, because one of the two "sources of truth" (the SIMD-0306 feature gate) is fully inert. Any tooling, monitoring, or governance process that treats `raise_account_cu_limit` as the controlling switch for the account CU cap (as its name and SIMD documentation imply) would be silently wrong, and there is no on-chain or off-chain check comparing the declared feature's intended effect against the value actually enforced by `CostTracker`.

### Impact Explanation
This does not directly allow fund theft or an unprivileged attacker-triggered crash by itself, but it breaks the "false execution/rooting/acceptance" invariant class: cluster behavior (how much CU a single hot account may consume per block, a key parallelism/DoS-resistance parameter) depends on a feature flag that appears activatable/deactivatable independently but has zero effect in isolation. If SIMD-0306 is ever activated without SIMD-0286 (e.g., a future release intends to decouple these gates, or an operator/validator/tooling assumes independent activation per the SIMD spec), the account limit will silently remain at the old value (24M) while some other code path or monitoring assumes 40M, or vice versa — a corrupted/mismatched value scenario identical in nature to the report's CTC/Execution-Manager gas-limit mismatch. Because block-cost and account-cost limits directly gate transaction admission into the leader's block (`CostTracker::would_fit`), any divergence between the intended and actual enforcement is a consensus-relevant parameter, not cosmetic.

### Likelihood Explanation
Low-to-moderate. This is not exploitable by an unprivileged remote attacker today since the flag is simply unused — it requires a future protocol change (activating `raise_account_cu_limit` independently, as its name/SIMD doc suggest) or a governance/tooling error that assumes the two gates are independently meaningful. This is closest to the report's own caveat: "would only occur due to a misconfiguration of the system by its deployers or a corrupt upgrade."

### Recommendation
Wire `raise_account_cu_limit` into `cost_limits()`/`apply_cost_tracker_limits_for_active_features` explicitly (or remove the unused feature ID if SIMD-0306 is meant to be implicitly bundled with SIMD-0286), and add a debug/const assertion so any future decoupling of the two gates cannot silently produce an account-cost limit that no longer matches its named feature-gate semantics. Document in `slot_params.rs` that the scaling is intentionally single-sourced from `raise_block_limits_to_100m` so the currently-dead `raise_account_cu_limit` isn't mistaken for a controlling switch.

### Proof of Concept [6](#0-5)  demonstrates the coupling: activating only `raise_block_limits_to_100m` (SIMD-0286) causes `get_account_limit()` to change to the SIMD-0306 value even though `raise_account_cu_limit` was never stored/activated, proving the account-limit enforcement path ignores the feature gate that is documented as controlling it.

**Uncertainty:** I could not find any other consumer of `raise_account_cu_limit` in the indexed portion of the codebase (grep returned only the declaration in `feature-set/src/lib.rs`); it's possible additional wiring exists in code not covered by the index. If the user needs full certainty, a Devin session with complete repo access could re-verify via a repo-wide search/build check for `raise_account_cu_limit` usage.

### Citations

**File:** runtime/src/slot_params.rs (L94-119)
```rust
    pub(crate) const fn cost_limits(self, raise_block_limits_to_100m: bool) -> CostTrackerLimits {
        let cost_tracker_limits = self.cost_tracker_limits;
        let (account_cost, block_cost) = if raise_block_limits_to_100m {
            (
                cost_tracker_limits
                    .account_cost
                    .saturating_mul(100)
                    .saturating_div(60),
                cost_tracker_limits
                    .block_cost
                    .saturating_mul(100)
                    .saturating_div(60),
            )
        } else {
            (
                cost_tracker_limits.account_cost,
                cost_tracker_limits.block_cost,
            )
        };

        CostTrackerLimits::new(
            account_cost,
            block_cost,
            cost_tracker_limits.allocated_data_size,
        )
    }
```

**File:** runtime/src/bank.rs (L4871-4879)
```rust
    /// Recomputes cost tracker limits from active feature state.
    fn apply_cost_tracker_limits_for_active_features(&mut self) {
        let params = self.current_slot_params();
        let cost_limits =
            params.cost_limits(self.feature_set.snapshot().raise_block_limits_to_100m);

        let mut cost_tracker = self.write_cost_tracker().unwrap();
        cost_tracker.set_limits(cost_limits);
    }
```

**File:** feature-set/src/lib.rs (L2447-2450)
```rust
        (
            raise_account_cu_limit::id(),
            "SIMD-0306: Raise account CU limit to 40% max",
        ),
```

**File:** cost-model/src/cost_tracker.rs (L272-286)
```rust
    fn would_fit(
        &self,
        tx_cost: &TransactionCost<impl TransactionWithMeta>,
    ) -> Result<(), CostTrackerError> {
        let cost: u64 = tx_cost.sum();

        if self.block_cost().saturating_add(cost) > self.limits.block_cost {
            // check against the total package cost
            return Err(CostTrackerError::WouldExceedBlockMaxLimit);
        }

        // check if the transaction itself is more costly than the account_cost_limit
        if cost > self.limits.account_cost {
            return Err(CostTrackerError::WouldExceedAccountMaxLimit);
        }
```

**File:** runtime/src/bank/tests.rs (L6698-6741)
```rust
#[test]
fn test_block_limits() {
    const MAX_WRITABLE_ACCOUNT_UNITS_SIMD_0306_ENABLED: u64 = 40_000_000;
    let (bank0, _bank_forks) = create_simple_test_arc_bank(100_000);
    let mut bank = Bank::new_from_parent(bank0, SlotLeader::default(), 1);

    // Ensure increased block limits features are inactive.
    assert!(!bank.feature_set.snapshot().raise_block_limits_to_100m);
    assert_eq!(
        bank.read_cost_tracker().unwrap().get_block_limit(),
        MAX_BLOCK_UNITS,
        "before activating the feature, bank should have old/default limit"
    );
    assert_eq!(
        bank.read_cost_tracker().unwrap().get_account_limit(),
        MAX_WRITABLE_ACCOUNT_UNITS,
        "before activating the feature, bank should have old/default limit"
    );

    // Activate `raise_block_limits_to_100m` feature
    bank.store_account(
        &feature_set::raise_block_limits_to_100m::id(),
        &feature::create_account(&Feature::default(), 42),
    );
    // compute_and_apply_features_after_snapshot_restore will not cause the block limit to be updated
    bank.compute_and_apply_features_after_snapshot_restore();
    assert_eq!(
        bank.read_cost_tracker().unwrap().get_block_limit(),
        MAX_BLOCK_UNITS,
        "before activating the feature, bank should have old/default limit"
    );

    // compute_and_apply_new_feature_activations will cause feature to be activated
    bank.compute_and_apply_new_feature_activations();
    assert_eq!(
        bank.read_cost_tracker().unwrap().get_block_limit(),
        MAX_BLOCK_UNITS_SIMD_0286,
        "after activating the feature, bank should have new limit"
    );
    assert_eq!(
        bank.read_cost_tracker().unwrap().get_account_limit(),
        MAX_WRITABLE_ACCOUNT_UNITS_SIMD_0306_ENABLED,
        "after activating the feature, bank should have new limit"
    );
```
