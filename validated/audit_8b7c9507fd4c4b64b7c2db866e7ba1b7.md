### Title
Reward-per-token calculation relies on raw ERC20 balance of MasterMagpie, letting anyone dilute/freeze staker yield via a direct token donation - ([File: rewards/BaseRewardPool.sol], [File: rewards/BaseRewardPoolV2.sol])

### Summary
`BaseRewardPool` and `BaseRewardPoolV2` compute `totalStaked()` as the raw ERC20 balance of the staking token held by the `MasterMagpie` operator contract, rather than from any independently-tracked "shares deposited" counter. Because any unprivileged wallet can transfer staking tokens directly to `MasterMagpie` without going through `deposit()`/`depositFor()`, an attacker can inflate `totalStaked()` at will right before a reward is queued, silently diluting `rewardPerTokenStored` for all legitimate stakers — the same class of bug as the original report, where an attacker manipulates an accounting value (there, EL+CL balance vs. exit count; here, `totalStaked()` vs. actual deposited shares) by directly sending funds to the contract to corrupt a downstream ratio/threshold calculation, with no check that the balance used for accounting matches tracked internal state.

### Finding Description
`totalStaked()` in both reward pool implementations is defined purely from the token balance of the operator: [1](#0-0) [2](#0-1) 

This value is then used as the denominator when a manager provisions new rewards: [3](#0-2) 

`balanceOf(_account)`, in contrast, is derived from `MasterMagpie`'s internal `stakingInfo` bookkeeping (per-user `UserInfo.amount`), which is only updated through `deposit`/`withdraw`/`depositFor`/`withdrawFor`: [4](#0-3) [5](#0-4) 

Because `totalStaked()` is sourced from a raw `balanceOf(operator)` call while individual user shares come from the separate `UserInfo.amount` mapping, any ERC20 tokens sent directly to the `MasterMagpie` contract address (bypassing `deposit`) are counted in the denominator of `rewardPerTokenStored` but are never credited to any user's share. There is no reconciliation/check ensuring `IERC20(stakingToken).balanceOf(operator) == sum(UserInfo.amount)` before rewards are distributed, mirroring the missing-check root cause from the original report (`collateralsCountToReturn <= exitedCount - collateralReturnedCount` was never validated against the tracked state).

### Impact Explanation
An attacker (or anyone, even unintentionally) can send extra staking tokens straight to `MasterMagpie` immediately before a `queueNewRewards`/`donateRewards` call. This permanently and irreversibly increases `totalStaked()` for that reward-provisioning event, reducing `rewardPerTokenStored` awarded to every genuine staker for that reward tranche. Because `rewardPerTokenStored` is cumulative and never re-based, this dilution is baked into the historical reward-per-token forever, permanently reducing/freezing the yield legitimate stakers would otherwise have earned from that provisioning event. This falls under "permanent freezing of ... unclaimed yield."

### Likelihood Explanation
Any unprivileged wallet can call `IERC20(stakingToken).transfer(masterMagpie, amount)` directly — no special role or front-running of privileged transactions is even required (though front-running a `queueNewRewards` call maximizes the dilution). The staking token contracts (LP/receipt tokens, mWOM, mWomSV, etc.) are standard ERC20s with no transfer restriction into `MasterMagpie`, so this is trivially reachable from a normal wallet's transaction.

### Recommendation
Track `totalStaked` for each pool as an internal counter updated exclusively by `_deposit`/`_withdraw` in `MasterMagpie` (as is already done for per-user `UserInfo.amount`), and have `BaseRewardPool`/`BaseRewardPoolV2.totalStaked()` read from that tracked counter instead of the raw `IERC20(stakingToken).balanceOf(operator)`. This removes the ability for externally donated tokens to be silently absorbed into the reward-rate denominator.

### Proof of Concept
1. Attacker identifies an upcoming `queueNewRewards(amount, rewardToken)` call for a pool (manager-triggered harvest cadence is usually public/predictable, e.g. via `WombatStaking._toMasterWomAndSendReward`).
2. Attacker front-runs by transferring an arbitrary amount of the pool's `stakingToken` directly to the `MasterMagpie` contract address (no `deposit()` call, so no `UserInfo.amount` is created for the attacker).
3. `BaseRewardPoolV2.totalStaked()` now returns the inflated `balanceOf(operator)`.
4. The manager's `queueNewRewards` executes `_provisionReward`, computing `rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked()` using the inflated denominator, permanently lowering the reward rate credited to all real stakers for this reward tranche.
5. Attacker can withdraw the donated tokens later (if not itself locked/consumed) since no share was ever credited to them for it, or simply grief the pool at near-zero cost, permanently reducing distributed yield to legitimate stakers.

### Citations

**File:** rewards/BaseRewardPool.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
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

**File:** rewards/BaseRewardPoolV2.sol (L130-136)
```text
    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L297-313)
```text
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

**File:** rewards/MasterMagpie.sol (L334-358)
```text
    /// @notice Deposits staking token to the pool, updates pool and distributes rewards
    /// @param _stakingToken Staking token of the pool
    /// @param _amount Amount to deposit to the pool
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }

    /// @notice Withdraw staking tokens from Master Mgapie.
    /// @param _stakingToken Staking token of the pool
    /// @param _amount amount to withdraw
    function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _withdraw(_stakingToken, msg.sender, _amount, false);
    }

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
