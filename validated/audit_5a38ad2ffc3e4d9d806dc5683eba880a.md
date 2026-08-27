### Title
`donateRewards` accepts fee-on-transfer reward tokens without reconciling actual received balance, letting any unprivileged caller inflate `rewardPerTokenStored`/`queuedRewards` beyond backed funds - (File: `rewards/BaseRewardPool.sol`)

### Summary
`donateRewards` is a public, unprivileged entrypoint that funnels straight into `_provisionReward`, which credits `historicalRewards`, `queuedRewards`, and `rewardPerTokenStored` using the *requested* `_amountReward` rather than the tokens actually received by the contract. If the registered `_rewardToken` charges a transfer fee, an attacker can call `donateRewards` with such a token to make the pool's reward accounting promise more tokens than it actually holds, permanently freezing part of the yield for later claimants whose `safeTransfer` will revert on insufficient balance.

### Finding Description
`_provisionReward` performs `IERC20(_rewardToken).safeTransferFrom(msg.sender, address(this), _amountReward)` and then unconditionally uses `_amountReward` (the pre-fee, requested value) for all downstream accounting: [1](#0-0) 

- If `totalStaked() == 0`, the *full* `_amountReward` is added to `rewardInfo.queuedRewards`, even though a fee-on-transfer token would have delivered less than `_amountReward` to the contract.
- Otherwise, `rewardPerTokenStored` is incremented by `(_amountReward * 10**stakingDecimals()) / totalStaked()`, again using the pre-fee `_amountReward`.

`donateRewards` has no access control beyond requiring the token to already be registered via `isRewardToken[_rewardToken]`: [2](#0-1) 

Because any unprivileged EOA can call `donateRewards` directly, and `_provisionReward` never compares balance-before/balance-after to determine the actual amount received, a fee-on-transfer reward token lets the caller inject an accounting entry (`queuedRewards` and/or `rewardPerTokenStored`) that overstates the tokens truly transferred into the pool. Once that inflated `queuedRewards` is folded into a subsequent legitimate `queueNewRewards` (which shares the same `_provisionReward` code path) at line 310-313, the resulting `rewardPerTokenStored` increment is computed from an inflated numerator, so `earned()` (which reads `rewardPerToken`) promises more reward than the contract can pay: [3](#0-2) 

No existing modifier (`onlyManager`, `onlyMasterMagpie`, `updateReward`) protects `donateRewards` from this, and there is no balance-delta check anywhere in `_provisionReward` to detect/reject fee-on-transfer behavior.

### Impact Explanation
When `rewardPerTokenStored`/`queuedRewards` becomes unbacked by real token balance, the last staker(s) attempting `getReward` will have `IERC20(rewardToken).safeTransfer(_receiver, reward)` revert due to insufficient contract balance (line 234), permanently freezing part of the unclaimed yield for those users — matching the "High - Permanent freezing of unclaimed yield" impact class.

### Likelihood Explanation
The only precondition is that a reward token registered in the pool (`isRewardToken[_rewardToken] == true`) charges a transfer fee — this is plausible for any deflationary/fee-on-transfer ERC20 that a reward manager might add. Once such a token is registered, any unprivileged address can call `donateRewards` repeatedly, at low cost (down to token amounts where the fee still rounds to a non-zero loss), to compound the shortfall. This is fully repeatable and requires no special capital beyond the fee-bearing token amount donated.

### Recommendation
In `_provisionReward`, measure the actual amount received by comparing the reward token balance before and after the `safeTransferFrom` call, and use that delta (not the requested `_amountReward`) for all `historicalRewards`, `queuedRewards`, and `rewardPerTokenStored` updates. Alternatively, explicitly disallow fee-on-transfer/rebasing tokens from being registered as reward tokens.

### Proof of Concept
Foundry/Hardhat test plan:
1. Deploy `BaseRewardPool` with a mock fee-on-transfer ERC20 (e.g., 10% fee) as `_rewardToken`, registered at construction.
2. Ensure `totalStaked() == 0` (no stakers yet) or set up one staker with a known stake via `MasterMagpie` mock.
3. As an unprivileged attacker EOA, call `donateRewards(1000e18, feeToken)`; confirm the pool's actual `feeToken.balanceOf(pool)` only increased by 900e18 (10% fee taken), but `rewards[feeToken].queuedRewards` (or `rewardPerTokenStored` if `totalStaked() > 0`) reflects the full 1000e18.
4. Have the legitimate reward manager call `queueNewRewards(realAmount, feeToken)` after a staker deposits, folding in the inflated `queuedRewards`.
5. Have the staker call `getReward`; assert that `earned()` computes a reward value exceeding `feeToken.balanceOf(pool)`, and that `safeTransfer` reverts (or a subsequent claimant's transfer reverts) due to insufficient balance — demonstrating permanently frozen yield for at least one claimant.

### Citations

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

**File:** rewards/BaseRewardPool.sol (L279-284)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPool.sol (L297-320)
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
        if (this.totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingDecimals()) /
                this.totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }
```
