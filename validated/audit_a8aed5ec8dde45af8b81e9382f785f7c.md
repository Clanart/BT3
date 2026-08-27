### Title
Reward distribution splits harvested rewards by instantaneous `totalStaked()`, allowing sandwiching of `WombatStaking.harvest()` to steal yield from long-term stakers - (File: rewards/BaseRewardPool.sol, wombat/WombatStaking.sol)

### Summary
`BaseRewardPool` (and `BaseRewardPoolV2`/`mWOMSVBaseRewarder`) distribute newly harvested reward tokens by dividing the entire batch by whatever `totalStaked()` happens to be at the exact moment the reward is provisioned, exactly mirroring the Acala report's flaw of splitting a fixed periodic reward among whoever holds shares at accumulation time. Since deposit/withdraw into `MasterMagpie` via the pool helpers is unrestricted (no cooldown/unbonding) and `WombatStaking.harvest()` is a permissionless, externally callable function, an attacker can flash-deposit LP receipt tokens immediately before triggering `harvest()`, capture a share of the newly harvested rewards proportional to their inflated stake, and withdraw immediately after — diluting the `rewardPerToken` delta earned by genuine long-term LPs.

### Finding Description
`BaseRewardPool._provisionReward` computes the reward-per-share increment instantaneously against the current staked balance: [1](#0-0) 

`totalStaked()` reads the live balance of the staking (receipt) token held by `MasterMagpie`: [2](#0-1) 

and `earned()` uses the user's live, real-time `balanceOf` against the stored `rewardPerToken`, with no snapshot at reward-provisioning time: [3](#0-2) 

Rewards get provisioned into the rewarder from `WombatStaking._toMasterWomAndSendReward`, which is invoked by the permissionless, unprivileged-callable `harvest()` function: [4](#0-3) 

The pool helper additionally exposes a public, unrestricted `harvest()` wrapper that any wallet may call: [5](#0-4) 

Deposits/withdrawals into `MasterMagpie` via the pool helper have no cooldown or unbonding period — `_deposit`/`_unstake` immediately update `userInfo[stakingToken][account].amount`: [6](#0-5) 

Because the reward increment `rewardPerTokenStored += reward * 1eDecimals / totalStaked()` is computed once, at the exact block `harvest()` is called, and because a user's `earned()` uses their *current* balance rather than a time-weighted balance, a wallet that deposits a large LP position immediately before calling (or front-running) `harvest()`, and withdraws immediately after collecting rewards, obtains a share of the harvested rewards proportional to its temporary balance-fraction of `totalStaked()` at that single block — exactly the "sandwich accumulation" pattern described in the Acala report, just applied to an event-triggered (harvest-call) accumulation instead of a fixed block-interval accumulation.

This differs from `MasterMagpie`'s own MGP emission (`accMGPPerShare`), which is a continuously-accruing per-second rate checkpointed on every state change — that mechanism is *not* vulnerable to this exact sandwich because reward accrual is tied to elapsed time, not to a lump-sum split at a single block. The vulnerability is specific to `BaseRewardPool`/`BaseRewardPoolV2`/`mWOMSVBaseRewarder`'s `_provisionReward`, which distributes discrete WOM/bonus-token harvests instantaneously.

### Impact Explanation
Genuine long-term LPs who keep shares staked between harvests suffer diluted `rewardPerToken` increments whenever an attacker temporarily inflates `totalStaked()` around a `harvest()` call, transferring yield that would otherwise accrue to them to the attacker. This is a theft/misappropriation of unclaimed yield belonging to honest depositors, satisfying the "theft or permanent freezing of unclaimed yield" impact bar. The magnitude scales with the size of the temporarily-deposited position relative to existing `totalStaked()` and with the size of the harvested reward batch.

### Likelihood Explanation
`harvest()` is callable by any address with no access control beyond `whenNotPaused`/pool-active checks, and deposit/withdraw of LP into the corresponding pool via `WombatPoolHelper` has no lock-up, so the entire attack (deposit → harvest → withdraw) can be executed atomically or across adjacent blocks by an ordinary wallet, provided the attacker can source (e.g., flash-loan or otherwise acquire) the underlying LP/deposit token. This mirrors the "economically constrained but reachable" scenario judged valid-medium in the Acala report, since acquiring temporary capital exposure to mint/redeem LP receipt tokens is a normal, permissionless DeFi operation.

### Recommendation
Do not split a lump-sum harvested reward by the instantaneous `totalStaked()`. Instead, stream newly harvested rewards over the harvest interval (similar to a Synthetix-style `rewardRate`/`periodFinish` design), or track a time-weighted average of staked shares between harvests so that reward accrual is proportional to actual duration of participation, not to balance at a single snapshot block.

### Proof of Concept
1. Existing LPs hold `S` staking-token shares in `MasterMagpie` for pool `_lpToken`, with `rewarder = BaseRewardPool`.
2. Attacker deposits a large LP amount `X >> S` into the pool via `WombatPoolHelper.depositLP` (or `deposit`), receiving stakingToken shares and immediately becoming staked in `MasterMagpie` (no cooldown).
3. Attacker (or anyone) calls `WombatPoolHelper.harvest()` → `WombatStaking.harvest(_lpToken)` → `_toMasterWomAndSendReward` → `rewarder.queueNewRewards(reward, wom)` → `_provisionReward` computes `rewardPerTokenStored += reward * 1e_dec / (S + X)`, heavily diluted versus the un-inflated `reward * 1e_dec / S`.
4. Attacker calls `getReward`/claim through `MasterMagpie` to harvest `X * delta` of the reward token, then immediately withdraws `X` via `WombatPoolHelper.withdraw`.
5. Existing long-term LPs' `earned()` is `S * delta`, which is far smaller than what they would have received had `totalStaked()` remained `S` (i.e., `S * reward/S = reward` in the limiting single-LP case) — their yield is diluted and captured by the attacker for a single momentary deposit. [1](#0-0)

### Citations

**File:** rewards/BaseRewardPool.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPool.sol (L169-185)
```text
    /// @notice Returns amount of reward token earned by a user
    /// @param _account Address account
    /// @param _rewardToken Address reward token
    /// @return Returns amount of reward token earned by a user
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

**File:** wombat/WombatPoolHelper.sol (L142-144)
```text
    function harvest() external override {
        IWombatStaking(wombatStaking).harvest(lpToken);
    }
```

**File:** wombat/WombatPoolHelper.sol (L148-170)
```text
    function _deposit(uint256 _amount, uint256 _minimumLiquidity, address _from) internal {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, msg.sender, _from);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewDeposit(msg.sender, _amount);
    }

    function _wrapNative() internal {
        IWNative(depositToken).deposit{value: msg.value}();
    }

    /// @notice stake the receipt token in the masterchief of GMP on behalf of the caller
    function _stake(uint256 _amount, address _sender) internal {
        IERC20(stakingToken).safeApprove(masterMagpie, _amount);
        IMasterMagpie(masterMagpie).depositFor(stakingToken, _amount, _sender);
    }

    /// @notice unstake from the masterchief of GMP on behalf of the caller
    function _unstake(uint256 _amount, address _sender) internal {
        IMasterMagpie(masterMagpie).withdrawFor(stakingToken, _amount, _sender);
    }
```
