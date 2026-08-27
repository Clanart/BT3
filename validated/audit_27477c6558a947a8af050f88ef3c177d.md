### Title
Reward-per-token accumulator with no vesting allows flash-stake sandwich theft of donated rewards - (File: rewards/BaseRewardPoolV2.sol)

### Summary
`BaseRewardPoolV2._provisionReward` (invoked by both the permissionless `donateRewards` and the manager-only `queueNewRewards`) instantly and fully bakes the entire donated reward amount into `rewardPerTokenStored`, dividing by `totalStaked()` sampled at that single block. Because there is no streaming/duration mechanism (unlike Synthetix-style `rewardRate * rewardsDuration`), an attacker who deposits a large stake immediately before a donation and withdraws immediately after claiming captures a full, disproportionate, instantaneous share of the entire reward relative to zero time-weighted contribution, diluting and stealing yield from genuine long-term stakers.

### Finding Description
`_provisionReward` computes the reward-per-token delta as: [1](#0-0) 
i.e. `rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked()`, where `totalStaked()` reads the live balance of the staking token held by the operator (MasterMagpie) at the exact moment of the call: [2](#0-1) 

`donateRewards` is a fully public, unauthenticated entry point that triggers this update: [3](#0-2) 

A user's claimable reward is computed at claim time as `balanceOf(account) * (rewardPerToken - userRewardPerTokenPaid) + userRewards`: [4](#0-3) 

`balanceOf` is read live from MasterMagpie's `stakingInfo`, not snapshotted at deposit time: [5](#0-4) 

Exploit flow:
1. Attacker calls `MasterMagpie.deposit(stakingToken, largeAmount)` (public, only `whenNotPaused nonReentrant`, no lock-up or cooldown observed) [6](#0-5) .
2. Attacker (or anyone) calls `donateRewards(reward, token)` or front-runs a manager's `queueNewRewards` call. `rewardPerTokenStored` jumps by `reward * 1e_dec / (S_original + D_attacker)`.
3. Attacker calls `multiclaimSpec`/`multiclaim` to harvest, receiving `D_attacker / (S_original + D_attacker) * reward` [7](#0-6) .
4. Attacker calls `MasterMagpie.withdraw` to exit immediately, with no delay: [8](#0-7) .

Because the reward is applied as a single instantaneous jump to a global accumulator (not streamed via a `rewardRate`/`periodFinish` mechanism), the attacker's near-zero holding duration captures the same pro-rata share as a staker who had been staked for the entire accrual period. Existing long-term stakers receive `S_original / (S_original + D_attacker) * reward` instead of the full `reward` they would have accrued absent the attacker's presence — this is a direct value transfer (theft) from long-term stakers to the flash-staker. Nothing in `donateRewards`, `queueNewRewards`, `deposit`, `withdraw`, or `multiclaimSpec` — no `nonReentrant` conflict, no time-lock, no vesting curve — prevents this, since these are legitimate calls performed as ordinary transactions/blocks, not a reentrancy.

### Impact Explanation
This results in direct theft of unclaimed yield from legitimate stakers, siphoned into the flash-staking attacker's wallet with a bounded, calculable capital requirement and no meaningful holding-period risk (funds only exposed for one to two blocks). This matches the Immunefi impact class "Theft of unclaimed yield" / direct theft of user funds via reward-pool manipulation.

### Likelihood Explanation
- `donateRewards` is fully permissionless — no precondition beyond holding/approving the reward token — so the attacker can self-trigger the whole sandwich in a single transaction, or front-run any manager's periodic `queueNewRewards` reward distribution (a routine, expected, and mempool-visible operation).
- `deposit`/`withdraw` in `MasterMagpie` have no lock-up, cooldown, or minimum staking duration found in the reviewed code, making same-block or same-epoch entry/exit feasible.
- Capital requirement is only the size of stake needed to inflate `totalStaked()` sufficiently to capture a meaningful share; this can be obtained via a flash loan of the staking/receipt token if it is liquid, or simply via attacker's own capital held briefly.
- This is repeatable against every future `donateRewards`/`queueNewRewards` call, and observable in the mempool for front-running.

### Recommendation
Replace the instantaneous full-application accumulator model with a time-weighted/streamed distribution (Synthetix-style `rewardRate` + `periodFinish`/`rewardsDuration`), so that a newly-added reward accrues gradually over a fixed duration rather than being fully captured by whoever holds the largest share of `totalStaked()` at the exact block of donation. Alternatively, snapshot and time-weight balances (e.g., checkpoint-based or minimum-staking-duration gating) so that reward claims are proportional to actual staking duration rather than instantaneous balance at donation time.

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, `BaseRewardPoolV2`, staking token, reward token; register a manager and reward token.
2. Have "Alice" (long-term staker) deposit `S` staking tokens and let some time pass with no rewards.
3. Attacker deposits `D >> S` staking tokens into the same pool via `MasterMagpie.deposit`.
4. Attacker (or a simulated manager) calls `donateRewards(R, rewardToken)`/`queueNewRewards(R, rewardToken)` in the same block.
5. Attacker calls `MasterMagpie.multiclaimSpec` (or `multiclaim`) to harvest reward, then immediately calls `MasterMagpie.withdraw(stakingToken, D)`.
6. Assert: attacker's harvested reward ≈ `D/(S+D) * R`, captured within one block with zero staking-duration cost, while Alice's subsequent `earned()` for the same reward token is reduced to `S/(S+D) * R` versus the `R` (or a duration-weighted fair share) she would have received absent the attacker's flash stake — demonstrating the disproportionate capture and the resulting dilution/theft from the honest staker.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L126-128)
```text
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L133-136)
```text
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L255-260)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L301-312)
```text
        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingTokenDecimals) /
                totalStaked();
        }
```

**File:** rewards/BaseRewardPoolV2.sol (L316-321)
```text
    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```

**File:** rewards/MasterMagpie.sol (L337-339)
```text
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }
```

**File:** rewards/MasterMagpie.sol (L344-346)
```text
    function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _withdraw(_stakingToken, msg.sender, _amount, false);
    }
```

**File:** rewards/MasterMagpie.sol (L406-410)
```text
    function multiclaimSpec(address[] calldata _stakingTokens, address[][] memory _rewardTokens)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, msg.sender, msg.sender, _rewardTokens);
    }
```
