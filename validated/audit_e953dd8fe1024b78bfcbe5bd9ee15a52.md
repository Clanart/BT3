### Title
Division by Zero in `DelegateVoteRewardPool._updateVote` Permanently Freezes Staked Funds - (File: rewards/DelegateVoteRewardPool.sol)

### Summary
`DelegateVoteRewardPool._updateVote()` divides `votingWeights[pool] * totalSupply` by `totalWeight` without checking whether `totalWeight` is zero. Since `totalWeight` can legitimately reach zero while `votePools` still contains entries, every subsequent call to `stakeFor`/`withdrawFor` reverts, permanently freezing any vlMGP already staked in the delegate pool.

### Finding Description
`_updateVote()` iterates over `votePools` and computes, for each pool, `targetVote = (votingWeights[pool] * totalSupply) / totalWeight` with no zero-guard: [1](#0-0) 

`totalWeight` and `votingWeights[lp]` are only mutated in `updateWeight`: [2](#0-1) 

The pool is added to `votePools` the first time `updateWeight` is called for it, regardless of the `weight` argument (`0` is a valid input). If a pool's weight is later reduced to `0` (e.g. deprecating a sub-pool's allocation without removing it from `votePools`), or if the first weight set for a pool is `0`, `totalWeight` becomes `0` while `votePools.length > 0`. From that point on, any call that triggers `_updateVote()` unconditionally divides by `totalWeight`, reverting.

`_updateVote()` is invoked from `stakeFor` and `withdrawFor`, which are `onlyOperator`-gated (the operator being `WombatBribeManager`) and are reached whenever an ordinary vlMGP holder calls `WombatBribeManager.vote()` targeting the LP pool whose `rewarder` is set to this `DelegateVoteRewardPool`: [3](#0-2) 

This mirrors the reported bug class: a value (`weight`/`totalWeight`) that is expected to be non-zero in normal operation can become zero through ordinary configuration/weight-adjustment flow, and the missing guard causes the division to revert instead of degrading gracefully.

### Impact Explanation
Once `totalWeight` is zero with a non-empty `votePools` array, `stakeFor` and `withdrawFor` both permanently revert for any user routed through this rewarder. Because `withdrawFor` is unreachable, vlMGP tokens already staked in the delegate pool become permanently unwithdrawable, and reward accounting/voting for the affected pool is frozen indefinitely — this is a permanent freezing-of-funds condition (well beyond a 24-hour freeze), not merely a gas/no-impact issue.

### Likelihood Explanation
No malicious privileged action is required — a routine, legitimate weight adjustment (e.g., an owner setting a new pool's initial weight to `0`, or reducing an existing pool's weight to `0` while temporarily deprioritizing it) is sufficient to zero `totalWeight` while `votePools` remains non-empty. From that point, any ordinary user calling the standard `vote()`/stake/withdraw path against the affected pool triggers the revert — no special privileges are needed to hit the bug once the state exists.

### Recommendation
Guard the division in `_updateVote()`:
```solidity
uint256 targetVote = totalWeight == 0
    ? 0
    : (votingWeights[pool] * totalSupply) / totalWeight;
```
Additionally consider removing pools from `votePools` when their weight is set to `0` in `updateWeight`, so `totalWeight == 0` and `votePools.length > 0` cannot co-exist.

### Proof of Concept
1. Owner calls `updateWeight(lpA, 0)` for the first vote pool associated with the delegate rewarder — `votePools = [lpA]`, `totalWeight = 0`.
2. A vlMGP holder calls `WombatBribeManager.vote()` for the LP whose `rewarder` is this `DelegateVoteRewardPool`, which internally calls `stakeFor(user, amount)`.
3. `stakeFor` calls `_updateVote()`, which computes `(votingWeights[lpA] * totalSupply) / totalWeight` → division by zero → transaction reverts.
4. Any subsequent `stakeFor`/`withdrawFor` call reverts identically, so users who already staked cannot withdraw their vlMGP from the pool.

### Citations

**File:** rewards/DelegateVoteRewardPool.sol (L57-82)
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

    function withdrawFor(
        address _for,
        uint256 _amount,
        bool _claim
    ) external override onlyOperator updateRewards(_for, rewardTokens) {
        totalSupply = totalSupply - _amount;
        _balances[_for] = _balances[_for] - _amount;
        _updateVote();

        emit Withdrawn(_for, _amount);

        if (_claim) {
            _getReward(_for);
        }
    }
```

**File:** rewards/DelegateVoteRewardPool.sol (L132-143)
```text
    function _updateVote() internal {
        uint256 length = votePools.length;
        int256[] memory deltas = new int256[](length);
        for (uint256 index = 0; index < length; ++index) {
            address pool = votePools[index];
            uint256 targetVote = (votingWeights[pool] * totalSupply) /
                totalWeight;
            uint256 currentVote = _getVoteForLp(pool);
            deltas[index] = int256(targetVote) - int256(currentVote);
        }
        IWombatBribeManager(operator).vote(votePools, deltas);
    }
```

**File:** rewards/DelegateVoteRewardPool.sol (L207-217)
```text
    function updateWeight(address lp, uint256 weight) external onlyOwner {
        if (lp == address(this)) revert InvalidPoolTokenAddress();
        if (!isVotePool[lp]) {
            if (!IWombatBribeManager(operator).isPoolActive(lp))
                revert PoolNotActive();
            isVotePool[lp] = true;
            votePools.push(lp);
        }
        totalWeight = totalWeight - votingWeights[lp] + weight;
        votingWeights[lp] = weight;
    }
```
