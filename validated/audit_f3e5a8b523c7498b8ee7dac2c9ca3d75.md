Confirmed: `donateRewards` in `BaseRewardPool.sol` and `BaseRewardPoolV2.sol` (and similarly `mWOMSVBaseRewarder.sol`, `vlMGPBaseRewarder.sol`) is a public, unprivileged function that any wallet can call with any address already flagged `isRewardToken[_rewardToken]`. It calls `_provisionReward`, which does `safeTransferFrom` for `_amountReward` and then unconditionally uses that same `_amountReward` value (not the actual balance received) to update `rewardInfo.rewardPerTokenStored` and `historicalRewards`. If the reward token has fee-on-transfer or rebasing behavior, the pool's accounted `rewardPerTokenStored` will exceed the tokens actually held, causing later claimants via `getReward`/`_sendReward` to fail (insolvency) once the pool is drained by earlier claimants.

### Title
Fee-on-Transfer / Rebasing Reward Token Accounting Mismatch in Reward Pools Leads to Insolvency for Late Claimants - (File: rewards/BaseRewardPool.sol, rewards/BaseRewardPoolV2.sol, rewards/mWOMSVBaseRewarder.sol, rewards/vlMGPBaseRewarder.sol)

### Summary
`_provisionReward` (invoked by the unprivileged, permissionless `donateRewards` function, and also by manager-only `queueNewRewards`) credits `rewardInfo.rewardPerTokenStored` and `historicalRewards` based on the caller-supplied `_amountReward` parameter rather than the actual token balance increase measured before/after the `safeTransferFrom`. Any ERC20 token that is fee-on-transfer or rebasing and is added to `rewardTokens` will cause the pool to overstate distributable rewards, leaving insufficient balance for later claimants.

### Finding Description
In `_provisionReward`: [1](#0-0) 
the function transfers `_amountReward` via `safeTransferFrom` but does not verify how many tokens the pool actually received. It then adds the full nominal `_amountReward` into `rewardInfo.historicalRewards` and folds it into `rewardInfo.rewardPerTokenStored`, which directly determines each staker's claimable amount via `_earned`/`rewardPerToken`. The permissionless `donateRewards` entry point: [2](#0-1) 
lets any wallet (not just the manager) invoke `_provisionReward` for any token already registered in `isRewardToken`. If such a token deducts a transfer fee or rebases downward while held by the pool, `rewardPerTokenStored` becomes inflated relative to the pool's real token balance. The same pattern exists in `rewards/BaseRewardPool.sol` (lines around `_provisionReward`), `rewards/mWOMSVBaseRewarder.sol`, and `rewards/vlMGPBaseRewarder.sol`.

### Impact Explanation
Because claims are paid out via `_sendReward` -> `IERC20(_rewardToken).safeTransfer(_receiver, _amount)` using the inflated `rewardPerTokenStored` accounting, the pool can run out of actual token balance before all stakers have claimed. Earlier claimants receive their full inflated share while later claimants' `getReward`/`getRewards` calls will revert due to insufficient balance, permanently freezing their legitimately accrued (but unbacked) reward entitlement. This matches the accepted class of "theft or permanent freezing of unclaimed yield."

### Likelihood Explanation
Likelihood depends on whether a fee-on-transfer or rebasing token is ever registered as a reward token for a pool (via `queueNewRewards`, which does require a manager to add a *new* token the first time, though `donateRewards` itself needs no privilege once a token is registered). Since the protocol does not document or enforce non-standard token exclusion, and reward tokens are third-party assets whose fee-on-transfer status can change over the token's lifecycle (some tokens add fees via upgradeable/governed mechanisms after listing), this is a realistic, not purely theoretical, scenario for any of the several reward pool contracts in scope.

### Recommendation
In `_provisionReward`, measure the actual balance increase (`balanceBefore`/`balanceAfter` around `safeTransferFrom`) and use that delta — not the caller-supplied `_amountReward` — to update `historicalRewards` and `rewardPerTokenStored`. Alternatively, explicitly document and enforce (e.g., via an allow-list check or decimals/transfer self-test) that only standard, non-fee, non-rebasing ERC20 tokens may be registered as reward tokens.

### Proof of Concept
1. Manager calls `queueNewRewards(1000e18, FOT_TOKEN)` to register a fee-on-transfer reward token that charges a 10% fee on transfer.
2. `_provisionReward` calls `safeTransferFrom(manager, pool, 1000e18)`; pool actually receives only 900e18, but `rewardPerTokenStored` is incremented as if 1000e18 was received.
3. Any user can subsequently call `donateRewards(amount, FOT_TOKEN)` to further compound the mismatch, since `donateRewards` has no access control beyond `isRewardToken` check. [3](#0-2) 
4. Stakers accrue claimable rewards computed off the inflated `rewardPerTokenStored`.
5. When stakers call `getReward`, the first claimants successfully withdraw via `_sendReward`'s `safeTransfer`, but the last claimant(s) find the pool's real `FOT_TOKEN` balance insufficient, and their `safeTransfer` reverts, permanently freezing their earned rewards.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L252-260)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L290-313)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;

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
        emit RewardAdded(_amountReward, _rewardToken);
```
