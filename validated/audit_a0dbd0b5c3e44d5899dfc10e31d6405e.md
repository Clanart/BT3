### Title
First-staker capture of empty-pool reward backlog via unrestricted `donateRewards` - (File: rewards/BaseRewardPoolV2.sol)

### Summary
`donateRewards(uint256 _amountReward, address _rewardToken)` in `BaseRewardPoolV2.sol` is callable by any unprivileged address and internally routes to `_provisionReward`, the same accrual logic used by `queueNewRewards`. While `totalStaked() == 0`, every provisioned amount is parked in `rewardInfo.queuedRewards`; the next provisioning call that occurs once `totalStaked() > 0` folds the *entire* accumulated backlog into a single `rewardPerTokenStored` jump computed against whatever `totalStaked()` is at that instant. Because `balanceOf` reads live stake from `IMasterMagpie(operator).stakingInfo`, an attacker who deposits stake immediately before that transition (either by front-running a pending `queueNewRewards` tx, or simply calling `donateRewards` themselves with 1 wei) becomes the party that the whole backlog is credited to.

### Finding Description
Root cause is in `_provisionReward`: [1](#0-0) 

- If `totalStaked() == 0`, incoming rewards (from either `donateRewards` or `queueNewRewards`) only increase `queuedRewards`, no `rewardPerTokenStored` update occurs, so no stake is "on the hook" for it yet.
- The very next time `_provisionReward` runs while `totalStaked() > 0`, the code folds `queuedRewards` into `_amountReward` and computes `rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked()`. This uses the **current** `totalStaked()`, not any historical or time-weighted stake distribution over the period the backlog accrued.
- `donateRewards` has no access control beyond `isRewardToken[_rewardToken]`: [2](#0-1) 
  This means any unprivileged EOA holding as little as 1 wei of a registered reward token can trigger the transition themselves — no need to even wait for `queueNewRewards`, though front-running a pending `queueNewRewards` tx works identically.
- `balanceOf` and `earned` are computed live from `IMasterMagpie(operator).stakingInfo`, with no vesting, minimum holding period, or snapshot mechanism: [3](#0-2) [4](#0-3) 
- `MasterMagpie.deposit`/`withdraw` are unrestricted, unlocked, and have no cooldown: [5](#0-4) 

Exploit flow:
1. Attacker observes (or waits for) `totalStaked() == 0` for a pool with nonzero `queuedRewards` (e.g., after all legitimate stakers have withdrawn, or during a pool's early bootstrap while a reward manager keeps queuing rewards).
2. Attacker acquires the staking token on the market and calls `MasterMagpie.deposit(stakingToken, amount)`, becoming the sole (or dominant) staker.
3. Attacker calls `donateRewards(1, _rewardToken)` (or simply waits for the pending `queueNewRewards` mempool tx to land) — this triggers `_provisionReward` with `totalStaked() > 0`, crystallizing the entire `queuedRewards` backlog into `rewardPerTokenStored` in one step, attributed to `totalStaked()` which is now dominated by the attacker's fresh deposit.
4. Attacker calls `getReward`/`multiclaim` via MasterMagpie to harvest the entire backlog.
5. Attacker calls `MasterMagpie.withdraw` to exit immediately, having held stake for as little as one block.

No existing modifier (`onlyManager`, `onlyMasterMagpie`, `updateReward`) prevents this because `donateRewards` is explicitly public and reward accrual is not time-weighted — a single depositor at the exact moment of transition receives 100% of a backlog that may have accumulated over an arbitrary period during which no one had a legitimate claim, and — critically — during which other honest depositors could have staked one block later and received nothing.

### Impact Explanation
This lets an unprivileged attacker directly convert protocol-provisioned rewards (deposited by the reward manager via `queueNewRewards`, or even other users' own `donateRewards` deposits) into personal profit disproportionate to any real staking duration or risk, at the expense of stakers who would otherwise share it. This matches "Critical - Direct theft of user/protocol funds," since the attacker can extract value with capital only needed for one block of staking plus a negligible (down to 1 wei) donation amount.

### Likelihood Explanation
- No privileged role required; `donateRewards` and `MasterMagpie.deposit`/`withdraw` are fully public.
- Precondition (`totalStaked() == 0` with nonzero `queuedRewards`) is realistic: it occurs whenever a pool is newly deployed and rewards are queued before the first staker, or whenever all stakers fully exit and a reward manager (or attacker) provisions more rewards before anyone re-stakes.
- The attack is deterministic and repeatable across any `BaseRewardPoolV2` instance whenever this state occurs, and requires no flash loan (the attacker only needs to hold the staking token and 1 wei of a registered reward token) — capital cost is minimal.
- Front-running a pending `queueNewRewards` tx increases the realistic capital captured (protocol-funded rewards), but is not even required, since the attacker can self-trigger via their own `donateRewards(1, token)` call.

### Recommendation
- Do not let a single instantaneous `totalStaked()` snapshot absorb a multi-period backlog. Options:
  - Track reward accrual on a time-weighted basis (e.g., stream `queuedRewards` linearly over time once staking resumes, similar to Synthetix's `rewardRate`/`periodFinish` model) instead of an instant `rewardPerTokenStored` jump.
  - Alternatively, require a minimum staking duration/cooldown before a depositor's stake counts toward `rewardPerToken` distribution, preventing single-block capture.
  - Restrict `donateRewards` to be gated by the same `onlyManager` role as `queueNewRewards`, or at least ensure it cannot be used to opportunistically flush `queuedRewards` at attacker-chosen moments with dust amounts.

### Proof of Concept
Hardhat test outline:
1. Deploy `MasterMagpie`, a staking token, and `BaseRewardPoolV2` for that staking token with a registered reward token; set a reward manager.
2. Ensure `totalStaked() == 0`; have the reward manager call `queueNewRewards(1000e18, rewardToken)` — assert `rewards[rewardToken].queuedRewards == 1000e18` and `rewardPerTokenStored == 0`.
3. Attacker (address with no privileged role) acquires staking tokens and calls `MasterMagpie.deposit(stakingToken, 1)`.
4. Attacker calls `donateRewards(1, rewardToken)` (or simulate reward manager's `queueNewRewards` landing right after attacker's deposit to model the mempool front-run scenario).
5. Assert `rewardPerTokenStored` jumped by `(1000e18+1)*1e18/totalStaked()`, and `earned(attacker, rewardToken) ≈ 1000e18`.
6. Attacker calls `MasterMagpie.multiclaim` to harvest, then `withdraw` full stake.
7. Assert attacker's reward-token balance increased by ~the full backlog despite having staked for a single block, while `balanceOf(attacker)` and `IMasterMagpie(operator).stakingInfo(stakingToken, attacker).staked` show minimal (1 wei) actual stake — demonstrating the backlog was captured disproportionately to any staking duration.

### Citations

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

**File:** rewards/BaseRewardPoolV2.sol (L255-260)
```text
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

**File:** rewards/MasterMagpie.sol (L337-346)
```text
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }

    /// @notice Withdraw staking tokens from Master Mgapie.
    /// @param _stakingToken Staking token of the pool
    /// @param _amount amount to withdraw
    function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _withdraw(_stakingToken, msg.sender, _amount, false);
    }
```
