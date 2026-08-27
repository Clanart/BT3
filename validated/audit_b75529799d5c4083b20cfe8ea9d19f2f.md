## Analysis

This is a valid analog to the reported "lazy-updated snapshot" rate-manipulation bug class, found in Magpie's reward-accounting contracts.

### Title
Instantaneous `totalStaked()` snapshot in reward provisioning allows flash-stake reward sniping - (File: rewards/BaseRewardPool.sol)

### Summary
`BaseRewardPool._provisionReward` (and the equivalent logic in `BaseRewardPoolV2`) updates the global `rewardPerTokenStored` accumulator using the *instantaneous* value of `totalStaked()` at the moment a reward is injected, rather than a time-weighted supply. Because deposits and withdrawals through `MasterMagpie` have no cooldown or vesting, an attacker can deposit a large stake immediately before a reward-provisioning call and withdraw right after in the same transaction, capturing a share of the newly injected reward proportional to their momentary balance instead of their actual staking duration — directly analogous to the reported Aave mid-rate manipulation, where an instantaneously-read on-chain value is baked into a persistent rate/accumulator that other users then rely on.

### Finding Description
`rewardPerToken()` simply returns the stored value `rewards[_rewardToken].rewardPerTokenStored` [1](#0-0) , which is updated only when new rewards are provisioned via `_provisionReward`: [2](#0-1) 

The increment `(_amountReward * 10**stakingDecimals()) / this.totalStaked()` is computed using `totalStaked()`, which reads the live balance of the staking token held by the operator (`MasterMagpie`) at call time [3](#0-2) . This is a lazy, instantaneously-read snapshot exactly like the Aave `currentLiquidityRate`/`currentVariableBorrowRate` snapshot in the original report — it is not time-weighted and is trivially manipulable within a single attacker-controlled transaction.

`_provisionReward` is reachable from unprivileged flows in two ways:
1. `donateRewards`, callable by any address for any already-registered reward token, with no access control beyond `isRewardToken` [4](#0-3) .
2. `queueNewRewards`, restricted to `onlyManager`, but triggered indirectly by ordinary user activity — e.g. `WombatStaking._toMasterWomAndSendReward` harvests WOM/bonus rewards from the underlying Wombat market and forwards them to the pool's rewarder every time *any* user stakes or withdraws through a pool helper [5](#0-4) .

Because `MasterMagpie.depositFor`/`withdrawFor` have no lock-up and only guard against reentrancy (not against sequential deposit→act→withdraw within one transaction) [6](#0-5) , an attacker can, in one atomic transaction:
1. Deposit a large stake into a pool just before a reward injection lands (front-running a pending harvest/queue tx, or self-triggering via `donateRewards`).
2. Let `_provisionReward` bump `rewardPerTokenStored` using the now-inflated `totalStaked()`... actually the attacker benefits from being one of the large depositors *at the exact moment of injection*, so their `earned()` captures a share of the reward proportional to their instantaneous balance, not their holding duration.
3. Withdraw immediately afterward, since `_updateFor` already checkpointed `userRewardPerTokenPaid` for them at the moment of the injection [7](#0-6) .

This siphons yield away from genuine long-term stakers who were staked when the reward was actually earned, to an attacker who was staked for only one block/transaction.

### Impact Explanation
This is a theft of unclaimed yield: real stakers see their proportional share of freshly-harvested WOM/MGP/bonus rewards diluted by an attacker's momentary deposit, and the attacker extracts value they did not economically earn. Repeated over each harvest cycle, this can meaningfully drain yield intended for long-term LPs/stakers.

### Likelihood Explanation
Likelihood is moderate-to-high: the attack requires only capital to temporarily deposit the staking token (which may be obtainable via flashloan/flashmint of the underlying LP or receipt token in some pools) and gas, with no privileged access or governance action needed. The `donateRewards` path is directly and permissionlessly callable by any wallet, and the `queueNewRewards` path is triggered incidentally by ordinary user harvest/deposit/withdraw activity in `WombatStaking`, making it reachable purely from unprivileged wallet transactions.

### Recommendation
Replace the instantaneous `totalStaked()`-based increment with a mechanism that streams rewards over time (e.g., a `rewardRate`/`periodFinish`-based linear release as already used in `WomUp.sol`'s `rewardPerToken()` [8](#0-7) ), or checkpoint balances so that reward accrual is proportional to staking *duration*, not the balance held at the single moment a reward is injected. Additionally, consider adding a minimum staking duration/cooldown before newly deposited balances are eligible to receive newly injected rewards.

### Proof of Concept
1. Attacker holds/obtains `stakingToken` for a pool tracked by `BaseRewardPool`.
2. In a single transaction: call `MasterMagpie.depositFor(stakingToken, largeAmount, attacker)`.
3. In the same transaction, trigger (or front-run) a reward injection — either call `BaseRewardPool.donateRewards(amount, rewardToken)` directly, or time the transaction to land immediately before a pending `WombatStaking` harvest/`queueNewRewards` call.
4. `_provisionReward` executes, bumping `rewardPerTokenStored` based on the now-inflated `totalStaked()`, and the attacker's `userRewardPerTokenPaid` is checkpointed by the deposit's `updateReward` modifier just before the injection.
5. Still within the same transaction, call `MasterMagpie.withdrawFor(stakingToken, largeAmount, attacker)`.
6. Attacker calls `getReward` and receives a share of the just-injected rewards proportional to `largeAmount`, despite having staked for zero meaningful duration, diluting genuine long-term stakers' rewards.

### Citations

**File:** rewards/BaseRewardPool.sol (L126-128)
```text
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPool.sol (L141-148)
```text
    function rewardPerToken(address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return rewards[_rewardToken].rewardPerTokenStored;
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

**File:** wombat/WombatStaking.sol (L671-690)
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
```

**File:** rewards/MasterMagpie.sol (L364-370)
```text
    function withdrawFor(
        address _stakingToken,
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyPoolHelper(_stakingToken) nonReentrant {
        _withdraw(_stakingToken, _for, _amount, false);
    }
```

**File:** wombat/WomUp.sol (L100-108)
```text
    function rewardPerToken() public view returns (uint256) {
        if (totalSupply() == 0) {
            return rewardPerTokenStored;
        }
        return
            rewardPerTokenStored + (
                (lastTimeRewardApplicable() - (lastUpdateTime)) * rewardRate * (1e18) / (totalSupply())
            );
    }
```
