### Title
Sandwiching `donateRewards`/`queueNewRewards` dilutes honest stakers' pro-rata share of reward donations - ([File: rewards/BaseRewardPool.sol], [File: rewards/BaseRewardPoolV2.sol], [File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`BaseRewardPool`, `BaseRewardPoolV2`, and `mWOMSVBaseRewarder` all expose a permissionless `donateRewards(uint256 _amountReward, address _rewardToken)` function that adds arbitrary amounts of an already-registered reward token to the pool and immediately mutates the global `rewardPerTokenStored` accumulator in proportion to `1 / totalStaked()` at that exact block. This is the same non-atomic "donate to a shared pool, valued instantly against live TVL" pattern as `LendingPool.donateToTranche` in the referenced Sherlock report (M-5), except here the donation entry point requires **no privileged role at all**. [1](#0-0) [2](#0-1) 

### Finding Description
`_provisionReward` computes the reward-per-token delta as `_amountReward * 10**decimals / totalStaked()`, where `totalStaked()` reads the live token balance held by the operator (`MasterMagpie`) at the moment of the call: [3](#0-2) [4](#0-3) 

A user's earned rewards are computed from their live `balanceOf(_account)` multiplied by the difference between the current `rewardPerToken` and the `userRewardPerTokenPaid` snapshot taken the last time they interacted (deposit/withdraw/harvest): [5](#0-4) [6](#0-5) 

Because `donateRewards` is unpermissioned (only gated on the token already being a registered reward token, not on caller identity) and there is no time-weighting, snapshotting, or vesting of the deposited reward, an ordinary wallet can:

1. **Front-run**: monitor the mempool for a pending `donateRewards` (or `queueNewRewards`) call for a pool it is not currently staked in (or under-staked in), and submit a large `deposit`/`depositFor` into `MasterMagpie` for that `stakingToken` just before it. This mints the attacker a large share of `totalStaked()` and snapshots their `userRewardPerTokenPaid` at the pre-donation rate via `_deposit` → `_harvestBaseRewarder`/`updateReward`.
2. **Sandwich**: the donation transaction executes, and `rewardPerTokenStored` jumps by `amountReward * 1e_dec / totalStaked()`, where `totalStaked()` now includes the attacker's freshly-deposited principal, so the attacker captures a share of the donation proportional to their (large, momentary) stake instead of long-term stakers capturing all of it.
3. **Back-run**: the attacker immediately calls `withdraw` on `MasterMagpie`, which triggers `_harvestAndUnstake` → harvest of the now-inflated reward share, then returns their principal in full, leaving with their original capital plus a slice of the donation that rightfully belonged to the pool's existing stakers.

This mirrors exactly the root cause identified as valid Medium in the Sherlock report: a donation function that revalues a share-based pool atomically against instantaneous stake, without any snapshot or timelock protecting against deposit-then-donate-then-withdraw sandwiching. Unlike the `LendingPool.donateToTranche` case (which required a permissioned manual liquidator to trigger the donation and was still ruled valid Medium by the final judgment), `donateRewards` here has **no caller restriction whatsoever** — any external address, and in particular the attacker itself or any third party sponsoring bonus rewards, can trigger the donation, making the unprivileged attack surface strictly broader.

### Impact Explanation
Honest long-term stakers in the affected reward pool have their share of legitimately donated/bonus reward tokens diluted by an attacker who only needs to hold stake momentarily around the donation transaction. This is a theft of yield that would otherwise accrue to existing depositors — the attacker extracts value they did not economically earn by holding stake for any meaningful duration, directly reducing the reward yield available to genuine long-term stakers of that reward token in `BaseRewardPool`/`BaseRewardPoolV2`/`mWOMSVBaseRewarder`.

### Likelihood Explanation
Likelihood is moderate: it requires (a) a `donateRewards` transaction to be visible in the mempool or otherwise predictable in timing, and (b) the attacker to have or acquire capital equal to a meaningful fraction of `totalStaked()` for that specific pool, which can be sizeable for popular pools but is easily reachable for smaller/newer pools with low TVL. No flash loan is usable since the staking token must remain deposited (non-atomic, spans at least the donation transaction), but no special privilege is required — deposit and withdraw are both open, unprivileged `MasterMagpie` functions, and `donateRewards` itself can be called by anyone (including the attacker orchestrating the whole sequence, or the attacker simply reacting to a third-party sponsor's donation).

### Recommendation
- Time-weight reward accrual (e.g., a streaming/linear-vesting distribution over a minimum duration) instead of an instantaneous `rewardPerTokenStored` bump proportional to live `totalStaked()`.
- Alternatively, require a minimum stake duration (cooldown) before a deposit's balance counts toward `earned()` for freshly-arrived stake, or snapshot eligible balances prior to processing a donation.
- Consider restricting `donateRewards` to trusted managers (as `queueNewRewards` already is) and pairing it with the same time-weighting fix, since restricting the caller alone does not solve the underlying atomic-revaluation flaw.

### Proof of Concept
1. Pool P has `totalStaked() = 100` (attacker excluded), and `rewardPerTokenStored = R0`.
2. Attacker observes a pending `donateRewards(1000, rewardToken)` call targeting pool P.
3. Attacker calls `MasterMagpie.deposit(P.pid, 900)` (or `depositFor`), becoming staked; `_deposit` harvests (nothing owed yet) and sets `userRewardPerTokenPaid[attacker] = R0`. [7](#0-6) 
4. `donateRewards(1000, rewardToken)` executes: `totalStaked() = 1000` now (100 honest + 900 attacker); `rewardPerTokenStored` increases by `1000 * 1e_dec / 1000 = 1e_dec` (i.e., 1 token per staked unit), instead of `1000/100 = 10` tokens per staked unit had the attacker not front-run. [3](#0-2) 
5. Attacker calls `MasterMagpie.withdraw(P.pid, 900)`, triggering `_harvestAndUnstake` which harvests `earned = 900 * (R0+1e_dec - R0)/1e_dec = 900` reward tokens for the attacker (90% of the entire donation) despite having staked for a single block, then returns their 900 principal in full. [8](#0-7) 
6. The 100 honest long-term stakers are left splitting only the remaining 100 reward tokens (10%) instead of the full 1000 they would have received absent the sandwich, confirming the dilution of the donation exactly as in the referenced Sherlock M-5 finding.

### Citations

**File:** rewards/BaseRewardPool.sol (L126-128)
```text
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
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

**File:** rewards/BaseRewardPoolV2.sol (L252-260)
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

**File:** rewards/MasterMagpie.sol (L482-498)
```text
    function _deposit(address _stakingToken, address _account, uint256 _amount, bool _isVlmgp) internal {
        updatePool(_stakingToken);

        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][_account];

        if (user.amount > 0) {
            _harvestMGP(_stakingToken, _account);
        }
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount + _amount;
        if (!_isVlmgp) {
            user.available = user.available + _amount;
            IERC20(pool.stakingToken).safeTransferFrom(address(msg.sender), address(this), _amount);
        }
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;
```

**File:** rewards/MasterMagpie.sol (L507-533)
```text
    /// @notice internal function to deal with withdraw staking token
    function _withdraw(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        _harvestAndUnstake(_stakingToken, _account, _amount, _isVlMgp);

        if (!_isVlMgp)
            IERC20(tokenToPoolInfo[_stakingToken].stakingToken).safeTransfer(address(msg.sender), _amount);
        emit Withdraw(_account, _stakingToken, _amount);
    }

    function _harvestAndUnstake(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        updatePool(_stakingToken);

        UserInfo storage user = userInfo[_stakingToken][_account];

        if (!_isVlMgp && user.available < _amount)
            revert WithdrawAmountExceedsStaked();
        else if(user.amount < _amount && _isVlMgp)
            revert UnlockAmountExceedsLocked();
        
        _harvestMGP(_stakingToken, _account);
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount - _amount;
        
        if(!_isVlMgp)
            user.available = user.available - _amount;
        user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
```
