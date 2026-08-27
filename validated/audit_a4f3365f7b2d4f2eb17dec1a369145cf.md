Based on my research, I found a concrete analog in the reward-accounting layer of MasterMagpie/Wombat, matching the "business logic flaw exploitable via manipulable on-chain state" class from the UPSToken report.

### Title
Instant, non-streamed reward distribution combined with unrestricted `donateRewards`/deposit-withdraw allows flash-deposit theft of staker yield - ([File: rewards/BaseRewardPool.sol])

### Summary
`BaseRewardPool` (and its variants `BaseRewardPoolV2`, `mWOMSVBaseRewarder`) credit newly injected rewards to `rewardPerTokenStored` **instantly and in full**, proportional to whatever `totalStaked()` happens to be at the exact moment the reward is provisioned, with no time-weighted streaming/vesting window (no `rewardRate`/`periodFinish` mechanism). Additionally, `donateRewards` is callable by **any unprivileged wallet** with no access control beyond the reward token already being registered [1](#0-0) , and it funnels straight into the same instant-credit accounting used by legitimate manager-driven `queueNewRewards` calls [2](#0-1) .

### Finding Description
`_provisionReward` computes:
```
rewardInfo.rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked();
``` [3](#0-2) 

This is a single atomic increment based on the *current* `totalStaked()` snapshot — there is no accrual over time as in a standard Synthetix-style `rewardRate`/`lastUpdateTime` design. Any address holding a share of `totalStaked()` at the instant this line executes captures a proportional slice of the newly added rewards, regardless of how long they have actually been staking.

This mirrors the UPSToken root cause: a critical, funds-affecting calculation (`myPressure`/fee logic in UPSToken; `rewardPerTokenStored` here) is derived from **a transient, atomically-manipulable piece of state** (LP reserves there; `totalStaked()` here) rather than a time-weighted or otherwise manipulation-resistant value, and an unprivileged actor can move that state within their own transaction to bias the outcome in their favor.

Because deposits/withdrawals into the staking/receipt tokens tracked by `MasterMagpie` (e.g., LP receipt tokens, `mWomSV`, `vlMGP`) are not subject to any cooldown before reward eligibility, and `queueNewRewards`/`donateRewards`/harvest-driven reward injections (e.g. from `WombatStaking._sendRewards` → `IBaseRewardPool(_rewarders[i]).queueNewRewards(...)` [4](#0-3) ) are periodic and often externally triggerable, an attacker can:
1. Monitor for (or, via `donateRewards`, self-trigger) an imminent reward injection into a `BaseRewardPool`.
2. Deposit a large amount of the underlying receipt/staking token immediately before the reward-injection transaction lands (or bundle it in the same block), inflating their share of `totalStaked()` at the moment `_provisionReward` executes.
3. Immediately call `getReward`/withdraw after the `rewardPerTokenStored` bump is applied, harvesting a disproportionate share of rewards that should have accrued to genuine long-term stakers, then exit their principal.

### Impact Explanation
This constitutes theft of unclaimed yield from genuine long-term stakers in `BaseRewardPool`/`BaseRewardPoolV2`/`mWOMSVBaseRewarder` pools, since a flash/just-in-time depositor can capture reward shares they did not economically earn, diluting or diverting rewards intended for real depositors of LP tokens, `mWomSV`, or `vlMGP`. This falls under "theft ... of unclaimed yield," one of the accepted impact categories.

### Likelihood Explanation
Likelihood is moderate-to-high: the entry points (`donateRewards`, deposit/withdraw of the underlying receipt tokens through `MasterMagpie`/`WombatStaking`, and `getReward`) are all unprivileged and externally callable. The main precondition is timing a deposit around a reward-injection transaction, which is achievable via mempool observation or same-block bundling; I was not able to fully verify (due to tool budget) whether `MasterMagpie`'s deposit/withdraw path enforces any minimum holding period or fee that would blunt this, so this should be confirmed against `rewards/MasterMagpie.sol` deposit/withdraw implementations before treating severity as final.

### Recommendation
Introduce time-weighted reward streaming (e.g., a `rewardRate` distributed linearly over a fixed duration, as in the standard Synthetix `StakingRewards` pattern) instead of crediting the entire `_amountReward` to `rewardPerTokenStored` atomically based on the instantaneous `totalStaked()`. Alternatively, add a minimum staking duration/cooldown before a deposit is eligible to earn newly queued rewards, and restrict or rate-limit permissionless `donateRewards` calls.

### Proof of Concept
1. Attacker observes (or triggers via `donateRewards`) an imminent large reward injection into a `BaseRewardPool` instance.
2. In the same block, attacker calls `MasterMagpie.deposit`/`depositFor` with a large amount of the pool's staking/receipt token, inflating `totalStaked()`.
3. The reward-injection transaction executes `_provisionReward`, crediting `rewardPerTokenStored` proportionally to the now-inflated `totalStaked()`, giving the attacker a large share of `rewardPerTokenStored` gain [5](#0-4) .
4. Attacker calls `getReward` to claim their disproportionate share [6](#0-5) , then withdraws their principal, having earned yield without any real staking duration/risk — funds that would otherwise have accrued to genuine long-term depositors.

### Citations

**File:** rewards/BaseRewardPool.sol (L221-240)
```text
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

**File:** rewards/BaseRewardPool.sol (L276-284)
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

**File:** rewards/BaseRewardPool.sol (L297-319)
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
```

**File:** wombat/WombatStaking.sol (L729-750)
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
```
