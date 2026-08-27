### Title
Permissionless `donateRewards` combined with instantaneous, un-vested `rewardPerTokenStored` update allows just-in-time stake front-running to capture disproportionate share of donated yield - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`donateRewards(_amountReward, _rewardToken)` has no access control and immediately folds the donated amount into `rewardInfo.rewardPerTokenStored` using `totalStaked()` evaluated at call time [1](#0-0) . Because `balanceOf(_account)` is read live from `IMasterMagpie.stakingInfo` at the time rewards are updated/claimed, rather than from a time-weighted or snapshot balance, an attacker who stakes immediately before a donation lands and calls `getReward` immediately after receives a share of the donated reward proportional to their newly-added stake, despite a near-zero holding period.

### Finding Description
`_provisionReward` computes the new `rewardPerTokenStored` as `rewardInfo.rewardPerTokenStored + (_amountReward * 1e18) / totalStaked()` at the exact block of the call [2](#0-1) . The `updateReward`/`updateRewards` modifiers and `_earned` calculate a user's owed rewards as `usermWOMSVShare * (rewardPerToken - userRewardPerTokenPaid) / 1e18` [3](#0-2) , where `usermWOMSVShare` is the account's *current* balance from `balanceOf`, not a time-integrated balance [4](#0-3) .

`donateRewards` is callable by any address — it only checks `isRewardToken[_rewardToken]`, with no `onlyManager`/`onlyMasterMagpie` restriction [5](#0-4) . This means the reward-per-token index can be pushed forward by anyone at a time of their choosing (or in response to observing a pending `queueNewRewards` transaction from the legitimate `rewardManager` in the mempool). Combined with the fact that `balanceOf` has no minimum holding-period requirement enforced within this contract, a staker who deposits in the same block as (or immediately before) a reward injection is treated identically to a staker who has held their position for a long time — they both receive the full pro-rata share of `rewardPerTokenStored` delta based on current balance at the time `getReward`/`updateReward` is invoked.

However, I was unable to fully verify within the available context whether `MasterMagpie` (the contract that actually manages `stakingInfo` and gates `getReward`/`getRewards` via the `onlyMasterMagpie` modifier at lines 233–261) imposes any deposit lock, cooldown, or minimum staking duration on the `mWOMSV` staking token before a user's stake counts toward `balanceOf`/`totalStaked()`, or before a user is permitted to withdraw. This file's own logic contains no such protection, but the real-world exploitability (i.e., whether the attacker can freely stake and immediately be credited, and whether they need to hold or can withdraw right after harvesting) depends on `MasterMagpie.sol`'s deposit/withdraw implementation, which I could not fully inspect in this pass.

### Impact Explanation
If `MasterMagpie` does not enforce a lock/cooldown on `mWOMSV` deposits, this allows an attacker to capture a share of yield (donated via either `donateRewards` or `queueNewRewards`) that is intended to accrue to long-term stakers, diluting the share ultimately available to existing depositors. This matches the "theft of unclaimed yield" impact class, since existing stakers permanently lose the diluted portion of the reward to a staker with near-zero economic exposure/holding time.

### Likelihood Explanation
Feasibility depends entirely on capital available to stake immediately before a reward injection (either the attacker's own `donateRewards` call, which is self-defeating economically, or front-running a legitimate `queueNewRewards`/third-party `donateRewards` transaction visible in the mempool) and on whether `MasterMagpie` allows instantaneous stake/withdraw of `mWOMSV` with no cooldown. Given `mWOMSV` is described as freely tradable/holdable by the attacker per the preconditions, and this contract exposes no independent time-weighting protection, the attack is repeatable every time a sizeable reward injection is anticipated or observed in the mempool, requiring only enough capital to temporarily out-stake existing depositors and gas/MEV priority to land the stake transaction just before the donation.

### Recommendation
- Restrict `donateRewards` to trusted roles (`onlyManager`) or otherwise rate-limit/stream the effect of donations (e.g., linear vesting of the reward over a period, similar to Synthetix `StakingRewards`' `rewardRate`/`periodFinish` model) instead of instantaneously updating `rewardPerTokenStored`.
- If `donateRewards` must remain permissionless, introduce a minimum staking duration or checkpoint-based (time-weighted) balance for reward eligibility, so freshly staked balances do not immediately participate in already-queued/donated rewards.
- Consider a deposit lock or reward-eligibility delay enforced at the `MasterMagpie` level for `mWOMSV` staking, verified independently from this rewarder.

### Proof of Concept
Hardhat test plan:
1. Deploy `mWOMSVBaseRewarder`, `MasterMagpie` (or a mock matching its `stakingInfo`/deposit/withdraw behavior), a mock `mWOMSV` locker token, and a reward ERC20 registered via `queueNewRewards` once to populate `isRewardToken`.
2. Seed an existing staker (`Alice`) with a large `mWOMSV` stake via `MasterMagpie` deposit, and let some time pass without claiming.
3. Attacker (`Bob`) holds a small amount of `mWOMSV` (or acquires it), then in the same block:
   a. `Bob` calls `MasterMagpie.deposit(stakingToken, amount)` to stake into `mWOMSVBaseRewarder`.
   b. Immediately call `donateRewards(X, rewardToken)` (either as `Bob` himself, or simulate a third-party `queueNewRewards`/`donateRewards` call being front-run).
   c. `Bob` calls `MasterMagpie.harvest`/`getReward` for himself.
4. Assert: `Bob` receives `Bob.balance * X / totalStaked()` in reward tokens despite holding the stake for 0 blocks, and that `Alice`'s subsequent `earned()` value is reduced (diluted) by `Bob`'s participation compared to a baseline scenario where `Bob` did not stake before the donation.
5. If `MasterMagpie` permits it, additionally show `Bob` can withdraw his stake in the very next transaction, completing a zero-duration deposit/harvest/withdraw cycle with positive reward extraction — confirming the JIT-staking exploit end-to-end (this final step requires verifying `MasterMagpie`'s withdraw logic, which was not confirmed in this analysis).

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L146-149)
```text
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L296-326)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }    

    /* ============ Internal Functions ============ */

    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20Metadata(_rewardToken).safeTransferFrom(
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
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L378-383)
```text
    function _earned(address _account, address _rewardToken, uint256 _userMWOMSVShare) internal view returns (uint256) {
        return ((_userMWOMSVShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**mWOMSVDecimal) + userRewards[_rewardToken][_account];
    }
```
