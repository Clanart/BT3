## Title
`Stakes::sub_delegated_stake` uses an `.expect()`-panic instead of checked arithmetic, allowing a stale-vs-current effective-stake mismatch to crash the validator - (File: `runtime/src/stakes.rs`)

### Summary
The external report's root cause is generic: a value is *accrued* using one snapshot of an external, mutable input (Oracle price), then later *subtracted* using a different snapshot of that same input, and the subtraction is implemented as an unchecked arithmetic operation that reverts/panics rather than saturating or erroring gracefully. The Agave analog of this pattern is `Stakes::sub_delegated_stake` in `runtime/src/stakes.rs`, which maintains a cached, aggregated "delegated stake" value that is incremented via `add_delegated_stake` and later decremented via `sub_delegated_stake` using effective-stake amounts computed by `delegation_effective_stake()` — a function whose output depends on external/mutable parameters (`stake_history`, `new_rate_activation_epoch`, `use_fixed_point_stake_math`) that can differ between the time stake was added to the cache and the time it is removed.

### Finding Description
`Stakes` maintains a `delegated_stakes: DelegatedStakes` map that aggregates per-voter delegated stake. Entries are added with `add_delegated_stake`: [1](#0-0) 

and removed with `sub_delegated_stake`, which performs a `checked_sub` but then immediately unwraps it with `.expect(...)`, i.e. it panics instead of returning an error on underflow: [2](#0-1) 

The stake amounts fed into both `add_delegated_stake` and `sub_delegated_stake` (via `remove_stake_delegation`) are not raw stake values — they are computed by `delegation_effective_stake(delegation, epoch, stake_history, new_rate_activation_epoch, use_fixed_point_stake_math)`: [3](#0-2) 

This is exactly the "Oracle" analog from the report: `delegation_effective_stake` is a valuation function over a delegation that depends on external/mutable state (`stake_history`, the warmup/cooldown rate-activation epoch, and the fixed-point-math feature flag), just as `_repay()`/`_accrueInterest()` depended on `oracle.getLatestAnswer()`. `new_warmup_cooldown_rate_epoch()` and `use_fixed_point_stake_math()` are bank-level values that can change as new features activate at epoch boundaries, and `stake_history` itself is updated every epoch: [4](#0-3) [5](#0-4) 

`refresh_delegated_stakes` even acknowledges that the aggregate cache must be periodically recomputed from scratch as these parameters change: [6](#0-5) 

The broken invariant: `sub_delegated_stake` assumes that the effective-stake amount computed for a delegation at removal time is always `<=` the amount that was added for that same delegation earlier, i.e. that the "valuation function" is monotonically stable across the two calls for a fixed delegation. This assumption silently depends on `stake_history`/`new_rate_activation_epoch`/`use_fixed_point_stake_math` being unchanged between the `add` and the `sub` — but these are external, time-varying bank state, not immutable inputs pinned to the delegation. If a delegation's cached (added) effective stake was computed under one set of parameters and its later removal computes a *larger* effective stake for the same underlying `Delegation` (analogous to the Oracle price increasing between `_accrueInterest()` and `_repay()`), `current_stake.checked_sub(stake)` returns `None` and the `.expect()` panics instead of returning a `Result`/error like almost every other lamport-arithmetic path in the codebase (`checked_sub_lamports`, `validate_fee_payer`, `deposit_fees`, etc., which all propagate `InstructionError`/`TransactionError` rather than panicking).

Unlike other Agave lamport-arithmetic call sites — which uniformly avoid panics in favor of `checked_add`/`checked_sub` combined with `Result` propagation (e.g. `checked_sub_lamports` in `transaction-context/src/instruction_accounts.rs:154-161`, or `validate_fee_payer` in `svm/src/account_loader.rs:398-405`) — `sub_delegated_stake` is one of the few remaining places using a bare `.expect()` on lamport/stake arithmetic in a code path reachable from ordinary, unprivileged stake-delegate/stake-deactivate transactions via `update_stakes_cache`.

### Impact Explanation
A panic inside `Stakes::sub_delegated_stake`, reached from `Bank::update_stakes_cache` during ordinary transaction processing (any unprivileged user issuing stake delegate/undelegate/split/merge instructions), would abort bank processing for that validator process. Because this code runs on every validator replaying the same block, a reliably triggerable divergence would cause a consensus-wide crash/halt rather than an isolated node failure — this matches "consensus halt" and "non-RPC remote exhaustion/crash" in the valid-impact list. Even if triggering the exact underflow requires very specific stake-history/feature-activation timing (making certain identical scenarios only in-process, single-node, rather than reliably reproducible by any attacker), the presence of an unchecked `.expect()` on state derived from time-varying external parameters is a structural weakness identical in shape to the LineOfCredit bug: an accrue/consume pair of operations over the same logical value, computed at two different points using a function of external, mutable state, with no reconciliation step and a panicking (not graceful) subtraction.

### Likelihood Explanation
The likelihood of exploiting this exact code path deterministically is uncertain from static code review alone — it requires stake_history entries, `new_warmup_cooldown_rate_epoch` activation, or `use_fixed_point_stake_math` toggling in a way that produces two different `delegation_effective_stake` results for the same `Delegation` object between an add and a corresponding remove of the same stake account in the cache. The code's own `refresh_delegated_stakes()` function exists specifically to recompute the whole cache after such parameter changes, suggesting the maintainers are aware that these values can drift, but `sub_delegated_stake`/`add_delegated_stake` (used for incremental per-transaction updates in `update_stakes_cache`) are not protected by the same recomputation guarantee, and still rely on a panicking assertion rather than defensive handling.

### Recommendation
- Replace the `.expect("subtraction value exceeds delegated stake")` in `sub_delegated_stake` with saturating arithmetic (`saturating_sub`) or a graceful clamp-to-zero-and-log path, consistent with the rest of the codebase's checked/saturating arithmetic conventions for lamport/stake amounts.
- Ensure `add_delegated_stake` and the corresponding `sub_delegated_stake`/`remove_stake_delegation` for a given delegation always use the exact same `stake_history`/`new_rate_activation_epoch`/`use_fixed_point_stake_math` snapshot, or store the added effective-stake value alongside the delegation so removal can subtract the identical originally-added quantity rather than recomputing a fresh (and potentially different) valuation.
- Add regression tests that force a divergence (e.g., activate `new_rate_activation_epoch` mid-lifecycle of a delegation) to confirm the aggregate cache handles the mismatch without panicking.

### Proof of Concept
Static-analysis level PoC (exact runtime trigger not confirmed from available context):
1. A stake account is delegated while `use_fixed_point_stake_math` is `false` (or before `new_warmup_cooldown_rate_epoch` is set); `Stakes::upsert`/`check_and_store` calls `add_delegated_stake(voter, stake_A)` where `stake_A = delegation_effective_stake(delegation, epoch, stake_history_v1, None, false)`. [1](#0-0) 
2. A feature activation flips `use_fixed_point_stake_math` to `true` and/or `new_warmup_cooldown_rate_epoch` becomes `Some(epoch)`, and `stake_history` advances to a new snapshot (`stake_history_v2`) as epochs roll over. [5](#0-4) 
3. The same stake account is later deactivated/removed (e.g., via `remove_stake_delegation`), which recomputes `stake_B = delegation_effective_stake(delegation, epoch, stake_history_v2, Some(rate_epoch), true)` for the *same* underlying delegation. [3](#0-2) 
4. If `stake_B > stake_A` (i.e., the recomputed effective stake under the new parameters exceeds the cached amount originally added), `sub_delegated_stake(voter, stake_B)` executes `current_stake.checked_sub(stake_B)`, which returns `None`, and the `.expect("subtraction value exceeds delegated stake")` panics. [7](#0-6) 

This is the structural analog of the LineOfCredit bug: an "accrue" step (`add_delegated_stake`) and a "repay/consume" step (`sub_delegated_stake`) operate on the same logical quantity but are valuated at two different times using a function of external, mutable state (here, stake-history/feature parameters instead of an oracle price), and the consuming step uses an unchecked/panicking subtraction rather than a guarded one. Full confirmation of a concrete, attacker-triggerable sequence of feature activations/stake_history states that reproduces `stake_B > stake_A` was not verifiable from the indexed code alone; a Devin session with full repository access and test-execution capability would be needed to construct and run a deterministic end-to-end reproduction.

### Citations

**File:** runtime/src/stakes.rs (L541-553)
```rust
    fn refresh_delegated_stakes(
        &mut self,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        self.delegated_stakes = Self::calculate_delegated_stakes(
            &self.stake_delegations,
            self.epoch,
            &self.stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        );
    }
```

**File:** runtime/src/stakes.rs (L555-560)
```rust
    fn add_delegated_stake(&mut self, voter_pubkey: Pubkey, stake: u64) {
        if stake == 0 {
            return;
        }
        *self.delegated_stakes.entry(voter_pubkey).or_default() += stake;
    }
```

**File:** runtime/src/stakes.rs (L562-576)
```rust
    fn sub_delegated_stake(&mut self, voter_pubkey: &Pubkey, stake: u64) {
        if stake == 0 {
            return;
        }
        let current_stake = self
            .delegated_stakes
            .get_mut(voter_pubkey)
            .expect("subtraction from missing delegated stake");
        *current_stake = current_stake
            .checked_sub(stake)
            .expect("subtraction value exceeds delegated stake");
        if *current_stake == 0 {
            self.delegated_stakes.remove(voter_pubkey);
        }
    }
```

**File:** runtime/src/stakes.rs (L582-601)
```rust
    fn remove_stake_delegation(
        &mut self,
        stake_pubkey: &Pubkey,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        if let Some(stake_account) = self.stake_delegations.remove(stake_pubkey) {
            let removed_delegation = stake_account.delegation();
            let removed_stake = delegation_effective_stake(
                removed_delegation,
                self.epoch,
                &self.stake_history,
                new_rate_activation_epoch,
                use_fixed_point_stake_math,
            );
            self.sub_delegated_stake(&removed_delegation.voter_pubkey, removed_stake);
            self.vote_accounts
                .sub_stake(&removed_delegation.voter_pubkey, removed_stake);
        }
    }
```

**File:** runtime/src/bank.rs (L5755-5792)
```rust
    /// a bank-level cache of vote accounts and stake delegation info
    fn update_stakes_cache(
        &self,
        txs: &[impl SVMMessage],
        processing_results: &[TransactionProcessingResult],
    ) {
        debug_assert_eq!(txs.len(), processing_results.len());
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let use_fixed_point_stake_math = self.use_fixed_point_stake_math();
        txs.iter()
            .zip(processing_results)
            .filter_map(|(tx, processing_result)| {
                processing_result
                    .processed_transaction()
                    .map(|processed_tx| (tx, processed_tx))
            })
            .filter_map(|(tx, processed_tx)| {
                processed_tx
                    .executed_transaction()
                    .map(|executed_tx| (tx, executed_tx))
            })
            .filter(|(_, executed_tx)| executed_tx.was_successful())
            .flat_map(|(tx, executed_tx)| {
                let num_account_keys = tx.account_keys().len();
                let loaded_tx = &executed_tx.loaded_transaction;
                loaded_tx.accounts.iter().take(num_account_keys)
            })
            .for_each(|(pubkey, account)| {
                // note that this could get timed to: self.rc.accounts.accounts_db.stats.stakes_cache_check_and_store_us,
                //  but this code path is captured separately in ExecuteTimingType::UpdateStakesCacheUs
                self.stakes_cache.check_and_store(
                    pubkey,
                    account,
                    new_warmup_cooldown_rate_epoch,
                    use_fixed_point_stake_math,
                );
            });
    }
```

**File:** runtime/src/bank.rs (L6061-6080)
```rust
    /// Compute and apply all activated features, initialize the transaction
    /// processor, and recalculate partitioned rewards if needed
    fn initialize_after_snapshot_restore<F, TP>(&mut self, rewards_thread_pool_builder: F)
    where
        F: FnOnce() -> TP,
        TP: std::borrow::Borrow<ThreadPool>,
    {
        self.transaction_processor =
            TransactionBatchProcessor::new_uninitialized(self.slot, self.epoch);
        if let Some(compute_budget) = &self.compute_budget {
            self.transaction_processor
                .set_execution_cost(compute_budget.to_cost());
        }

        self.compute_and_apply_features_after_snapshot_restore();
        self.stakes_cache.refresh_delegated_stakes(
            self.new_warmup_cooldown_rate_epoch(),
            self.use_fixed_point_stake_math(),
        );

```
