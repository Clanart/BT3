### Title
Permissionless `WombatStaking#harvest` crystallizes pending WOM yield into `rewardPerTokenStored` without time-weighting, letting a flash-depositor hijack matured rewards - (File: `wombat/WombatStaking.sol`, `rewards/BaseRewardPoolV2.sol`)

### Summary
`WombatStaking.harvest()` is an external, unrestricted (`whenNotPaused _onlyActivePool`) function that anyone can call to pull pending WOM (and bonus token) rewards accrued in Wombat's `MasterWombat` since the last harvest, and forward them into the pool's `BaseRewardPool`/`BaseRewardPoolV2` via `queueNewRewards` → `_provisionReward`. [1](#0-0) 
`_provisionReward` immediately folds the entire harvested amount into `rewardPerTokenStored` in proportion to `totalStaked()` **at the instant harvest is called**, with no time-weighting of how long each staker's balance was actually staked while that yield accrued. [2](#0-1) 

### Finding Description
This is the same root-cause pattern as the Sherlock H-4 report on `CuratedVault#totalAssets`: a value that represents "matured but not yet realized yield" is distributed to whoever holds a claim at the moment the yield is crystallized, rather than to those who held the claim while the yield accrued.

Here, WOM (and bonus) rewards accrue continuously inside Wombat's `MasterWombat` contract between harvests, but `BaseRewardPoolV2`'s `rewardPerTokenStored` is only updated in discrete jumps whenever `queueNewRewards`/`_provisionReward` is called — and that update divides the whole batch of harvested rewards by the *current* `totalStaked()`, not by a time-integral of stake: [3](#0-2) 

`balanceOf` for reward accounting purposes is read live from `MasterMagpie.stakingInfo`, so it reflects whatever the staked amount is right when `_updateFor`/`updateReward` runs — there is no mechanism that locks in a staker's *historical* share of the un-harvested reward before an attacker's deposit can inflate `totalStaked()`. [4](#0-3) 

Attack path (unprivileged wallet, no admin/governance role needed):
1. Wait until a pool has a large amount of unharvested WOM pending in `MasterWombat` (common for low-interaction pools, since `harvest()` is only called occasionally by keepers or via user deposits/withdrawals).
2. Flash-loan a large amount of the pool's LP/deposit token and deposit it via the pool's `WombatPoolHelper`/`WombatStaking.deposit` or `depositLP`, which stakes into `MasterMagpie` and inflates `totalStaked()` in the corresponding `BaseRewardPoolV2` instantly. [5](#0-4) 
3. Call (or simply wait a block for someone/anything to call) the permissionless `harvest()` function, which pulls all pending WOM from `MasterWombat` and immediately folds it into `rewardPerTokenStored` based on the now-inflated `totalStaked()`. [6](#0-5) 
4. Immediately withdraw/claim rewards and unstake, capturing `attackerStake / (attackerStake + existingStake)` of the entire batch of harvested WOM that had actually accrued to existing long-term stakers, then repay the flash loan.

### Impact Explanation
This directly steals already-matured yield from existing stakers, transferring it to a transient flash depositor. Since `harvest()` is unrestricted and can be triggered by the attacker in the very same transaction sequence as their deposit/withdrawal, the attack requires no special permissions or external conditions beyond gas/flash-loan cost and existence of a nontrivial pending-reward backlog (worse for low-interaction / long-un-harvested pools), matching the same "theft of unclaimed yield" impact class as the referenced H-4 finding.

### Likelihood Explanation
Likelihood is high: `harvest()` has no access control beyond `whenNotPaused`/`_onlyActivePool`, deposits/withdrawals from `MasterMagpie` staking pools have no cooldown preventing same-block deposit+harvest+withdraw, and flash loans for most Wombat-supported stablecoins/LP-underlying assets are readily available. Any pool that accumulates a backlog of unharvested WOM (e.g., due to infrequent voting/harvest cadence) is exploitable by an ordinary wallet.

### Recommendation
Update the reward accounting to be time-weighted rather than snapshot-based, e.g.:
- Convert `BaseRewardPool`/`BaseRewardPoolV2` to a per-second reward-rate/duration model (similar to Synthetix `StakingRewards`) where newly queued rewards are vested linearly over a period rather than instantly divided by current `totalStaked()`, or
- Force a harvest/reward update (`updateReward`) prior to any deposit or withdrawal changing `totalStaked()`, and additionally require harvesting pending upstream (`MasterWombat`) rewards before allowing large stake changes to affect `rewardPerTokenStored` distribution, or
- Introduce a minimum staking duration / cooldown before a depositor's stake counts toward reward distribution for already-accrued-but-unharvested rewards.

### Proof of Concept
Conceptual PoC (mirrors the report's flash-loan pattern, adapted to this codebase):
1. Let a pool `P` accumulate `X` WOM of pending, unharvested rewards in `MasterWombat` while `totalStaked` in `BaseRewardPoolV2` for `P` is `S`.
2. Attacker flash-loans `F` of `P`'s deposit token, calls `WombatStaking.deposit(...)` (or `depositLP`) to mint receipt tokens and stake `F` into `MasterMagpie`, raising `totalStaked` to `S + F`. [5](#0-4) 
3. Attacker (or anyone) calls `WombatStaking.harvest(P)`, which pulls `X` WOM from `MasterWombat` and calls `_sendRewards` → `queueNewRewards` → `_provisionReward`, setting `rewardPerTokenStored += X * 1e_dec / (S + F)`. [1](#0-0) [3](#0-2) 
4. Attacker withdraws/claims via `MasterMagpie.withdraw`/`getReward`, receiving `F/(S+F) * X` WOM despite having staked for zero accrual time, then repays the flash loan, netting `F/(S+F) * X` minus flash-loan/gas fees.

I was not able to fully verify whether any of the `WombatPoolHelper` variants impose a minimum hold time or fee on immediate withdraw-after-deposit that could reduce/block this specific sequence; confirming that (and exact `MasterMagpie` withdraw path timing) would need deeper reading of `wombat/WombatPoolHelper.sol` / `wombat/WombatPoolHelperV2.sol`, which the available index snippets did not fully cover.

### Citations

**File:** wombat/WombatStaking.sol (L242-270)
```text
    function deposit(
        address _lpAddress,
        uint256 _amount,
        uint256 _minimumLiquidity,
        address _for,
        address _from
    ) nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external {
        // Get information of the Pool of the token
        Pool storage poolInfo = pools[_lpAddress];
        address depositToken = poolInfo.depositToken;
        IERC20(depositToken).safeTransferFrom(_from, address(this), _amount);

        IERC20(depositToken).safeApprove(poolInfo.depositTarget, _amount);
        uint256 beforeBalance = IERC20(poolInfo.lpAddress).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).deposit(
            depositToken,
            _amount,
            _minimumLiquidity,
            address(this),
            block.timestamp,
            false
        );

        uint256 lpReceived = IERC20(poolInfo.lpAddress).balanceOf(address(this)) - beforeBalance;
        _toMasterWomAndSendReward(_lpAddress, lpReceived, true); // triggers harvest from wombat exchange
        // update variables
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, lpReceived);
        emit NewDeposit(_for, depositToken, _amount, poolInfo.receiptToken, lpReceived);
    }
```

**File:** wombat/WombatStaking.sol (L330-335)
```text
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

**File:** rewards/BaseRewardPoolV2.sol (L290-314)
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
    }
```

**File:** rewards/BaseRewardPool.sol (L130-136)
```text
    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```
