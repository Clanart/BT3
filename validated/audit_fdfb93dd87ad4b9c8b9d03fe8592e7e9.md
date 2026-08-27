### Title
Denial-of-service on WombatStaking LP deposit/withdraw when a fee-taking transfer to a "Revert on Zero Value" reward token address rounds to zero - (File: wombat/WombatStaking.sol)

### Summary
`WombatStaking._sendRewards` calculates a fee amount as a percentage of harvested reward tokens and, when the fee recipient is a plain address (`feeInfo.isAddress == true`), unconditionally calls `safeTransfer` with that computed amount, without checking whether the amount is zero.

### Finding Description
`_sendRewards` is invoked from `_toMasterWomAndSendReward`, which runs on every stake/unstake action that touches a pool's LP (harvesting WOM and bonus rewards as a side effect of `deposit`/`withdraw` calls into `masterWombat`) [1](#0-0) .

Inside `_sendRewards`, for each active, non-fee-free fee entry, the fee amount is computed proportionally to the harvested reward amount and, when the fee recipient is a plain address, transferred directly with no zero-amount guard: [2](#0-1) 

```solidity
uint256 feeAmount = (originalRewardAmount * feeInfo.value) / DENOMINATOR;
...
uint256 feeTosend = feeAmount;
...
if (!feeInfo.isAddress) {
    ... queueNewRewards(feeTosend, rewardToken);
} else {
    IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend); // no `feeTosend > 0` check
    emit RewardPaidTo(feeInfo.to, rewardToken, feeTosend);
}
```

If any bonus reward token registered for a pool (`assetToBonusRewards`, arbitrary tokens added by governance for alt pools, see `addBonusRewardForAsset` at line 636) is a "Revert on Zero Value Transfer" token, and the harvested amount for a given stake/unstake call is small enough that `feeAmount` rounds down to zero (integer division truncation, which is routine for low-value or dust harvests), the call to `IERC20(rewardToken).safeTransfer(feeInfo.to, 0)` reverts. This reverts the entire `_sendRewards` call, which reverts `_toMasterWomAndSendReward`, which reverts the outer `deposit`/`withdraw` call in `WombatStaking`.

This directly matches the reported bug class in the external report: a fee/reward payout of exactly zero amount interacting with a token that reverts on zero-value transfers causes a DOS of an otherwise-legitimate user operation.

### Impact Explanation
Because `_sendRewards` runs as a mandatory side effect of every `deposit`/`withdraw` for the affected pool (harvest-on-interaction pattern), once triggered, this becomes a persistent DOS: any user attempting to stake or unstake LP tokens (via `WombatPoolHelper`/`MasterMagpie` → `WombatStaking`) for the pool with the affected bonus token will have their transaction revert as long as the harvested bonus-reward amount continues to round the fee to zero. This can permanently freeze users' ability to withdraw their principal LP position and to claim/route accrued yield for that pool, matching the "permanent freezing of funds" / "freezing of unclaimed yield" impact bar.

### Likelihood Explanation
Likelihood depends on: (1) a bonus reward token for a pool being a revert-on-zero-transfer ERC20 (explicitly an in-scope "weird token" behavior per the referenced report's classification), and (2) harvested bonus-reward amounts being small enough, relative to the configured fee percentage, that `feeAmount` truncates to zero — which is common for low-volume pools, low fee percentages, or early/late-stage reward accrual. No privileged action is required; it is triggered purely by ordinary stake/unstake transaction flow and reward token/fee configuration set by governance (in-scope, non-privileged-wallet trigger).

### Recommendation
Guard the address-recipient fee transfer with a zero-amount check, mirroring the fix pattern used elsewhere in the codebase (e.g., `BaseRewardPool.getReward`, `vlMGPBaseRewarder._sendReward`, which already gate `safeTransfer` behind `> 0` checks):
```solidity
if (!feeInfo.isAddress) {
    ... queueNewRewards(feeTosend, rewardToken);
} else if (feeTosend > 0) {
    IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend);
    emit RewardPaidTo(feeInfo.to, rewardToken, feeTosend);
}
```

### Proof of Concept
1. Governance registers a bonus reward token for a pool via `addBonusRewardForAsset` where the token reverts on `transfer(to, 0)` (in-scope "Revert on Zero Value" weird ERC20 behavior) [3](#0-2) .
2. An active fee entry exists with `isAddress = true` and a nonzero `value`, targeting that same reward token when harvested (see `setFee`/fee struct) [4](#0-3) .
3. A user calls `deposit`/`withdraw` on the pool through the pool helper, triggering `_toMasterWomAndSendReward`, which harvests a small bonus-reward balance difference and calls `_sendRewards(_lpToken, bonusTokens[i], poolInfo.rewarder, bonusBalanceDiff)` [5](#0-4) .
4. Inside `_sendRewards`, `feeAmount = (bonusBalanceDiff * feeInfo.value) / DENOMINATOR` truncates to `0` due to small `bonusBalanceDiff`.
5. `IERC20(rewardToken).safeTransfer(feeInfo.to, 0)` reverts because the token disallows zero-value transfers, reverting the user's entire `deposit`/`withdraw` transaction and blocking further interaction with that pool until the token/fee configuration changes.

### Citations

**File:** wombat/WombatStaking.sol (L51-57)
```text
    struct Fees {
        uint256 value;              // allocation denominated by DENOMINATOR
        address to;
        bool isMWOM;
        bool isAddress;
        bool isActive;
    }
```

**File:** wombat/WombatStaking.sol (L636-644)
```text
    function addBonusRewardForAsset(address _lpToken, address _bonusToken) external onlyOwner {
        uint256 length = assetToBonusRewards[_lpToken].length;
        for (uint256 i = 0; i < length; i++) {
            if (assetToBonusRewards[_lpToken][i] == _bonusToken)
                revert BonusRewardExisted();
        }

        assetToBonusRewards[_lpToken].push(_bonusToken);
    }
```

**File:** wombat/WombatStaking.sol (L671-696)
```text
    function _toMasterWomAndSendReward(address _lpToken, uint256 lpAmount, bool _isStake) internal {
        Pool storage poolInfo = pools[_lpToken];

        address[] memory bonusTokens = assetToBonusRewards[_lpToken];
        uint256 bonusTokensLength = bonusTokens.length;

        uint256 womBeforeBalance = IERC20(wom).balanceOf(address(this));
        uint256[] memory beforeBalances = _rewardBeforeBalances(_lpToken);

        if(_isStake)
            _stakeToWombatMaster(_lpToken, lpAmount); // triggers harvest from wombat exchange
        else
            IMasterWombat(masterWombat).withdraw(poolInfo.pid, lpAmount); // triggers harvest from wombat exchange
        uint256 womRewards = IERC20(wom).balanceOf(address(this)) - womBeforeBalance;
        _sendRewards(_lpToken, wom, poolInfo.rewarder, womRewards);

        for (uint256 i; i < bonusTokensLength; i++) {
            uint256 bonusBalanceDiff = IERC20(bonusTokens[i]).balanceOf(address(this)) - beforeBalances[i];
            if (bonusBalanceDiff > 0) {
                _sendRewards(_lpToken, bonusTokens[i], poolInfo.rewarder, bonusBalanceDiff);
            }
        }

        emit WomHarvested(womRewards);

    }
```

**File:** wombat/WombatStaking.sol (L729-762)
```text
        if (!isPoolFeeFree[_lpToken]) {
            for (uint256 i = 0; i < feeInfos.length; i++) {
                Fees storage feeInfo = feeInfos[i];

                if (feeInfo.isActive) {
                    address rewardToken = _rewardToken;
                    uint256 feeAmount = (originalRewardAmount * feeInfo.value) / DENOMINATOR;
                    _amount -= feeAmount;
                    uint256 feeTosend = feeAmount;

                    if (feeInfo.isMWOM && rewardToken == wom) {
                        if (smartWomConverter != address(0)) {
                            IERC20(wom).safeApprove(smartWomConverter, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IConverter(smartWomConverter).smartConvert(feeAmount, 0);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
                        } else {
                            IERC20(wom).safeApprove(mWom, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IMWom(mWom).deposit(feeAmount);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
                        }
                    }

                    if (!feeInfo.isAddress) {
                        IERC20(rewardToken).safeApprove(feeInfo.to, 0);
                        IERC20(rewardToken).safeApprove(feeInfo.to, feeTosend);
                        IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken);
                    } else {
                        IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend);
                        emit RewardPaidTo(feeInfo.to, rewardToken, feeTosend);
                    }
```
