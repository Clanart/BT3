## Title
Per-delegation clamp in `calculate_block_reward` does not bound the *aggregate* block-reward minted against a vote account's finite `pending_delegator_rewards` pool - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

### Summary
This is a direct structural analog of the Perpetual "funding fee" bug: a per-share reward rate is derived from one aggregate quantity (`total_active_stake`) and applied to a set of individual quantities (`stake` per delegation) whose sum is *assumed* — but not verified — to equal that aggregate. The code even documents, in a comment, that the assumption can be violated ("it's possible to have `stake > total_active_stake`"), and only clamps each *individual* term, exactly like the Perpetual protocol's per-position funding-fee calculation that assumed `Σ(long open notional) == Σ(short open notional)` without enforcing it.

### Finding Description
`calculate_block_reward` computes each stake delegation's share of a vote account's `pending_delegator_rewards` pool as: [1](#0-0) 

```
(pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
    .try_into().unwrap_or(u64::MAX)
    .min(pending_delegator_rewards)
```

The comment directly above explains the precondition can fail: [2](#0-1)  "During recalculation, if stake account has already received rewards, it's possible to have `stake > total_active_stake`. ... We can also have individual rewards look greater than the pending rewards. This is harmless in practice, but we clamp it just to be safe."

This clamp only bounds a *single* delegation's share to `pending_delegator_rewards`; it does not verify that `Σ(stake_i)` over all delegations to that vote account equals `total_active_stake` (the denominator), nor that the *sum* of all clamped block-reward payouts for that vote account stays within `pending_delegator_rewards`. If `total_active_stake` (taken from a possibly-stale `RewardEpochDelegatedStakes` snapshot) understates the true, live sum of `stake` values used per delegation (e.g., due to recalculation reusing already-rewarded stake amounts, or warmup/cooldown/fixed-point-math differences per `delegation_effective_stake`), each delegation's computed share can independently approach or hit the `pending_delegator_rewards` ceiling, and the resulting `block_reward` amounts are unconditionally minted into stake accounts: [3](#0-2) 

```
account.checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)...
account.checked_add_lamports(partitioned_stake_reward.block_reward)...
```

Crucially, in `distribute_epoch_rewards_in_partition` only `stake_reward_lamports_minted` (inflation reward) is added to `capitalization`, while `block_reward_lamports_distributed` is not treated as a capitalization increase — implying the protocol's own accounting model assumes `block_reward` lamports were already backed 1:1 by lamports physically sitting in the vote account (deposited via `deposit_delegator_rewards`), not newly minted: [4](#0-3) 

Within the scope I was able to inspect, I could not locate any code path that decrements the vote account's `pending_delegator_rewards` field (or subtracts real lamports from the vote account balance) to match the block rewards actually minted into stake accounts across a distribution pass — `add_pending_delegator_rewards` is the only mutator I found, and it only ever increases the counter on deposit: [5](#0-4) 

If the aggregate-share invariant assumed by `calculate_block_reward` is broken (per the code's own comment) and/or `pending_delegator_rewards`/vote-account balance is never reconciled against what was actually minted to stakers, the vote account's advertised pool can be over- or under-drawn: either stakers are collectively paid more in `block_reward` than the vote account ever received (an accounting/capitalization mismatch — "excess funds" analog), or, since `pending_delegator_rewards` never appears to decrease, the same nominal pool is used to justify a fresh mint every epoch instead of being consumed once (a "distribute-forever" leak, the AG analog of a `PnL pool` never being debited).

### Impact Explanation
This concerns validator/delegator reward accounting, a core part of the runtime's stake-reward/capitalization consensus rules. A mismatch here means:
- Individual stake accounts are minted `block_reward` lamports that are not proven to have been deposited/available, corrupting the "sum of all lamports == capitalization" invariant that every other part of the runtime (e.g. transaction fee distribution, rent, and stake rewards) is careful to enforce with explicit asserts (compare with the `assert!` check in `distribute_reward_commissions` guarding against over-paying the calculated `point_value.rewards`, which has no equivalent for block rewards: [6](#0-5) ).
- Because reward calculation/distribution is executed identically by every validator from deterministic on-chain state, any divergence here would not cause consensus disagreement by itself, but it would silently create or destroy lamports relative to the vote account's actual funded balance, i.e. false minting/false accounting of funds — analogous to the Sherlock "excess funds/bad debt" finding, just without a market-driven trigger.

### Likelihood Explanation
The precondition violation (`stake > total_active_stake`) is explicitly acknowledged as reachable by the authors ("During recalculation, if stake account has already received rewards, it's possible to have `stake > total_active_stake`"), meaning it is not a hypothetical edge case but a known, accepted possibility in the current design, mirroring the Perpetual report's judged "medium, specific-state-required" severity rather than a trivially-triggerable exploit. I was not able to fully trace whether a compensating invariant (e.g. an aggregate cap check, or a decrement of `pending_delegator_rewards`/vote-account lamports elsewhere in the codebase outside the files I reviewed) exists to neutralize this; that uncertainty should be resolved before treating this as conclusively exploitable.

### Recommendation
- Enforce the invariant at the aggregate level: track cumulative `block_reward` paid out per vote account within a distribution pass and cap the total at `pending_delegator_rewards`, rather than clamping each delegation independently.
- Explicitly decrement `pending_delegator_rewards` (and the corresponding real lamports) on the vote account as block rewards are distributed, so the pool is provably consumed exactly once and cannot be reused as the basis for a subsequent mint.
- Add a runtime assertion analogous to the one in `distribute_reward_commissions` ("verify that we didn't pay any more than we expected to") for the block-reward path, so that `Σ(block_reward_i) <= pending_delegator_rewards` is enforced and any violation fails loudly instead of silently minting lamports.

### Proof of Concept
Conceptual trigger (not fully verified end-to-end due to inability to trace all downstream decrement logic within the available exploration budget):
1. A vote account accumulates `pending_delegator_rewards = P` via `DepositDelegatorRewards`.
2. During epoch-boundary reward *recalculation* (a supported code path per the comment in `calculate_block_reward`), the `RewardEpochDelegatedStakes.delegated_stakes[vote_pubkey]` snapshot (`total_active_stake`) becomes stale/lower relative to the live `stake` values fed into `delegation_effective_stake` for each delegation (e.g., because some stake accounts already reflect prior rewards or warmup/cooldown adjustments).
3. For N delegations to the same vote account, each computed share `P * stake_i / total_active_stake` individually clamps to `P`, but since no aggregate check exists, the sum of minted `block_reward` amounts across all N delegations can exceed `P`.
4. Stake accounts are credited via `checked_add_lamports(block_reward)` in `build_updated_stake_reward` for amounts summing to more than `P`, while `pending_delegator_rewards` on the vote account is never correspondingly reduced and `block_reward_lamports_distributed` is excluded from the capitalization delta in `distribute_epoch_rewards_in_partition`, producing lamports whose origin is not reconciled against real deposits.

Given the reasoning-effort and iteration constraints of this session, I was unable to confirm whether a separate mechanism elsewhere in the runtime forecloses this scenario (e.g., a per-vote-account aggregate cap computed before calling `calculate_block_reward`, or a decrement of `pending_delegator_rewards` performed in code I did not reach). This should be verified with a full-repository review (e.g., a Devin session with complete file access) before treating this as a confirmed, exploitable vulnerability.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L211-231)
```rust
    if total_active_stake == 0 {
        0
    } else {
        let stake = delegation_effective_stake(
            delegation,
            rewarded_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        // During recalculation, if stake account has already received rewards,
        // it's possible to have `stake > total_active_stake`. If
        // `pending_delegator_rewards` is a huge number, we could potentially
        // overflow a `u64`. We can also have individual rewards look greater
        // than the pending rewards. This is harmless in practice, but we
        // clamp it just to be safe
        (pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
            .try_into()
            .unwrap_or(u64::MAX)
            .min(pending_delegator_rewards)
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L397-408)
```rust
        // verify that we didn't pay any more than we expected to
        assert!(
            point_value.rewards
                >= distributed_lamports
                    + distributed_to_incinerator_lamports
                    + burned_lamports
                    + total_stake_rewards_lamports,
            "point_value={point_value:?}, distributed_lamports={distributed_lamports}, \
             distributed_to_incinerator_lamports={distributed_to_incinerator_lamports} \
             burned_lamports={burned_lamports}, \
             total_stake_rewards_lamports={total_stake_rewards_lamports}"
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L192-204)
```rust
        // increase total capitalization by the distributed rewards
        self.capitalization
            .fetch_add(stake_reward_lamports_minted, Relaxed);

        // decrease total capitalization by burned block rewards
        self.capitalization
            .fetch_sub(block_reward_lamports_burned, Relaxed);

        // decrease distributed capital from epoch rewards sysvar
        self.update_epoch_rewards_sysvar(
            stake_reward_lamports_minted + stake_reward_lamports_burned,
            block_reward_lamports_distributed + block_reward_lamports_burned,
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-267)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
```

**File:** programs/vote/src/vote_state/handler.rs (L196-209)
```rust
    pub(crate) fn add_pending_delegator_rewards(
        &mut self,
        amount: u64,
    ) -> Result<(), InstructionError> {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => {
                v4.pending_delegator_rewards = v4
                    .pending_delegator_rewards
                    .checked_add(amount)
                    .ok_or(InstructionError::ArithmeticOverflow)?;
                Ok(())
            }
        }
    }
```
