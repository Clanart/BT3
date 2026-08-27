### Title
Permissionless `DelegateVoteRewardPool.harvestAll()` lets a just-in-time depositor snipe already-accrued bribe yield from long-term delegators - ([File: rewards/DelegateVoteRewardPool.sol])

### Summary
`harvestAll()` has no access control and can be called by anyone at any time to pull all pending bribes from Wombat and instantly fold them into `rewardPerTokenStored` divided by the *current* `totalSupply`. Because the reward-per-token update is instantaneous rather than streamed over time, an attacker who stakes into the delegate pool immediately before calling `harvestAll()` captures a pro-rata share of bribes that accrued entirely before their deposit, diluting the honest, time-weighted depositors.

### Finding Description
`harvestAll()` is declared with no modifier at all: [1](#0-0) 
It calls `IWombatBribeManager(operator).claimAllBribes(address(this))` and immediately routes the proceeds through `_manageRewards` → `_queueNewRewardsWithoutTransfer`, which takes a protocol fee and then updates `rewardPerTokenStored` by dividing the freshly-claimed amount by the pool's current `totalSupply`: [2](#0-1) 

The accounting model (`rewardPerTokenStored`, `userRewardPerTokenPaid`, `earned`) is the standard Synthetix-style instantaneous index, not a time-streamed reward: [3](#0-2) 

Staking into the pool is gated by `onlyOperator` (the pool's `operator` is the `WombatBribeManager`, per the interface used in `harvestAll`), so an attacker cannot call `stakeFor` directly: [4](#0-3) 
The `updateRewards` modifier sets `userRewardPerTokenPaid[_for]` to the *pre-stake* `rewardPerTokenStored` value and only afterward is `totalSupply`/`_balances` incremented: [5](#0-4) 

The exploit flow: an attacker deposits into the delegate pool (through whatever legitimate WombatBribeManager staking/delegation entry point invokes `stakeFor`), increasing `totalSupply` by `X` on top of the existing `T`. If bribes `R` have accrued in Wombat's voter contract but not yet been harvested, the attacker then calls the now-public `harvestAll()` themselves (rather than waiting for `WombatBribeManager.castVotes` to be invoked by its normal caller/schedule). This bumps `rewardPerTokenStored` by `R * 10^dec / (T + X)`. Because `X` is now part of the divisor, and because the attacker's `userRewardPerTokenPaid` was frozen at the pre-harvest value, the attacker immediately earns `X * R / (T + X)` — a nonzero share of rewards that were entirely accrued by other depositors' time-weighted stake before the attacker ever participated. Existing depositors receive a correspondingly smaller effective share because the same `R` is now spread over a larger `totalSupply`.

No `nonReentrant` guard, no minimum holding period, and no restriction on who may call `harvestAll()` exist to prevent this timing attack; the only gate is that staking itself must go through the operator (`WombatBribeManager`), but the attacker only needs the capability to become a delegator for a single block, not any minimum duration.

### Impact Explanation
This is a theft of unclaimed yield: honest long-term delegators who caused the bribes to accrue have their entitlement diluted, while the attacker extracts value proportional to a stake held for effectively zero time. This matches the "theft of unclaimed yield" impact class named in scope.

### Likelihood Explanation
Preconditions: (1) delegate pool has unharvested bribes recorded in Wombat's voter, and (2) attacker can get `stakeFor` invoked with a large amount in the same block before calling `harvestAll()`. The attacker needs real capital to become a delegator (the legitimate deposit path through `WombatBribeManager`, not a free flash-loan-style call), but does not need to hold that capital for any duration beyond the single block — the public, gateless `harvestAll()` is what lets the attacker choose the exact instant to trigger the harvest for maximum extraction rather than being at the mercy of whenever `castVotes` is normally invoked. This is repeatable every time bribes accumulate between harvests, and capital requirements scale only with the size of the pending bribe pool the attacker wants to capture a share of, making it fairly practical to time profitably for large delegate pools with periodic bribe accrual.

### Recommendation
Restrict `harvestAll()` to `onlyOperator`/`onlyManager` (matching the pattern used elsewhere in the pools) so harvest timing cannot be attacker-controlled, and/or change the reward distribution to stream newly harvested rewards over time (rate-based accrual) rather than instantly folding them into `rewardPerTokenStored`, so JIT deposits cannot capture rewards earned before the deposit. Additionally, consider requiring a minimum stake duration before a depositor's balance counts toward reward-per-token calculations for freshly queued rewards.

### Proof of Concept
Foundry test outline:
1. Deploy `DelegateVoteRewardPool` with mock `WombatBribeManager` (operator) that supports `stakeFor`, `withdrawFor`, `claimAllBribes`, and `vote`.
2. Have an honest depositor call (via operator) `stakeFor(honest, T)`; advance many blocks; have the mock `WombatBribeManager.claimAllBribes` accrue `R` bribe tokens payable on the next call.
3. In a single block: attacker triggers the operator's stake path to call `stakeFor(attacker, X)`, then attacker directly calls `delegatePool.harvestAll()`.
4. Assert `earned(attacker, rewardToken) == X * R / (T + X)` (nonzero despite zero prior holding time), and assert `earned(honest, rewardToken) < R` (diluted below the pre-attack full entitlement of `R`), confirming yield theft via JIT staking combined with permissionless harvest timing.

### Citations

**File:** rewards/DelegateVoteRewardPool.sol (L57-66)
```text
    function stakeFor(
        address _for,
        uint256 _amount
    ) external override onlyOperator updateRewards(_for, rewardTokens) {
        totalSupply = totalSupply + _amount;
        _balances[_for] = _balances[_for] + _amount;
        _updateVote();

        emit Staked(_for, _amount);
    }
```

**File:** rewards/DelegateVoteRewardPool.sol (L97-103)
```text
    function harvestAll() external {
        (
            address[] memory rewardTokensList,
            uint256[] memory earnedRewards
        ) = IWombatBribeManager(operator).claimAllBribes(address(this));
        _manageRewards(rewardTokensList, earnedRewards);
    }
```

**File:** rewards/DelegateVoteRewardPool.sol (L178-203)
```text
    function _queueNewRewardsWithoutTransfer(
        uint256 _amountReward,
        address _rewardToken
    ) internal {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;
        if (totalSupply == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10 ** this.stakingDecimals()) /
                totalSupply;
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPool.sol (L173-185)
```text
    function earned(address _account, address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return (
            (((balanceOf(_account) *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                (10**stakingDecimals())) + userRewards[_rewardToken][_account])
        );
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L107-120)
```text
    modifier updateRewards(address _account, address[] memory _rewards) {
        uint256 length = _rewards.length;
        uint256 userShare = balanceOf(_account);
        
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewards[index];
            // if a reward stopped queuing, no need to recalculate to save gas fee
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;
            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userShare);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
        _;
    }    
```
