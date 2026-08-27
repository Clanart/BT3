### Title
Reward-per-token denominator uses raw `balanceOf(operator)` instead of tracked stake, enabling permanent yield dilution via direct token donation - (File: `rewards/BaseRewardPool.sol` / `rewards/BaseRewardPoolV2.sol`)

### Summary
`BaseRewardPool` (and `BaseRewardPoolV2`) compute the reward-per-token denominator from the raw ERC20 balance of the `stakingToken` held by `MasterMagpie` (`operator`), rather than from an internally tracked total-staked counter. Any unprivileged wallet holding the pool's `stakingToken` (e.g. mWOM, a WombatStaking receipt token, or a VLMGP/mWomSV share token) can transfer it directly to the `MasterMagpie` contract address, inflating the denominator used in `_provisionReward` without crediting any user's `balanceOf`. This mirrors the HundredFinance bug class: an exchange-rate/reward-rate denominator that is derived from a raw, donatable token balance instead of protected internal accounting, producing rounding/accounting corruption when new rewards are distributed.

### Finding Description
`totalStaked()` is defined as the plain ERC20 balance of the staking token at the `operator` (MasterMagpie) address: [1](#0-0) 

while individual user shares (`balanceOf(_account)`) come from MasterMagpie's internal `stakingInfo` accounting, not from the token balance: [2](#0-1) 

When new rewards are provisioned — either by a manager via `queueNewRewards` or by any caller via the public `donateRewards` — the reward-per-token accumulator is incremented using `this.totalStaked()` (i.e., the raw balance) as the divisor: [3](#0-2) 

The identical pattern exists in `BaseRewardPoolV2`: [4](#0-3) [5](#0-4) 

Because `totalStaked()` reads a raw `balanceOf`, and MasterMagpie's `stakingInfo` (used for `balanceOf(_account)`) is only updated through `depositFor`/`withdrawFor` flows, any wallet can independently inflate the denominator by simply transferring the relevant `stakingToken` (mWOM, a WombatStaking receipt token, vlMGP/mWomSV share token, etc.) straight to the MasterMagpie contract address, completely bypassing the deposit accounting path. This is analogous to the HundredFinance donation attack, where a raw token balance used as the exchange-rate denominator was manipulated by direct transfer rather than through the protocol's proper mint/deposit accounting, corrupting the rate used for all subsequent accounting.

### Impact Explanation
Every subsequent call to `queueNewRewards`/`donateRewards` computes `rewardPerTokenStored += (amount * 10**decimals) / totalStaked()` against the inflated, donation-poisoned denominator. This permanently and irreversibly reduces the `rewardPerTokenStored` increment attributable to real stakers for every future reward distribution — there is no mechanism to later correct `rewardPerTokenStored` or to reclaim the "lost" fractional yield, since user claims are strictly a function of `rewardPerToken` deltas. The undistributed portion of yield becomes permanently stuck relative to what genuine stakers should have earned, constituting a permanent freeze/loss of unclaimed yield for the pool's legitimate stakers, achievable purely from an unprivileged wallet.

### Likelihood Explanation
High reachability: the attack requires only (1) holding or acquiring a small amount of the target pool's `stakingToken` (these are freely tradable ERC20 receipt/derivative tokens: mWOM, WombatStaking receipt tokens, vlMGP/mWomSV shares) and (2) a plain ERC20 `transfer` to the `MasterMagpie` contract address — no privileged role, governance, or oracle interaction is needed. The corrupting effect compounds every time rewards are subsequently queued, so even a modest donation causes a persistent, worsening yield-dilution effect for all real stakers in that pool.

### Recommendation
Track `totalStaked()` via an internal accounting variable incremented/decremented exclusively inside the pool's `stake`/`withdraw` code paths (mirroring `balanceOf(_account)`'s reliance on `stakingInfo`), instead of deriving it from `IERC20(stakingToken).balanceOf(operator)`. This removes the ability of an external, unprivileged token transfer to influence the reward-per-token denominator.

### Proof of Concept
1. Identify any Magpie pool's `BaseRewardPool`/`BaseRewardPoolV2` instance and its `stakingToken` (e.g., an mWOM-backed receipt token).
2. From an unprivileged wallet holding that `stakingToken`, call `IERC20(stakingToken).transfer(masterMagpieAddress, X)` directly (no `depositFor` call), inflating `totalStaked()` returned by `BaseRewardPool.totalStaked()` without changing any account's `balanceOf` via `stakingInfo`.
3. Have a manager (or any caller, via the permissionless `donateRewards`) call `queueNewRewards`/`donateRewards` with a reward amount; observe that `rewardPerTokenStored` increases by less than it would have absent the donation, because the divisor `totalStaked()` is inflated.
4. Verify that legitimate stakers' `earned()` values are permanently lower than the actual reward amount deposited, with no path to recover the difference — demonstrating permanent yield dilution/freezing caused entirely by an unprivileged wallet.

*Note: I could not directly inspect `rewards/MasterMagpie.sol`'s `depositFor`/`withdrawFor` implementation within the available tool budget to confirm there is no additional safeguard (e.g., a balance-delta check) preventing unsolicited direct transfers from affecting `totalStaked()`. If such a safeguard exists there, it would need to be reviewed to fully validate exploitability; this should be confirmed in a full-repository review.*

### Citations

**File:** rewards/BaseRewardPool.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
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

**File:** rewards/BaseRewardPool.sol (L276-320)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }

    /* ============ Internal Functions ============ */

    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            userRewards[rewardToken][_account] = earned(_account, rewardToken);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
    }

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

**File:** rewards/BaseRewardPoolV2.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
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
