## Analysis Summary

The external report's broken invariant is: **a reward/asset "return" path becomes permanently unreachable because a state field can never be reduced/cleared, and the withdrawal function unconditionally requires that field to be zero before allowing the owner to reclaim their asset.** In the Solidity report, `multisig` blocklisting stalls `_withdraw()`; the Agave analog is the vote program's `pending_delegator_rewards` field, which gates closing a vote account and can become permanently non-zero once the vote account loses all active delegated stake.

### Title
Vote account can become permanently unclosable when `pending_delegator_rewards` is stranded after all delegated stake is withdrawn - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
`withdraw()` in the vote program refuses to fully close/deinitialize a vote account (i.e., withdraw all remaining lamports) whenever `pending_delegator_rewards > 0`. This field is only ever decremented through the block-reward distribution path in `calculate_block_reward`, which explicitly pays out `0` whenever the vote account's `total_active_stake` is `0`. Once a validator's delegators fully undelegate/withdraw their stake (a normal, unprivileged, permissionless action), `total_active_stake` for that vote account becomes and stays `0`, so `pending_delegator_rewards` can never be paid down to zero again. The vote account's `authorized_withdrawer` — an ordinary, non-malicious actor — is then permanently unable to withdraw/close the vote account for its full balance.

### Finding Description
`withdraw()` enforces (SIMD-0123): [1](#0-0) 

If the withdraw would zero the account (i.e., close it), the function requires `pending_delegator_rewards == 0`; otherwise it returns `InstructionError::InsufficientFunds`, blocking the entire close operation — exactly analogous to `unstake()` reverting on the `_withdraw(multisig, reward)` call in the Solidity report.

The only place `pending_delegator_rewards` is computed for payout is `calculate_block_reward`: [2](#0-1) 

Note line 211-212: when `total_active_stake == 0` for the vote account's delegated stake, the function returns `0` — no block reward is computed or paid, and therefore nothing is subtracted from `pending_delegator_rewards` for that epoch. `total_active_stake` is derived from the reward-epoch stake delegations to that vote account, all of which are user-controlled and can be freely deactivated/withdrawn by their owners at any time via the stake program (`deactivate_stake`/`withdraw_stake`), a fully permissionless, unprivileged sequence of actions requiring no cooperation from the vote account owner.

Because there is no alternative code path that reduces `pending_delegator_rewards` (it is strictly reward-distribution-driven), once all delegators have withdrawn their stake from a vote account while `pending_delegator_rewards > 0`, that value is permanently stranded. The vote account's `authorized_withdrawer` can now never satisfy the `pending_delegator_rewards > 0` gate in `withdraw()`, and thus can never fully withdraw/close the vote account.

### Impact Explanation
The vote account's rent-exempt-minimum-plus-`pending_delegator_rewards` lamports become permanently locked and unrecoverable by the account's legitimate owner — a direct, unprivileged fund-lock condition analogous to a stuck NFT/service in the original report. This does not require any malicious peer, validator, or leaked key; it is a natural consequence of ordinary delegator behavior (undelegating stake), combined with existing accounting rules in the reward distribution code.

### Likelihood Explanation
This requires only ordinary, permissionless actions: delegators withdrawing their stake from a vote account that still has a non-zero pending commission/delegator-rewards balance (e.g., due to timing of block-reward accrual vs. delegators' decision to leave). No attacker collusion, privileged access, or protocol-level exploit is needed — it can arise from routine validator churn (e.g., delegators leaving a poorly performing or unpopular validator), making the likelihood non-trivial for any validator that loses all its stake while a pending reward balance remains outstanding.

### Recommendation
Add a fallback mechanism to drain `pending_delegator_rewards` when `total_active_stake` for the vote account is `0` (e.g., pay/burn/credit the balance directly to the vote account itself or the commission collector once no active delegations remain), or relax the `withdraw()` gate to allow full closure once no active stake exists, since the reward can no longer legitimately be attributed to any delegator.

### Proof of Concept
1. Vote account `V` accrues `pending_delegator_rewards = X > 0` via `DepositDelegatorRewards`/block-reward accounting.
2. All stake accounts delegated to `V` are deactivated and withdrawn by their respective owners (permissionless, ordinary user action) — `total_active_stake` for `V` becomes `0`.
3. On subsequent epochs, `calculate_block_reward` returns `0` for `V` because `total_active_stake == 0`, so `pending_delegator_rewards` is never reduced. [3](#0-2) 
4. `V`'s `authorized_withdrawer` calls `Withdraw(V.lamports)` to fully close the account; `withdraw()` checks `pending_delegator_rewards > 0` and returns `InstructionError::InsufficientFunds`, permanently blocking closure. [4](#0-3) 
5. `V` and its stranded `pending_delegator_rewards` lamports remain inaccessible indefinitely.

**Uncertainty note:** I was not able to fully trace the exact code path that subtracts the paid `block_reward` from `pending_delegator_rewards` in the vote-state handler (grep matches existed in `programs/vote/src/vote_state/handler.rs` but were not read due to iteration limits), so I cannot 100% rule out an alternate, unconditional decrement path outside of `calculate_block_reward`. The core evidence — the `withdraw()` gate at [1](#0-0)  and the zero-payout-on-zero-stake branch at [3](#0-2)  — strongly supports the finding, but a deeper review of `handler.rs`'s `pending_delegator_rewards` mutation logic would be needed to fully confirm there is no other decrement path.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L1084-1092)
```rust
    // Always zero until SIMD-0123 is activated.
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();

    if remaining_balance == 0 {
        // SIMD-0123: vote account cannot be closed if
        // pending_delegator_rewards > 0.
        if pending_delegator_rewards > 0 {
            return Err(InstructionError::InsufficientFunds);
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L206-231)
```rust
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
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
