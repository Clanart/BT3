### Title
`BaseRewardPool.getRewards` is a no-op, permanently freezing already-earned reward tokens that users try to claim through it - (File: rewards/BaseRewardPool.sol)

### Summary
`BaseRewardPool` implements the `IBaseRewardPool` interface's `getRewards(address _account, address _receiver, address[] memory _rewardTokens)` function with an empty body that performs no state changes and transfers no tokens, while the sibling contract `BaseRewardPoolV2` implements the same interface function correctly by iterating the requested tokens and sending accrued rewards to the receiver. This is the same class of bug as the reported `getValueRepaid` issue: a function whose signature/interface promises specific documented behavior (claiming a caller-specified subset of reward tokens for a user) but whose implementation silently ignores its inputs and does nothing, rather than performing the documented action.

### Finding Description
`BaseRewardPool.getReward(_account, _receiver)` correctly iterates over all `rewardTokens`, calculates `userRewards[rewardToken][_account]`, transfers the reward to `_receiver`, and zeroes the balance: [1](#0-0) 

Immediately after it, `getRewards(address _account, address _receiver, address[] memory _rewardTokens)` — a function meant to let a caller claim a specific subset of reward tokens, as its parameters and its counterpart in `BaseRewardPoolV2` indicate — is declared but has a completely empty body: [2](#0-1) 

For comparison, `BaseRewardPoolV2.getRewards` (and `vlMGPBaseRewarder`, which shares this pattern) properly implements this same interface method by applying the `updateRewards` modifier and sending each requested reward token to the receiver: [3](#0-2) 

Because `BaseRewardPool.getRewards` never calls `_updateFor`/`updateReward`, never reads `userRewards`, and never performs any `IERC20.safeTransfer`, any transaction that reaches this specific entry point (e.g. through `MasterMagpie`'s multi-token claim path, which is designed for an ordinary unprivileged wallet to harvest its own accrued yield) will complete successfully (no revert) but transfer zero tokens, effectively swallowing the claim.

### Impact Explanation
Any user who calls the claim path that routes through `BaseRewardPool.getRewards` (as opposed to `getReward`) to harvest specific reward tokens receives nothing back, even though the pool's internal accounting (`userRewards[rewardToken][_account]`) may already reflect that the user is entitled to those tokens. Since the function does not revert, it can give the impression a claim succeeded while it silently discards the harvested rewards, and depending on how the caller (e.g. `MasterMagpie`) treats the call, the rewards may become permanently unclaimable for the user (the entitlement is not reset, but the actual token transfer to the user's wallet never occurs through this path). This falls under theft/permanent freezing of unclaimed yield for any pool still relying on the base (non-V2) `BaseRewardPool` implementation.

### Likelihood Explanation
This requires no privileged role or special conditions — any ordinary wallet holding a staked position with pending rewards on a pool backed by the plain `BaseRewardPool` (not `BaseRewardPoolV2`) contract can trigger this path via the normal multi-token reward-claim flow exposed by `MasterMagpie`. The bug is deterministic (the function body is unconditionally empty), so it manifests on every call, not merely under edge-case conditions.

### Recommendation
Implement `BaseRewardPool.getRewards` with the same logic pattern as `BaseRewardPoolV2.getRewards` / `vlMGPBaseRewarder.getRewards`: update rewards for the requested `_rewardTokens`, read `userRewards[rewardToken][_account]`, zero it, and `safeTransfer` it to `_receiver`, guarded by `onlyMasterMagpie` and an appropriate `updateRewards` modifier, matching the interface's documented multi-token claim semantics.

### Proof of Concept
1. A user stakes into a pool that uses `BaseRewardPool` (not V2) and accrues rewards for multiple reward tokens, so `userRewards[tokenA][user] > 0` and `userRewards[tokenB][user] > 0`.
2. The user (or `MasterMagpie` on their behalf) calls `getRewards(user, user, [tokenA, tokenB])`.
3. The call executes the empty function body at `rewards/BaseRewardPool.sol` lines 242-244, returns successfully, but performs no `safeTransfer` and leaves `userRewards` unchanged.
4. No tokens are received by the user despite the apparently successful transaction, contrasted with the correct behavior in `BaseRewardPoolV2.getRewards` (lines 237-240 shown above) where the same call would transfer the accrued rewards.

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

**File:** rewards/BaseRewardPool.sol (L242-244)
```text
    function getRewards(address _account, address _receiver, address[] memory _rewardTokens) override external {

    }
```

**File:** rewards/BaseRewardPoolV2.sol (L237-240)
```text
    function getRewards(address _account, address _receiver, address[] memory _rewardTokens) override
        external
        onlyMasterMagpie
        updateRewards(_account, _rewardTokens)
```
