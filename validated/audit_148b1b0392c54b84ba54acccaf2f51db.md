### Title
Fee-on-transfer/rebasing reward tokens allow reward index inflation beyond actual pool balance - (File: rewards/BaseRewardPoolV2.sol)

### Summary
`_provisionReward()` credits `historicalRewards` and increments `rewardPerTokenStored` using the caller-supplied `_amountReward` parameter rather than the actual balance delta received after `safeTransferFrom`. Any registered reward token that delivers less than the requested amount on transfer (deflationary/fee-on-transfer) — or whose balance can shrink post-transfer (rebasing) — lets the pool's per-token accounting outpace the tokens actually escrowed, so later stakers cannot all be paid in full.

### Finding Description
`donateRewards(uint256 _amountReward, address _rewardToken)` at [1](#0-0)  only checks `isRewardToken[_rewardToken]` and forwards straight to `_provisionReward`. That function performs: [2](#0-1) 

The transfer is done via `safeTransferFrom(msg.sender, address(this), _amountReward)`, but the accounting that follows (`historicalRewards += _amountReward`, `queuedRewards += _amountReward`, and the `rewardPerTokenStored` increment `(_amountReward * 10**decimals) / totalStaked()`) all use the *requested* `_amountReward`, not the actual balance increase of the pool. There is no `balanceOf(this)` before/after check.

If `_rewardToken` is a fee-on-transfer token (transfers less than `_amountReward`) or a rebasing token (balance can decrease independent of transfers), the index (`rewardPerTokenStored`) is inflated relative to the tokens the contract actually holds. Since `_earned()` computes user entitlement purely from `rewardPerTokenStored` deltas [3](#0-2) , and `_sendReward` performs a plain `safeTransfer` of the computed amount [4](#0-3) , eventually some staker's claim will revert or exhaust the pool's remaining balance of that token, leaving other stakers unable to claim their promised share — an insolvency for that reward token.

No modifier (`onlyManager`, `nonReentrant`, pausability) protects `donateRewards`; it is intentionally open to anyone so third parties can donate rewards, and there is no reward-token allow-listing beyond "already registered," so if a fee-on-transfer/rebasing token is ever added as a reward token (via `queueNewRewards`, which pushes new tokens into `rewardTokens`/`isRewardToken`), this path is directly and repeatedly exploitable by any unprivileged caller holding a nonzero balance of that token — no flash loan of the staking token is actually required for this specific defect, since `donateRewards` requires no staked position.

### Impact Explanation
This breaks the stated invariant that credited rewards must equal the balance delta actually received. Over repeated donations, `rewardPerTokenStored` diverges upward from the pool's real reward-token balance, meaning the sum of all stakers' `earned()` claims can exceed the pool's holdings of that reward token. This is a protocol insolvency for the affected reward token: some legitimate stakers will be unable to withdraw their earned rewards (transfer revert due to insufficient balance), a permanent loss/freezing of unclaimed yield for at least the last claimants.

### Likelihood Explanation
The vulnerability triggers only when a registered reward token has fee-on-transfer or rebasing (balance-decreasing) semantics. If all currently registered reward tokens are standard ERC-20 without transfer fees or rebasing, the current instance is not exploitable, but the code contains no defense against such a token being added later, and no owner/manager verification step catches this at add-time. Given `donateRewards` is intentionally permissionless, exploitation (once a suitable token is registered) requires no special capital, no flash loan, and no privileged role — only the attacker's own tokens, and it is repeatable every call.

### Recommendation
Measure the actual balance delta instead of trusting `_amountReward`: record `IERC20(_rewardToken).balanceOf(address(this))` before and after the `safeTransferFrom`, and use `after - before` for all subsequent accounting (`historicalRewards`, `queuedRewards`, and the `rewardPerTokenStored` increment). Optionally, reject reward tokens known to be fee-on-transfer/rebasing at registration time, or require a minimum-received check consistent with the true transferred amount.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `BaseRewardPoolV2` with `stakingToken` and a first `_rewardToken`.
2. Deploy a mock fee-on-transfer ERC20 (e.g., burns 10% of every transfer) and register it as a reward token via `queueNewRewards` (as the test-configured manager) or via constructor.
3. Have at least one staker stake in `MasterMagpie`/mock operator so `totalStaked() > 0`.
4. From an unprivileged EOA holding the fee-on-transfer token, call `donateRewards(1000e18, feeToken)`.
5. Assert: `feeToken.balanceOf(pool)` increased by only 900e18 (after 10% fee), but `rewards[feeToken].rewardPerTokenStored` was incremented as if 1000e18 was received (`(1000e18 * 10**decimals) / totalStaked()`).
6. Have stakers accumulate `earned()` claims that sum to more than `feeToken.balanceOf(pool)`.
7. Assert that a later staker's `getReward`/`getRewards` call reverts or under-pays due to insufficient token balance, demonstrating the reconciliation break between `queuedRewards`/`rewardPerTokenStored` and actual holdings.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L255-260)
```text
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

**File:** rewards/BaseRewardPoolV2.sol (L316-321)
```text
    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L323-327)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver, uint256 _amount) internal {
        userRewards[_rewardToken][_account] = 0;
        IERC20(_rewardToken).safeTransfer(_receiver, _amount);
        emit RewardPaid(_account, _receiver, _amount, _rewardToken);
    }
```
