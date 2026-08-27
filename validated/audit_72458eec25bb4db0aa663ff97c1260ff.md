### Title
Unbounded Growth of `rewardTokens` Array Causes Permanent Denial-of-Service on Deposit/Withdraw/Claim - ([File: rewards/BaseRewardPool.sol])

### Summary
`BaseRewardPool` accumulates every reward token ever queued into an ever-growing, never-pruned `rewardTokens` array. Every user-facing staking action (deposit, withdraw, harvest, claim) must iterate the *entire* array via `_updateFor` and `getReward`, so gas cost for these operations grows linearly and permanently with the total number of reward tokens ever added to a pool over its lifetime — mirroring the CDS `withdrawUser()` pattern of forcing iteration over all historical liquidation events.

### Finding Description
`BaseRewardPool.rewardTokens` is append-only: `queueNewRewards()` pushes a new token any time an unseen reward token is queued for the pool, and there is no mechanism to ever remove entries. [1](#0-0) 

Both `_updateFor` (invoked via the `updateReward` modifier on `getReward`, which `MasterMagpie` calls on every deposit/withdraw/harvest through `_harvestBaseRewarder`) and `getReward` itself loop over the full, unbounded `rewardTokens.length`: [2](#0-1) [3](#0-2) 

Because these functions execute on the hot path of ordinary, unprivileged user actions (deposit, withdraw, harvest via `MasterMagpie._deposit`/`_withdraw`/`_harvestAndUnstake`, and `_multiClaim`), and the array only grows over the life of a pool as new bribe/incentive tokens are periodically queued, the gas required for a single deposit/withdraw/claim increases monotonically over time with no upper bound — exactly the same structural flaw as the CDS report: an unprivileged user's routine transaction is forced to traverse an ever-growing, protocol-wide history list.

### Impact Explanation
As reward tokens accumulate for a long-lived, popular pool (a normal, non-malicious operational pattern — reward managers are expected to periodically queue new incentive tokens), the gas cost of `_updateFor`/`getReward` (and therefore of `deposit`, `withdraw`, `multiclaim`, `multiclaimFor`, etc.) grows without bound. Eventually this can exceed the block gas limit, making it impossible for any staker in that pool to withdraw their staked tokens or claim/settle rewards — a permanent freezing of user funds, matching the accepted impact bar (permanent freezing of funds / freezing of unclaimed yield).

### Likelihood Explanation
This requires no malicious actor: reward managers routinely add new bonus/incentive tokens to popular pools over time (a normal, expected lifecycle event, not privileged abuse). As the ecosystem grows and more reward tokens are queued into a single pool over months/years, the likelihood of hitting a gas-prohibitive array length increases, particularly for long-running, high-TVL pools that accumulate many different bribe/incentive tokens.

### Recommendation
Bound per-user reward settlement to only tokens the user actually needs to claim (e.g., accept a caller-specified reward-token subset as `getRewards(address,address,address[])` already stubs but does not implement), cap the maximum number of reward tokens a pool can register, or maintain a per-user "active reward token" set updated lazily so routine deposit/withdraw calls do not need to iterate the full historical reward-token list.

### Proof of Concept
1. A reward manager periodically calls `queueNewRewards()` with new distinct `_rewardToken` addresses for a given `BaseRewardPool`, each call appending to `rewardTokens` with no cap. [1](#0-0) 
2. Over time (normal protocol operation, no malicious actor needed), `rewardTokens.length` grows large.
3. Any user calling `withdraw`/`deposit`/`multiclaim` on `MasterMagpie` triggers `_harvestBaseRewarder` → `BaseRewardPool.getReward` → the `updateReward` modifier's `_updateFor`, both of which loop `for (uint256 index = 0; index < length; ++index)` over the full array. [2](#0-1) 
4. Once the loop's gas cost approaches the block gas limit, all withdrawals/claims/deposits for that pool permanently revert for every staker, freezing their principal and unclaimed rewards.

### Citations

**File:** rewards/BaseRewardPool.sol (L219-240)
```text
    /// @notice Calculates and sends reward to user. Only callable by masterMagpie
    /// @param _account Address account
    function getReward(address _account, address _receiver)
        override
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            uint256 reward = userRewards[rewardToken][_account]; // updated during updateReward modifier
            if (reward > 0) {
                userRewards[rewardToken][_account] = 0;
                IERC20(rewardToken).safeTransfer(_receiver, reward);
                emit RewardPaid(_account, _receiver, reward, rewardToken);
            }
        }

        return true;
    }
```

**File:** rewards/BaseRewardPool.sol (L261-274)
```text
    function queueNewRewards(uint256 _amountReward, address _rewardToken)
        override
        external
        onlyManager
        returns (bool)
    {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }

        _provisionReward(_amountReward, _rewardToken);
        return true;
    }
```

**File:** rewards/BaseRewardPool.sol (L288-295)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            userRewards[rewardToken][_account] = earned(_account, rewardToken);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
    }
```
