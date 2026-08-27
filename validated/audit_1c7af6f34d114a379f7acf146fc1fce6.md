## Title
Sole-staker reward-index inflation via zero-totalStaked queuedRewards flush — theft of previously queued yield - (File: rewards/mWOMSVBaseRewarder.sol)

## Summary
`_provisionReward` diverts new reward tokens into `rewardInfo.queuedRewards` whenever `totalStaked() == 0`, and later flushes the entire accumulated `queuedRewards` bucket plus any new amount into `rewardPerTokenStored` divided by the *current* `totalStaked()` on the next call. Since `donateRewards()` is a permissionless, unprivileged external entry point into the same `_provisionReward` logic, an attacker who becomes the sole (dust-sized) staker right after a `totalStaked()==0` reward queuing event can trigger the flush themselves and capture the whole queued pool.

## Finding Description
`queueNewRewards`/`donateRewards` both call `_provisionReward` [1](#0-0) . Inside it:

```
if (totalStaked() == 0) {
    rewardInfo.queuedRewards += _amountReward;
} else {
    if (rewardInfo.queuedRewards > 0) {
        _amountReward += rewardInfo.queuedRewards;
        rewardInfo.queuedRewards = 0;
    }
    rewardInfo.rewardPerTokenStored += (_amountReward * 10**mWOMSVDecimal) / totalStaked();
}
``` [2](#0-1) 

`totalStaked()` reads `IERC20(address(mWOMSV)).totalSupply()` [3](#0-2) , so it can genuinely be `0` in a bootstrap window before any user has locked/minted `mWOMSV` (e.g., a reward manager pre-funds incentives via `queueNewRewards` before the pool has its first staker).

`donateRewards` has no access control beyond the reward token being registered [4](#0-3) , so any unprivileged address can invoke `_provisionReward` at will.

Exploit flow:
1. Attacker monitors the mempool for a `queueNewRewards` call (or waits for the natural bootstrap state) that lands while `totalStaked() == 0`; the reward amount goes to `rewardInfo.queuedRewards`.
2. Attacker immediately mints/locks a dust amount of `mWOMSV` (e.g., 1 wei), becoming the sole holder, so `totalStaked() == 1`.
3. Attacker calls `donateRewards(1, _rewardToken)` (or any small amount) themselves. Since `totalStaked() != 0` now, the `else` branch fires: `queuedRewards` (the full previously queued pool) plus the attacker's tiny donation is divided by `totalStaked() == 1`, producing a massively inflated `rewardPerTokenStored`.
4. Attacker calls `getReward`/`getRewards` (via `masterMagpie`) and receives `balanceOf(attacker) * rewardPerTokenStored / 1e18`, i.e., essentially the entire previously queued reward pool, despite having contributed negligible stake and negligible time.
5. Any future legitimate stakers who join afterward have their `userRewardPerTokenPaid` checkpointed to the already-inflated `rewardPerTokenStored` on their first interaction (via the `updateReward`/`updateRewards` modifiers) [5](#0-4) , so they receive none of that reward — it was already drained by the attacker.

No modifier (`nonReentrant`, `whenNotPaused`, access control) prevents this: `donateRewards` is intentionally public, and the zero-supply branch is a normal code path, not a misconfiguration.

## Impact Explanation
This results in theft of unclaimed yield: reward tokens that were transferred into the contract by a legitimate reward manager and intended to be shared pro-rata among the pool's future stakers are instead captured almost entirely by an attacker holding a negligible (e.g., 1 wei) stake. This matches the Immunefi "theft of unclaimed yield" impact class and is a direct violation of the reward-conservation/fair-distribution invariant.

## Likelihood Explanation
The attack requires a specific but realistic precondition: a window where the pool-specific `mWOMSV` total supply is `0` while a reward manager (or the attacker themselves via chained donations) provisions rewards — most plausible during pool bootstrap/launch or after a period where all stakers have fully unlocked. It requires no privileged role: `donateRewards` is callable by anyone, and staking a dust amount of `mWOMSV` requires only enough underlying capital to mint 1 wei of the receipt token. The attack is capital-light, repeatable across any reward token registered on the rewarder, and only needs ordinary transaction/mempool visibility (no flash loan, no reentrancy needed).

## Recommendation
Do not let a single dust-sized staker capture an entire queued reward bucket accumulated during a zero-supply period. Options:
- Require a minimum bootstrap stake/timelock before `rewardPerTokenStored` can be updated from `queuedRewards`, or
- Distribute `queuedRewards` gradually (e.g., streaming/vesting) rather than in one lump-sum division by instantaneous `totalStaked()`, or
- Snapshot/checkpoint eligibility so `queuedRewards` can only be claimed by addresses that were part of the staker set for the full period the rewards accrued, or at minimum enforce a minimum total-staked threshold before flushing queued rewards to prevent division by near-zero denominators.

## Proof of Concept
Foundry/Hardhat test plan:
1. Deploy `mWOMSVBaseRewarder`, register `rewardToken`, mock `masterMagpie.stakingInfo` and `mWOMSV.totalSupply()`.
2. Set `mWOMSV.totalSupply()` to return `0`; have `rewardManager` call `queueNewRewards(1000e18, rewardToken)`. Assert `rewards[rewardToken].queuedRewards == 1000e18` and `rewardPerTokenStored == 0`.
3. Simulate attacker locking 1 wei of `mWOMSV` so `mWOMSV.totalSupply()` returns `1` and `masterMagpie.stakingInfo(stakingToken, attacker)` returns `1`.
4. Attacker calls `donateRewards(1, rewardToken)` (approving/transferring 1 wei of reward token). Assert `rewardPerTokenStored` jumps to `(1000e18 + 1) * 1e18 / 1`.
5. Have `masterMagpie` call `getReward(attacker, attacker)`. Assert attacker receives ~1000e18 reward tokens despite staking 1 wei.
6. Add a second legitimate staker with 1000e18 `mWOMSV` after step 4, and assert they earn 0 from the original 1000e18 queued reward pool (confirming it was fully siphoned by the attacker).

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L101-119)
```text
    modifier updateRewards(address _account, address[] memory _rewards) {
        uint256 length = _rewards.length;
        uint256 usermWOMSVAmount = balanceOf(_account);
        
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewards[index];
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;

            userRewards[rewardToken][_account] = _earned(_account, rewardToken, usermWOMSVAmount);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
        _;
    }

    modifier updateReward(address _account) {
        _updateFor(_account);
        _;
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L138-140)
```text
    function totalStaked() public override view returns (uint256) {
        return IERC20(address(mWOMSV)).totalSupply();
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L278-301)
```text
    function queueNewRewards(uint256 _amountReward, address _rewardToken)
        override
        external
        onlyManager
        returns (bool)
    {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }

        _provisionReward(_amountReward, _rewardToken);
        return true;
    }

    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }    
```

**File:** rewards/mWOMSVBaseRewarder.sol (L305-328)
```text
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
        emit RewardAdded(_amountReward, _rewardToken);
    }
```
