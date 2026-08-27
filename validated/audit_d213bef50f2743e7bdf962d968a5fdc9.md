### Title
Donation-based dilution of `rewardPerTokenStored` via unaccounted direct transfer into `MasterMagpie` - ([File: rewards/BaseRewardPool.sol], [File: rewards/BaseRewardPoolV2.sol])

### Summary
`BaseRewardPool.totalStaked()` / `BaseRewardPoolV2.totalStaked()` return the raw `IERC20(stakingToken).balanceOf(operator)` (where `operator` is `MasterMagpie`) instead of a tracked sum of `userInfo.amount`. Since `MasterMagpie` accepts any inbound ERC20 transfer of the staking token without requiring it to go through `deposit()`, an unprivileged attacker can inflate this balance directly, which is then used as the denominator in `_provisionReward` when `queueNewRewards`/`donateRewards` compute `rewardPerTokenStored`.

### Finding Description
`totalStaked()` is defined identically in both reward pool implementations: [1](#0-0) [2](#0-1) 

This value is used directly as the denominator when a manager injects rewards: [3](#0-2) 

`balanceOf(operator)` is the *entire* ERC20 balance of the `MasterMagpie` contract for that specific `stakingToken`, not a variable that is only incremented/decremented through `deposit`/`withdraw`. Looking at `MasterMagpie._deposit`, staking tokens are pulled in via `safeTransferFrom` and `user.amount`/`user.available` are updated in lockstep with the transferred amount: [4](#0-3) 

However, nothing prevents an attacker from calling `IERC20(stakingToken).transfer(masterMagpieAddress, largeAmount)` directly — a plain ERC20 transfer that does not invoke `deposit()` and therefore never touches `userInfo`. Because `totalStaked()` simply reads the token's `balanceOf`, this raises the denominator used by `_provisionReward` without any user's `amount` (the numerator basis for `earned()`/`balanceOf(_account)`) increasing: [5](#0-4) [6](#0-5) 

When the next `queueNewRewards` call occurs, `rewardPerTokenStored` is computed as `(_amountReward * 10**decimals) / totalStaked()`, so the inflated denominator permanently reduces the reward-per-token increment credited for that reward injection. This dilution is baked into `rewardPerTokenStored` (a monotonically accumulating value) and cannot be "undone" retroactively for stakers who were entitled to a larger share at that specific reward event — this is a genuine, permanent yield loss for legitimate stakers at that snapshot, since `earned()` is computed from the diluted stored value: [7](#0-6) 

No modifier, pause, or reentrancy guard blocks this because the exploit path is a plain external ERC20 `transfer` call to `MasterMagpie`'s address — it never calls any `MasterMagpie` or rewarder function, so `whenNotPaused`/`nonReentrant`/`onlyPoolHelper` checks are irrelevant and cannot intercept it.

Regarding the second scenario in the question (donate via `deposit()`, then `emergencyWithdraw()` immediately before a reward injection to shrink the denominator and inflate one's own share): `emergencyWithdraw` is gated by `whenPaused`, which requires the contract owner to have paused the system first, so an unprivileged attacker cannot trigger this path on demand: [8](#0-7) 
This part of the question is not exploitable by an unprivileged actor and is rejected.

### Impact Explanation
This matches the "theft or permanent freezing of unclaimed yield" impact class. Any legitimate staker's share of a reward injection processed while the attacker's donation inflates `totalStaked()` receives a permanently smaller `rewardPerTokenStored` increment than they should, and that lost yield is not recoverable since `rewardPerTokenStored` only accumulates forward. Repeated or well-timed donations immediately before `queueNewRewards`/`donateRewards` calls can materially and repeatedly dilute reward distribution for every pool built on `BaseRewardPool`/`BaseRewardPoolV2` (used across the Wombat- and vlMGP-related pools, including `mWOM`/`mWomSV` staking flows in `MasterMagpie`).

### Likelihood Explanation
Preconditions are minimal: the attacker only needs to hold (or acquire via swap) some amount of the target `stakingToken` and issue a plain `IERC20.transfer` to the `MasterMagpie` contract address, timed just before a manager's `queueNewRewards`/anyone's `donateRewards` call. No special privileges, flash loans, or governance access are required, and the action is fully repeatable for any pool where `stakingToken` is transferable and where reward injections are periodic/predictable. The attacker's own transferred tokens become stuck (unaccounted, unrecoverable by them since `userInfo` never reflects them), so this is primarily a griefing/yield-freezing vector rather than a direct profit vector for the attacker, but it still constitutes a real, unprivileged, reproducible loss of yield for other stakers.

### Recommendation
Replace `totalStaked()`'s reliance on raw `balanceOf(operator)` with an internally tracked total (e.g., a `totalAmount` accumulator on `MasterMagpie` or the rewarder that is only incremented/decremented inside `_deposit`/`_withdraw`/`emergencyWithdraw`), so that reward-per-token math is driven strictly by tracked stake, not by arbitrary external token transfers into the contract's address.

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, a mock ERC20 `stakingToken`, and a `BaseRewardPoolV2` rewarder registered via `add()`.
2. Have a legitimate staker `alice` call `deposit(stakingToken, 100e18)`.
3. Compute expected `rewardPerTokenStored` increment for a reward injection of `R` tokens using `R * 1e18 / 100e18` (tracked stake only).
4. Have attacker `mallory` (holding `stakingToken`, never calling `deposit`) call `stakingToken.transfer(address(masterMagpie), 900e18)` directly.
5. Assert `rewarder.totalStaked()` now returns `1000e18` even though `alice`'s `userInfo.amount` (via `stakingInfo`) is still `100e18`.
6. Call `queueNewRewards(R, rewardToken)` as an authorized manager.
7. Assert the actual `rewardPerTokenStored` increment equals `R * 1e18 / 1000e18`, i.e., 10x smaller than the expected `R * 1e18 / 100e18` from step 3.
8. Assert `alice.earned(rewardToken)` after the injection is 10x lower than it would have been absent the donation, demonstrating the permanent yield loss for the legitimate staker.

### Citations

**File:** rewards/BaseRewardPool.sol (L126-128)
```text
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
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

**File:** rewards/BaseRewardPoolV2.sol (L126-128)
```text
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
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

**File:** rewards/MasterMagpie.sol (L260-266)
```text
    function stakingInfo(address _stakingToken, address _user)
        public
        view
        returns (uint256 stakedAmount, uint256 availableAmount)
    {
        return (userInfo[_stakingToken][_user].amount, userInfo[_stakingToken][_user].available);
    }
```

**File:** rewards/MasterMagpie.sol (L438-447)
```text
    function emergencyWithdraw(address _stakingToken) external whenPaused {
        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][msg.sender];
        uint256 availableaAmount = user.available;
        user.available = 0;
        IERC20(pool.stakingToken).safeTransfer(address(msg.sender), availableaAmount);
        emit EmergencyWithdraw(msg.sender, _stakingToken, availableaAmount);
        user.amount = user.amount - availableaAmount;
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;
    }
```

**File:** rewards/MasterMagpie.sol (L482-505)
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

        if (_amount > 0)
            if (!_isVlmgp)
                emit Deposit(_account, _stakingToken, _amount);
            else
                emit DepositNotAvailable(_account, _stakingToken, _amount);
    }
```
