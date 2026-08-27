### Title
Instant flash-stake before permissionless `harvest()` steals proportional share of newly distributed WOM yield - ([File: rewards/BaseRewardPool.sol])

### Summary
`BaseRewardPool._provisionReward` distributes newly queued reward tokens as a single lump-sum increase to `rewardPerTokenStored`, computed against `totalStaked()` at the exact moment the reward is provisioned, rather than streaming/vesting the reward linearly over time. Combined with `WombatStaking.harvest()` being callable by any unprivileged wallet with no cooldown, and `MasterMagpie` deposits/withdrawals having no minimum staking duration, an attacker can deposit a large stake immediately before triggering (or waiting for) a harvest, capture a disproportionate share of the freshly harvested WOM yield relative to their staking duration, and withdraw immediately after — diluting the yield that would otherwise accrue to genuine long-term stakers.

### Finding Description
`_provisionReward` updates the global reward index in one shot: [1](#0-0) 

This means whoever is staked (i.e., counted in `totalStaked()`) at the instant `queueNewRewards`/harvest is executed receives a pro-rata share of the entire newly queued reward amount — regardless of how long they have actually been staked. There is no time-weighted accrual (e.g., `rewardRate` streamed per second as in Synthetix-style pools) protecting against this.

The trigger for pulling and queuing WOM rewards, `WombatStaking.harvest`, is unprivileged and callable by any wallet at any time: [2](#0-1) 

Deposits into the pool via `MasterMagpie.depositFor`/`WombatPoolHelper.deposit` have no minimum lock or cooldown before a user's stake counts toward `totalStaked()` for reward purposes: [3](#0-2) [4](#0-3) 

Because the WOM emissions accrued on the underlying Wombat `MasterWombat` contract build up continuously between harvests (irrespective of who is staked in Magpie's wrapper), a wallet can:
1. Deposit a large stake into the Magpie pool (checkpointing their `userRewardPerTokenPaid` at the pre-harvest index, earning nothing yet).
2. Wait for (or itself call) `harvest()`, which pulls the accumulated WOM and calls `queueNewRewards` → `_provisionReward`, crediting the entire batch of WOM pro-rata to `totalStaked()` at that instant — including the attacker's newly added, non-time-weighted stake.
3. Immediately claim and withdraw, exiting with a share of yield that should have accrued to stakers who held their position throughout the accrual period.

This directly matches the "Yield Protocol Flaw" bug class in the Thena report, where reward-pool accounting failed to protect against transient stake manipulation around a reward-crediting event, resulting in yield theft from honest stakers.

### Impact Explanation
This allows theft of unclaimed yield belonging to genuine long-term LPs/stakers, since each successful flash-stake cycle permanently redirects a slice of the harvested WOM rewards to the attacker instead of to stakers who earned it over time. Repeated execution scales the loss with the size of capital an attacker can transiently commit, and the diluted yield is not recoverable by the affected stakers once queued and reflected in `rewardPerTokenStored`.

### Likelihood Explanation
`harvest()` requires no privileged role and can be called by any address; deposits/withdrawals through `WombatPoolHelper`/`MasterMagpie` are also fully permissionless with no lockup. An attacker only needs enough capital (potentially via flash loan of the deposit asset, since the deposit path only requires the underlying `depositToken`/LP, not a privileged asset) to temporarily dominate `totalStaked()` at the harvest moment, making this practically executable by any unprivileged wallet.

### Recommendation
Introduce time-weighted/streamed reward distribution (e.g., a `rewardRate` streamed linearly per second, or a minimum staking-duration requirement before new deposits are eligible for pending/queued rewards) in `BaseRewardPool._provisionReward`, and/or add a deposit cooldown or harvest-lock window during which newly deposited stake does not count toward the reward snapshot taken at `queueNewRewards` time.

### Proof of Concept
Conceptual sequence (no working exploit code available from the index; based on function flow):
1. Attacker calls `WombatPoolHelper.deposit()` with a large amount, minted receipt tokens are staked via `MasterMagpie.depositFor`, checkpointing attacker's reward index at the pre-harvest `rewardPerTokenStored`.
2. Attacker (or anyone) calls `WombatStaking.harvest(lpToken)`, pulling accrued WOM and calling `queueNewRewards`, which updates `rewardPerTokenStored` using `totalStaked()` that now includes the attacker's large, freshly-added stake. [5](#0-4) 
3. Attacker calls `getReward`/claims their share of `userRewards`, then immediately calls `WombatPoolHelper.withdraw()` to exit, retaining a disproportionate share of yield relative to actual staking duration.

### Citations

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

**File:** wombat/WombatStaking.sol (L331-335)
```text
    function harvest(
        address _lpToken
    ) whenNotPaused _onlyActivePool(_lpToken) external {
        _toMasterWomAndSendReward(_lpToken, 0, true); // triggers harvest from wombat exchange
    }
```

**File:** rewards/MasterMagpie.sol (L348-358)
```text
    /// @notice Deposit staking tokens to Master Magpie. Can only be called by pool helper
    /// @param _stakingToken Staking token of the pool
    /// @param _amount Amount to deposit
    /// @param _for Address of the user the pool helper is depositing for, and also harvested reward will be sent to
    function depositFor(
        address _stakingToken,
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyPoolHelper(_stakingToken) nonReentrant {
        _deposit(_stakingToken, _for, _amount, false);
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
