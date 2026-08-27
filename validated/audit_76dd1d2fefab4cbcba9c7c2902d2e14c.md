### Title
Unpermissioned `donateRewards` flush of `queuedRewards` against dust `totalStaked` allows theft of pooled rewards - (File: `rewards/BaseRewardPool.sol`)

### Summary
`BaseRewardPool._provisionReward` queues rewards without distributing them whenever `totalStaked() == 0`, but the flush of `queuedRewards` into `rewardPerTokenStored` is driven by whatever `totalStaked()` is at the moment *any* caller triggers `_provisionReward` again. Because `donateRewards` is callable by anyone (no `onlyManager`/access control) and internally calls `_provisionReward`, an attacker who is the sole/first (re)depositor after a zero-stake period can call `donateRewards(0, token)` to flush a manager's previously queued rewards entirely onto their own dust stake, capturing nearly 100% of rewards meant for the whole pool.

### Finding Description
`queueNewRewards` (onlyManager) and `donateRewards` (unrestricted) both call `_provisionReward`: [1](#0-0) 

`_provisionReward` contains the flawed accounting: [2](#0-1) 

- If `totalStaked() == 0` when a manager calls `queueNewRewards`, the entire `_amountReward` is added to `rewardInfo.queuedRewards` and never distributed (`rewardPerTokenStored` untouched).
- Any subsequent call to `_provisionReward` (via `donateRewards`, which has **no access control**) when `totalStaked() > 0` flushes `queuedRewards + newAmount` into `rewardPerTokenStored`, dividing by whatever `totalStaked()` is *at that instant*.
- `totalStaked()` is read live from the staking token balance held by the operator (`IERC20(stakingToken).balanceOf(operator)`), so an attacker who deposits a dust amount (e.g., 1 wei) right after the pool goes to zero stake becomes the entire `totalStaked()` denominator.
- The attacker then calls `donateRewards(0, _rewardToken)` (valid — `SafeERC20.safeTransferFrom` with amount 0 succeeds, and `donateRewards` only requires `isRewardToken[_rewardToken]==true`, not `onlyManager`). This triggers `_provisionReward`, which computes `rewardPerTokenStored += (queuedRewards * 10**decimals) / totalStaked()` — using the attacker's dust stake as denominator.
- Because the attacker deposited *before* this flush, `userRewardPerTokenPaid` was set to the pre-flush (low) value at deposit time via `_updateFor`. After the flush, `earned()` computes `balanceOf(attacker) * (rewardPerTokenStored - paid) / 10**decimals`, which evaluates to essentially the entire flushed `queuedRewards` amount, since the attacker holds ~100% of `totalStaked()`.
- No modifier, `nonReentrant`, or reward-index safeguard prevents this: `donateRewards` is intentionally public with only a `isRewardToken` check, and `_provisionReward`'s zero/non-zero `totalStaked()` branching has no minimum-stake floor, no time-locking of newly deposited stake, and no protection against flushing accumulated `queuedRewards` against a freshly/artificially minimal `totalStaked()`.

### Impact Explanation
This is a direct theft-of-user-funds vulnerability (Immunefi "Direct theft of user funds"/"Theft of unclaimed yield" class). Any legitimately queued reward balance accumulated during a period when the pool's `totalStaked()` was zero can be redirected almost entirely to a single attacker who re-enters the pool with a trivial stake and calls the permissionless `donateRewards`. This breaks the pool's reward-conservation invariant: rewards intended for future stakers pro-rata to their stake are instead capturable in full by whoever manipulates the timing/size of the first re-deposit.

### Likelihood Explanation
- Feasible with negligible capital: attacker needs only 1 wei of the staking token and a trivial gas cost to call `donateRewards(0, token)`.
- Requires the precondition that `totalStaked() == 0` at some point (e.g., mass withdrawal, new pool bootstrap, or a low-liquidity pool where an attacker can economically justify temporarily removing enough stake to zero it, though pure "all users withdrew" scenario needs no attacker capital at all) and that a manager or donor queues/sends rewards during that window.
- No privileged role is needed for the exploit step itself — `donateRewards` is intentionally public, and depositing/withdrawing stake are standard user actions.
- Highly repeatable: this can occur any time the pool naturally passes through a `totalStaked()==0` state (e.g., first pool launch before general deposits, or emergency withdrawal events), making it a systemic risk beyond a single occurrence.

### Recommendation
- Do not allow rewards to be "queued and later flushed against an arbitrary future totalStaked()". Instead, track and snapshot `queuedRewards` distribution against a minimum stake threshold, or require that `_provisionReward` reverts/holds rewards until `totalStaked()` exceeds a sane minimum (e.g., matching the reward-token's smallest meaningful unit relative to reward size).
- Restrict `donateRewards` so it cannot trigger a flush of pre-existing `queuedRewards` by an unprivileged/unrelated caller — e.g., separate the "top-up" (`donateRewards`) amount from the flush of previously queued manager rewards, or require `onlyManager`/timelocked release for `queuedRewards`.
- Consider using a virtual-shares/offset pattern (as in ERC4626 to prevent share-price manipulation) so that `rewardPerTokenStored` computations are not divisible by attacker-controlled dust `totalStaked()`.
- Alternatively, gate the transition from `queuedRewards` to `rewardPerTokenStored` on `totalStaked()` being reasonably close to its pre-zero level, or require multiple blocks/deposits before flushing, to prevent single-block dust-stake capture.

### Proof of Concept
Foundry/Hardhat test plan:
1. Deploy `BaseRewardPool` with a `stakingToken`, `rewardToken`, `masterMagpie` (mock), and a `rewardManager`.
2. Have several users deposit into `stakingToken` via the mock `MasterMagpie` so `totalStaked() > 0`.
3. Have all users withdraw fully so `totalStaked() == 0`.
4. As `rewardManager`, call `queueNewRewards(1000e18, rewardToken)`. Assert `rewards[rewardToken].queuedRewards == 1000e18` and `rewardPerTokenStored` unchanged (still 0).
5. As attacker (unprivileged EOA), deposit `1 wei` of `stakingToken` via `MasterMagpie` so `totalStaked() == 1`.
6. As attacker, call `donateRewards(0, rewardToken)` (requires `isRewardToken[rewardToken]==true`, no manager check).
7. Assert `rewards[rewardToken].rewardPerTokenStored` jumped to `(1000e18 * 10**decimals) / 1`.
8. Call `getReward(attacker, attacker)` via `masterMagpie` (mocked `onlyMasterMagpie` caller) and assert attacker receives ~`1000e18` reward tokens — i.e., the entire queued reward — despite holding only 1 wei of stake, proving conservation violation and full-pool theft. [3](#0-2) [4](#0-3)

### Citations

**File:** rewards/BaseRewardPool.sol (L261-284)
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
