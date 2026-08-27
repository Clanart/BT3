### Title
Permissionless `harvest()` allows front-running of discrete, non-time-weighted WOM reward distribution to steal yield from long-term stakers - (File: wombat/WombatStaking.sol, rewards/BaseRewardPool.sol)

### Summary
`WombatStaking.harvest()` is callable by anyone with no time delay, and it eventually funnels harvested WOM rewards into `BaseRewardPool.queueNewRewards()` / `_provisionReward()`, which distributes the entire harvested amount instantly and proportionally to the *current* `totalStaked()` snapshot, not a time-weighted stake. Because `MasterMagpie.deposit`/`withdraw` (and the `WombatPoolHelper`/`WombatPoolHelperV2` wrappers that call `depositFor`/`withdrawFor`) have no cooldown, lock, or minimum staking period, an attacker can front-run a pending `harvest()` call, deposit a large stake immediately before it lands, capture a proportional share of rewards accrued over the entire period since the last harvest, and withdraw right after — diluting the rewards rightfully owed to genuine long-term stakers. This is the same bug class as the wstTAO report: a discretely-updated, non-continuously-accruing value (there `exchangeRate`, here `rewardPerTokenStored`) is credited based on a stale/instant state snapshot that a flash depositor can exploit by sandwiching the update transaction.

