### Title
Permanent loss of queued MGP rewards in `WomUp` when reward period starts before any user stakes - (File: `wombat/WomUp.sol`)

### Summary
`WomUp` implements a Synthetix-style, time-based (rather than snapshot/cumulative) reward-per-token accounting model. When `initializeRewards()` sets `rewardRate` and `lastUpdateTime` while `_totalSupply` is `0`, the elapsed time before the first `stake()` call produces zero `rewardPerTokenStored` increment, yet `lastUpdateTime` is still advanced forward once a user eventually stakes. The MGP rewards allocated for that dead interval become permanently stranded in the contract with no mechanism to recover or redistribute them.

### Finding Description
`rewardPerToken()` explicitly returns the stored value unchanged whenever `totalSupply() == 0`: [1](#0-0) 

The `updateReward` modifier updates `lastUpdateTime = lastTimeRewardApplicable()` on every `stake`/`withdraw`/`getReward`/`migrate` call regardless of whether `rewardPerTokenStored` actually advanced: [2](#0-1) 

The reward stream is started by the owner's `initializeRewards()`, a normal, expected operational action (not a malicious/privileged exploit) that computes `rewardRate` from the MGP balance already deposited into the contract and immediately sets `lastUpdateTime = block.timestamp` and `periodFinish = block.timestamp + duration`: [3](#0-2) 

Nothing in `stake()` requires `_totalSupply > 0` beforehand nor blocks `initializeRewards()` from running while `_totalSupply == 0`. If any time elapses between `initializeRewards()` and the first ordinary user's `stake()` call, that entire interval's worth of `rewardRate * elapsed` MGP is silently dropped: `rewardPerTokenStored` never reflects it (since it was calculated with `totalSupply() == 0`), and `lastUpdateTime` jumps forward past that interval the moment the first `stake()` executes and runs the `updateReward` modifier: [4](#0-3) 

Because `rescueReward()` can only be called by the owner while `rewardRate == 0` (i.e., before the stream starts), there is no path to recover the lost tokens once the stream has begun: [5](#0-4) 

This mirrors the reported bug class exactly: reward-rate accrual is silently skipped when supply is zero, but the clock (`lastUpdateTime`/`lastBlockRewardApplicable`) keeps moving, permanently forfeiting the rewards for that window.

Other reward pools in this codebase (`rewards/BaseRewardPool.sol`, `rewards/BaseRewardPoolV2.sol`, `rewards/mWOMSVBaseRewarder.sol`) use a different, safe pattern: they only bump `rewardPerTokenStored` at the moment new rewards are queued, and explicitly buffer rewards into `queuedRewards` when `totalStaked() == 0`, deferring distribution until stake exists rather than losing them: [6](#0-5) 
`WomUp.sol` is the outlier that reintroduces the vulnerable rate-over-time model.

### Impact Explanation
Any MGP tokens allocated to the reward interval that elapses between `initializeRewards()` and the first `stake()` (or any interval after `_totalSupply` returns to `0`, e.g., after all users withdraw) are permanently and irrecoverably lost — they remain locked in the `WomUp` contract, unclaimable by any user and unrecoverable by the owner. This constitutes permanent freezing/loss of unclaimed yield.

### Likelihood Explanation
This requires no attacker action or malicious admin behavior — it occurs under the normal, expected sequence of operations (owner starts the reward stream, then users stake over time), since there is no code path preventing or compensating for a gap between `initializeRewards()` and the first genuine `stake()`. Any nonzero delay (even a single block/transaction ordering gap) triggers the loss, and the loss is proportional to elapsed time × `rewardRate`, which can be significant over a 7-day `duration`.

### Recommendation
Add a check in `initializeRewards()` (or in `stake()`) preventing/handling the zero-supply reward-start condition, e.g. require `_totalSupply > 0` before allowing rewards to start accruing, or delay `lastUpdateTime` initialization until the first stake occurs, similar to how `BaseRewardPool`/`mWOMSVBaseRewarder` buffer rewards via `queuedRewards` when stake is zero instead of streaming them against a zero-supply denominator.

### Proof of Concept
1. Owner deposits MGP into `WomUp` and calls `initializeRewards()`, setting `rewardRate > 0`, `lastUpdateTime = T0`, `periodFinish = T0 + duration`. At this point `_totalSupply == 0`.
2. Time passes (e.g., `Δt` seconds) with no stakers, i.e., `totalSupply() == 0`.
3. A user calls `stake(amount)`. The `updateReward` modifier executes: `rewardPerTokenStored = rewardPerToken()`, which — since `totalSupply() == 0` at the time of evaluation (before `_totalSupply` is incremented later in the function body) — returns the unchanged stored value; then `lastUpdateTime` is set to `lastTimeRewardApplicable()` (≈ `T0 + Δt`).
4. The reward corresponding to `Δt * rewardRate` is never reflected in `rewardPerTokenStored` and is never attributed to any user going forward, since the accrual clock has already moved past that window.
5. `rescueReward()` cannot recover these tokens because `rewardRate > 0` at this point, so the funds are permanently stuck in the contract.

### Citations

**File:** wombat/WomUp.sol (L76-84)
```text
    modifier updateReward(address account) {
        rewardPerTokenStored = rewardPerToken();
        lastUpdateTime = lastTimeRewardApplicable();
        if (account != address(0)) {
            rewards[account] = earned(account);
            userRewardPerTokenPaid[account] = rewardPerTokenStored;
        }
        _;
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

**File:** wombat/WomUp.sol (L119-132)
```text
    function stake(uint256 _amount) public updateReward(msg.sender) returns (bool) {
        if (_amount == 0) revert MustNotZero();

        _totalSupply = _totalSupply + (_amount);
        _balances[msg.sender] = _balances[msg.sender] + (_amount);

        IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        IERC20(wom).safeApprove(address(mWom), _amount);
        mWom.deposit(_amount);

        emit Staked(msg.sender, _amount);

        return true;
    }
```

**File:** wombat/WomUp.sol (L187-194)
```text
    function rescueReward() public onlyOwner {
        if(block.timestamp >= startTime || rewardRate > 0) revert AlreadyStarted();

        uint256 balance = IERC20(mgp).balanceOf(address(this));
        IERC20(mgp).safeTransfer(owner(), balance);

        emit Rescued();
    }
```

**File:** wombat/WomUp.sol (L200-214)
```text
    function initializeRewards() external onlyOwner returns (bool) {
        if(rewardRate > 0) revert MustZero();

        uint256 rewardsAvailable = IERC20(mgp).balanceOf(address(this));
        if(rewardsAvailable == 0) revert MustNotZero();

        rewardRate = rewardsAvailable / (duration);

        lastUpdateTime = block.timestamp;
        periodFinish = block.timestamp + (duration);

        emit RewardAdded(rewardsAvailable);

        return true;
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
