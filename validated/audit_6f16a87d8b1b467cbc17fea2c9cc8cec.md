### Title
Permissionless `WombatStaking.harvest()` enables reward-sandwiching attack that steals unclaimed yield from long-term stakers - (File: wombat/WombatStaking.sol, wombat/WombatPoolHelper.sol, rewards/BaseRewardPool.sol)

### Summary
`WombatPoolHelper.harvest()` and the underlying `WombatStaking.harvest(lpToken)` are callable by any address (`_onlyActivePool` only checks the pool is active, not who is calling), and each harvest queues the entire batch of WOM/bonus rewards accumulated since the last harvest into `BaseRewardPool` via `queueNewRewards` → `_provisionReward`. Because `_provisionReward` distributes the whole queued lump sum pro-rata to `totalStaked()` measured at the instant of the call (not time-weighted since the last harvest), an attacker can `depositLP` immediately before calling `harvest()` and `withdraw` immediately after, capturing a share of rewards that accrued over the entire pre-harvest interval despite having staked only a few blocks.

### Finding Description
`WombatPoolHelper.harvest()` simply forwards to `IWombatStaking(wombatStaking).harvest(lpToken)` with no access control of its own [1](#0-0) . `WombatStaking.harvest` is gated only by `whenNotPaused` and `_onlyActivePool(_lpToken)`, which checks that the pool exists/is active, not the caller's identity [2](#0-1) .

`harvest()` calls `_toMasterWomAndSendReward(_lpToken, 0, true)`, which withdraws/deposits into `MasterWombat` to trigger WOM emission harvest, computes the WOM delta received since the last harvest, and forwards it to the pool's `BaseRewardPool` via `_sendRewards` → `queueNewRewards` [3](#0-2) [4](#0-3) .

In `BaseRewardPool._provisionReward`, the newly queued reward amount is converted into a single `rewardPerTokenStored` increment based on `totalStaked()` measured at call time:
```
rewardInfo.rewardPerTokenStored += (_amountReward * 10**stakingDecimals()) / this.totalStaked();
``` [5](#0-4) 

This is not time-weighted — the entire batch of WOM emitted over the whole interval since the last harvest is split among whoever holds a stake balance at the moment `queueNewRewards` executes, in proportion to their balance, regardless of how long they had actually contributed to earning that batch. `earned()`/`_updateFor` snapshot each user's `userRewardPerTokenPaid` only at the point of their own deposit/withdraw/getReward interactions [6](#0-5) [7](#0-6) , so an attacker who deposits right before the reward-per-token bump has `userRewardPerTokenPaid` set to the pre-bump value, then is fully entitled to `balance * increment` once the bump lands.

Attack flow:
1. Attacker calls `depositLP(smallAmount)` on `WombatPoolHelper`, which stakes receipt tokens into `MasterMagpie` on the attacker's behalf via `_stake`/`depositFor` [8](#0-7) .
2. Attacker (or anyone) calls `harvest()`, which harvests the full WOM emission accrued since the last harvest and queues it into `BaseRewardPool`, bumping `rewardPerTokenStored` for the whole pool [1](#0-0) .
3. Attacker calls `withdraw(smallAmount, 0)`, triggering `_unstake` → `MasterMagpie.withdrawFor`, which typically also calls `getReward`, transferring the attacker's now-accrued share of the harvested WOM/bonus tokens [9](#0-8) .

Because the attacker's `balanceOf` at harvest time is counted identically to a staker who has been in the pool for the entire un-harvested interval, the attacker receives `balance_attacker / totalStaked * batchReward`, which is disproportionate to the 1-2 blocks they were actually staked, at the expense of the long-term stakers who funded that emission through their locked liquidity over the whole interval. No modifier, cooldown, or vesting mechanism in `WombatStaking`, `WombatPoolHelper`, or `BaseRewardPool` prevents this deposit-harvest-withdraw sandwich; `nonReentrant` on `depositLP`/`withdraw` only prevents reentrancy, not this multi-transaction sandwich pattern.

### Impact Explanation
This is a theft of unclaimed yield: rewards that should accrue only to stakers who bore the staking duration are diverted to a same-block/near-block depositor who front-runs the (permissionless) harvest call. The magnitude scales with the size of the queued reward batch (proportional to time since last harvest and pool emission rate) and the attacker's fraction of `totalStaked` at the moment of the bump — an attacker can amplify this by depositing a large capital amount right before harvest (flash-loan or large balance), then withdrawing immediately after, since there's no minimum staking duration or linear vesting for rewards.

### Likelihood Explanation
- No privileged role is required: `harvest()` is unauthenticated, and `depositLP`/`withdraw` are open to any lpToken holder.
- Capital requirement is only the LP token amount to deposit, which can be borrowed/flash-loaned if a flash-loan source for the LP token exists, or simply funded by the attacker's own capital for a few blocks.
- The attack is trivially repeatable every time a harvest is due, and the profitability increases the longer the interval since the last harvest (larger reward batch to skim).
- No cooldown, vesting, or time-weighting mechanism exists in `BaseRewardPool` or `WombatStaking` to mitigate this.

### Recommendation
- Time-weight reward distribution instead of using a single lump-sum `rewardPerToken` bump based on instantaneous `totalStaked()` — e.g., adopt a continuous reward-rate/duration model (`rewardRate`, `periodFinish`, `lastUpdateTime` like Synthetix `StakingRewards`) so a newly added balance can only earn rewards accruing after the deposit.
- Alternatively, add a minimum staking duration / withdrawal cooldown before a depositor becomes eligible for pending rewards, or snapshot eligibility based on balance held continuously since the last harvest.
- Consider restricting `harvest()` to be triggered atomically with deposit/withdraw pathways only, or require a time lag between deposit and reward eligibility.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatStaking`, `MasterMagpie`, `BaseRewardPool`, `WombatPoolHelper`, mock `MasterWombat`/WOM token, and register the pool.
2. Long-term staker `Alice` deposits `1000 LP` via `depositLP`.
3. Advance `N` blocks (simulate WOM emission accruing in mock `MasterWombat` proportional to elapsed time/blocks).
4. Attacker `Bob` deposits `1000 LP` (equal size for clarity) via `depositLP` in block `N+1`.
5. Anyone calls `WombatPoolHelper.harvest()` in the same block `N+1`, which harvests all WOM emitted over the `N` blocks and queues it to `BaseRewardPool`, bumping `rewardPerTokenStored` while `totalStaked = 2000`.
6. Bob immediately calls `withdraw(1000, 0)` in block `N+2`, which internally calls `getReward`, transferring Bob's WOM share.
7. Assert: `Bob.womReceived == totalHarvestedWom / 2` (per current mechanism) despite having staked only 1 block, while `Alice`, who staked for `N` blocks, is entitled to the other half only when she later calls `getReward`/`withdraw` — demonstrating `Bob`'s reward-per-staked-block rate is `N`x higher than Alice's, confirming disproportionate yield capture.

### Citations

**File:** wombat/WombatPoolHelper.sol (L102-109)
```text
    function depositLP(uint256 _lpAmount) external {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).depositLP(lpToken, _lpAmount, msg.sender);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewLpDeposit(msg.sender, _lpAmount);
    }
```

**File:** wombat/WombatPoolHelper.sol (L125-140)
```text
    function withdraw(uint256 _liquidity, uint256 _minAmount) external override {
        // we have to withdraw from wombat exchange to harvest reward to base rewarder
        IWombatStaking(wombatStaking).withdraw(
            lpToken,
            _liquidity,
            _minAmount,
            msg.sender
        );
        // then we unstake from master wombat to trigger reward distribution from basereward
        _unstake(_liquidity, msg.sender);
        //  last burn the staking token withdrawn from Master Magpie
        IWombatStaking(wombatStaking).burnReceiptToken(lpToken, _liquidity);


        emit NewWithdraw(msg.sender, _liquidity);
    }
```

**File:** wombat/WombatPoolHelper.sol (L142-144)
```text
    function harvest() external override {
        IWombatStaking(wombatStaking).harvest(lpToken);
    }
```

**File:** wombat/WombatStaking.sol (L329-335)
```text
    /// @notice harvest a Pool from Wombat
    /// @param _lpToken wombat pool lp as helper identifier
    function harvest(
        address _lpToken
    ) whenNotPaused _onlyActivePool(_lpToken) external {
        _toMasterWomAndSendReward(_lpToken, 0, true); // triggers harvest from wombat exchange
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

**File:** wombat/WombatStaking.sol (L720-770)
```text
    function _sendRewards(
        address _lpToken,
        address _rewardToken,
        address _rewarder,
        uint256 _amount
    ) internal {
        if (_amount == 0) return;
        uint256 originalRewardAmount = _amount;

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
                }
            }
        }

        IERC20(_rewardToken).safeApprove(_rewarder, 0);
        IERC20(_rewardToken).safeApprove(_rewarder, _amount);
        IBaseRewardPool(_rewarder).queueNewRewards(_amount, _rewardToken);
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
