## Title
Direct token donation to MasterMagpie inflates `BaseRewardPool.totalStaked()` denominator, permanently diluting `rewardPerTokenStored` for legitimate stakers - ([File: rewards/BaseRewardPool.sol], [File: rewards/BaseRewardPoolV2.sol])

## Summary
`totalStaked()` in both `BaseRewardPool.sol` and `BaseRewardPoolV2.sol` returns `IERC20(stakingToken).balanceOf(operator)` instead of a tracked accounting variable such as the sum of `UserInfo.amount` in `MasterMagpie`. Because `MasterMagpie._deposit` uses plain `safeTransferFrom` [1](#0-0) , any unprivileged holder of the staking token can send it directly to the `MasterMagpie` contract address with a plain `IERC20.transfer`, inflating `totalStaked()` without creating a matching `UserInfo` entry.

## Finding Description
`totalStaked()` is defined as a live balance read: [2](#0-1)  and identically in V2: [3](#0-2) . This value is used as the denominator in `_provisionReward`, which is invoked from `queueNewRewards` (manager-gated) and also from the permissionless `donateRewards`: [4](#0-3) .

Because `MasterMagpie` does not reconcile `pool.stakingToken` balance against the sum of `UserInfo.amount`, an attacker can call `IERC20(stakingToken).transfer(masterMagpieAddress, dustAmount)` directly. This increases `IERC20(stakingToken).balanceOf(operator)` — and therefore `totalStaked()` — without any corresponding `UserInfo` entry being created, since only `_deposit` (which requires `safeTransferFrom` triggered by the depositor) updates `user.amount`: [5](#0-4) .

When a reward is next provisioned via `_provisionReward`, the division `(_amountReward * 10**stakingDecimals()) / totalStaked()` uses the now-inflated denominator, permanently reducing `rewardPerTokenStored` growth for every legitimate staker relative to the correct value that would result from `sum(userInfo.amount)`. Because the donated tokens are never withdrawable (no `UserInfo` entry exists to reclaim them, and `withdraw`/`_withdraw` reduce `user.amount`, not raw balance), the inflated balance persists indefinitely, and the dilution effect on `rewardPerTokenStored` compounds with every subsequent reward provisioning call, permanently reducing future yield accrual for legitimate stakers.

No modifier, `nonReentrant` guard, or reward-index update logic prevents this because the exploit does not go through any MasterMagpie or BaseRewardPool function at all — it is a raw ERC20 transfer to an address, which is unstoppable by contract-level access control.

## Impact Explanation
This is a permanent, cumulative reward-dilution / yield-freezing griefing vector affecting **all** stakers of the given staking token pool. Every future `queueNewRewards`/`donateRewards` call computes a lower `rewardPerTokenStored` increment than it should, so a portion of newly queued rewards effectively becomes unclaimable/diluted relative to correct accounting — this matches the "permanent freezing of unclaimed yield" impact class. The dust tokens sent by the attacker are also permanently stuck in `MasterMagpie` (no path to reclaim them, satisfying "funds frozen >24h").

However, the *magnitude* of the impact is proportional to `dustAmount / totalStaked()` and does not require the attacker to lose more than the dust itself — it is a low-cost griefing attack rather than a large fund-draining exploit. The attacker's own donated tokens are lost, and the dilution to other stakers is bounded by the ratio of donated dust to real total staked amount, meaning a meaningful economic impact would require a proportionally large donation.

## Likelihood Explanation
- Preconditions: attacker must hold the specific staking/receipt token for a pool that has active manager-driven reward provisioning (or use permissionless `donateRewards` on an already-registered reward token).
- Capital needed: minimal — enough tokens to make a "dust" transfer; magnitude of dilution scales with amount donated relative to real total staked.
- Feasibility: trivial, single ERC20 `transfer` call, no special timing needed beyond happening before a `queueNewRewards`/`donateRewards` call (which occurs periodically as part of normal operation).
- Repeatability: repeatable indefinitely by the same or different attackers, and effect is cumulative/permanent since the balance is never purged.

## Recommendation
Replace the live-balance-based `totalStaked()` with an internally tracked accounting variable (e.g., a running total incremented/decremented exclusively in `_deposit`/`_withdraw` in `MasterMagpie`, exposed via `stakingInfo`/a dedicated view function), rather than reading `IERC20(stakingToken).balanceOf(operator)`. This removes any dependency on the token balance, which can be manipulated by direct transfers, and ensures `totalStaked()` always equals `sum(userInfo[stakingToken][*].amount)`.

## Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, a mock staking/receipt ERC20, and a `BaseRewardPool` (or `BaseRewardPoolV2`) wired as its rewarder; register the pool via `add`.
2. Two legitimate stakers deposit equal amounts via `MasterMagpie.deposit`.
3. Baseline: manager calls `queueNewRewards(rewardAmount, rewardToken)`; record `rewardPerToken(rewardToken)` and each staker's `earned()`.
4. Reset state (or run as a second scenario): before `queueNewRewards`, an unprivileged attacker (holding some of the staking token, e.g., airdropped or purchased) calls `stakingToken.transfer(masterMagpieAddress, dustAmount)` directly — no `deposit`/`depositFor` call.
5. Assert `IBaseRewardPool(rewarder).totalStaked() > userAAmount + userBAmount` (i.e., `totalStaked() != sum(userInfo[stakingToken][*].amount)`).
6. Manager calls `queueNewRewards(rewardAmount, rewardToken)` again with identical `rewardAmount`.
7. Assert `rewardPerToken(rewardToken)` in the attack scenario is strictly less than in the baseline scenario, and that each legitimate staker's `earned(rewardToken)` is correspondingly reduced (quantify the shortfall as `dustAmount / (dustAmount + totalLegitimateStaked)` fraction of the queued reward).
8. Assert there is no function callable by the attacker or anyone else that returns the donated `dustAmount` from `MasterMagpie` (i.e., it remains permanently locked in the contract with no corresponding `UserInfo` entry).

### Citations

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

**File:** rewards/BaseRewardPool.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPool.sol (L276-318)
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
```

**File:** rewards/BaseRewardPoolV2.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```