### Finding Description
`harvest(address _lpToken)` has no caller restriction beyond the pool being active: [1](#0-0) 

It invokes `_toMasterWomAndSendReward`, which ultimately routes the harvested WOM reward to `IBaseRewardPool.queueNewRewards()`, seen used the same way from the `vote()` flow: [2](#0-1) 

`BaseRewardPool._provisionReward` (invoked by `queueNewRewards`/`donateRewards`) distributes the entire incoming reward amount in one shot, dividing by the instantaneous `totalStaked()`: [3](#0-2) 

`totalStaked()` and `balanceOf()` read the live, current stake from `MasterMagpie`, not a time-weighted average: [4](#0-3) 

Deposits and withdrawals through `MasterMagpie` (and the `WombatPoolHelper`/`WombatPoolHelperV2`/`AnkrBNBPoolHelper` wrappers) are immediate, with no cooldown, vesting, or minimum holding period: [5](#0-4) [6](#0-5) 

This is structurally identical to the wstTAO bug class: a value representing accrued rewards over a past interval (`exchangeRate` there, `rewardPerTokenStored` here) is updated in a single discrete transaction and credited against a state snapshot (`plxTAO` supply there, `totalStaked()` here) that an attacker can manipulate/exploit by inserting a deposit immediately before the update and a withdrawal immediately after, capturing rewards proportional to their share of the *post-front-run* stake even though they held no economic exposure during the period the rewards actually accrued.

### Impact Explanation
An attacker who observes a pending, permissionless `harvest()` (or `vote()`, which also queues rewards) transaction in the mempool can:
1. Deposit a large stake into the relevant pool via `WombatPoolHelper.deposit()`/`depositLP()`, instantly increasing `totalStaked()`.
2. Let the `harvest()` transaction land — `rewardPerTokenStored` increases based on the *diluted* denominator that now includes the attacker's flash stake.
3. Immediately call `withdraw()` to unstake and claim their earned share of the rewards.

Because reward accrual is not time-weighted, the attacker earns rewards that accumulated over the entire period since the previous harvest despite having zero economic exposure during that period. This directly steals unclaimed yield from genuine long-term stakers, who now receive a diluted `rewardPerTokenStored` value permanently (their pending, unclaimed rewards for that harvest cycle are perpetually reduced) — this is a direct theft of unclaimed yield from other users, satisfying the impact bar in the rules.

### Likelihood Explanation
`harvest()` is unauthenticated/permissionless and can be triggered by anyone, including the attacker themselves or any bot, making the timing of a reward-crediting event fully attacker-controllable or at minimum publicly observable in the mempool. Deposit/withdraw functions used by ordinary wallets carry no fee or lockup that would deter a same-block or few-block sandwich. This requires only ordinary wallet interactions (`deposit`, `harvest`, `withdraw`) — no privileged role, oracle, or governance action — making it a highly likely occurrence, particularly around large WOM harvests or bribe distributions.

### Recommendation
- Make reward accrual time-weighted (e.g., checkpoint per-second emission like `MasterMagpie.updatePool` does for MGP, rather than crediting a discrete lump sum against an instantaneous stake snapshot).
- Alternatively, introduce a minimum staking duration or a deposit/withdraw cooldown before a user's stake is eligible to receive rewards from `queueNewRewards`/`_provisionReward`, so freshly-deposited stake does not immediately participate in a lump-sum reward distribution.
- Consider snapshotting `totalStaked()` at the start of the harvested interval rather than at the moment `_provisionReward` executes.

### Proof of Concept
1. Attacker monitors mempool for a pending `WombatStaking.harvest(lpToken)` (or `vote()`) transaction — both are callable by any address once conditions (`_onlyActivePool`) are met.
2. Attacker front-runs it: calls `WombatPoolHelper.deposit(amount, minLiquidity)` for a large `amount`, which calls `WombatStaking.deposit(...)` and then `MasterMagpie.depositFor(stakingToken, lpReceived, attacker)` — instantly increasing `totalStaked()` for that pool's `BaseRewardPool`. [7](#0-6) 
3. The pending `harvest()` transaction executes, calling `_toMasterWomAndSendReward` → `queueNewRewards` → `_provisionReward`, which computes `rewardInfo.rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked()` using the now-inflated `totalStaked()` that includes the attacker's flash deposit. [8](#0-7) 
4. Attacker immediately calls `WombatPoolHelper.withdraw(liquidity, minAmount)`, which calls `MasterMagpie.withdrawFor` and burns the receipt token — but the reward accounting already credited the attacker's share via `userRewardPerTokenPaid`/`earned()` prior to withdrawal, so the attacker can claim their proportional share of the just-harvested rewards. [9](#0-8) 
5. Genuine stakers who held their position throughout the entire harvest interval receive a smaller `rewardPerToken` increment than they would have absent the attacker's flash stake, resulting in a permanent loss of their rightfully accrued yield.

### Citations

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

**File:** wombat/WombatStaking.sol (L403-412)
```text
                        if (caller != address(0) && bribeCallerFee != 0) {
                            callerFeeAmount = (rewardAmount * bribeCallerFee) / DENOMINATOR;
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeManager, callerFeeAmount);
                        }

                        rewardAmount -= protocolFee;
                        rewardAmount -= callerFeeAmount;
                        IERC20(rewardTokens[i][j]).safeApprove(_rewarders[i], rewardAmount);
                        IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, address(rewardTokens[i][j]));
                    }
```

**File:** rewards/BaseRewardPool.sol (L124-136)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }

    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
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

**File:** rewards/MasterMagpie.sol (L337-346)
```text
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }

    /// @notice Withdraw staking tokens from Master Mgapie.
    /// @param _stakingToken Staking token of the pool
    /// @param _amount amount to withdraw
    function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _withdraw(_stakingToken, msg.sender, _amount, false);
    }
```

**File:** wombat/WombatPoolHelper.sol (L98-140)
```text
    function deposit(uint256 _amount, uint256 _minimumLiquidity) external override {
        _deposit(_amount, _minimumLiquidity, msg.sender);
    }

    function depositLP(uint256 _lpAmount) external {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).depositLP(lpToken, _lpAmount, msg.sender);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewLpDeposit(msg.sender, _lpAmount);
    }

    function depositNative(uint256 _minimumLiquidity) external payable {
        if(!isNative) revert NotNativeToken();
        // Dose need to limit the amount must > 0?

        // Swap the BNB to wBNB
        _wrapNative();
        // depsoit wBNB to the pool
        IWNative(depositToken).approve(wombatStaking, msg.value);
        _deposit(msg.value, _minimumLiquidity, address(this));
        IWNative(depositToken).approve(wombatStaking, 0);
    }

    /// @notice withdraw stables from wombat pool, auto unstake from master Magpie
    /// @param _liquidity the amount of liquidity to withdraw
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

**File:** wombat/WombatPoolHelper.sol (L148-155)
```text
    function _deposit(uint256 _amount, uint256 _minimumLiquidity, address _from) internal {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, msg.sender, _from);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewDeposit(msg.sender, _amount);
    }
```
