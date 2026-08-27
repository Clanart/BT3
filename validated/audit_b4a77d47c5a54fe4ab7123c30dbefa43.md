### Title
Rewards distributed by `WomUp.initializeRewards` are permanently lost for the period before the first staker joins - (File: wombat/WomUp.sol)

### Summary
`WomUp.sol` implements a Synthetix-style staking rewards mechanism that streams MGP rewards (via `vlMGP.lockFor`) to users who deposit WOM through `stake()`. The reward accounting sets `periodFinish` and `rewardRate` at the moment `initializeRewards()` is called, rather than when the first staker actually enters the pool. If there is a delay between reward initialization and the first `stake()` call, the reward rate accrues against a zero `totalSupply`, and that portion of tokens is never credited to anyone and becomes permanently stuck in the contract.

### Finding Description
`initializeRewards()` computes `rewardRate = rewardsAvailable / duration` and immediately sets `periodFinish = block.timestamp + duration` and `lastUpdateTime = block.timestamp`, regardless of whether any user has staked: [1](#0-0) 

`rewardPerToken()` only accumulates rewards once `totalSupply() > 0`; while `_totalSupply == 0` it simply returns the stored (stale) value instead of tracking elapsed time: [2](#0-1) 

Because `updateReward` (used by `stake`, `withdraw`, `getReward`, `migrate`) always sets `lastUpdateTime = lastTimeRewardApplicable()` on the first `stake()`, the clock for the very first staker starts at that later timestamp, not at `initializeRewards()` time: [3](#0-2) [4](#0-3) 

Since `periodFinish` was fixed at `initializeRewards()` time and does not shift forward to account for the delay, any reward that would have accrued during `[initializeRewards timestamp, first stake timestamp]` is never attributed to `rewardPerTokenStored` and is unrecoverable — `rescueReward()` can only be called before `rewardRate` is set (`if(block.timestamp >= startTime || rewardRate > 0) revert AlreadyStarted();`), so the contract has no way to reclaim or later distribute the lost slice of rewards: [5](#0-4) 

This is the exact bug class described in the external report: rewards for the initial period between reward-stream activation and the first participant joining are permanently lost because the reward clock starts at activation time instead of first-deposit time.

### Impact Explanation
Any MGP allocated to the `duration`-long reward stream that corresponds to the time elapsed before the first `stake()` call is permanently unclaimable by any account and remains locked in the `WomUp` contract with no owner recovery path once `rewardRate > 0`. This is a permanent freezing/loss of yield intended for WOM stakers, matching the "theft or permanent freezing of unclaimed yield" impact category.

### Likelihood Explanation
`initializeRewards()` is a normal, expected operational call (not a malicious-admin scenario) that funds the reward stream for regular unprivileged stakers; the loss depends only on the ordinary and plausible delay between funding and the first `stake()` transaction, which can easily exceed short windows given real-world deployment/announcement lag, making this readily triggerable in normal usage.

### Recommendation
Defer setting `periodFinish`/`lastUpdateTime` (or otherwise start the reward clock) until `_totalSupply` transitions from zero to non-zero (i.e., when the first staker calls `stake()`), rather than at `initializeRewards()` call time, consistent with the mitigation recommended for the referenced Synthetix-derivative bug class.

### Proof of Concept
1. Owner calls `initializeRewards()` at time `T`, funding e.g. 7 days (`duration`) worth of MGP rewards. This sets `rewardRate = rewardsAvailable / duration`, `lastUpdateTime = T`, `periodFinish = T + duration`. [6](#0-5) 
2. No user calls `stake()` for `Y` seconds (e.g., 1 day) after `T`.
3. The first staker calls `stake()` at `T + Y`. Inside `updateReward`, `rewardPerToken()` returns `rewardPerTokenStored` unchanged (since `totalSupply()` was 0 the whole time), and `lastUpdateTime` is set to `T + Y`. [3](#0-2) [2](#0-1) 
4. `periodFinish` remains `T + duration` (not extended by `Y`), so only `duration - Y` worth of rewards will ever be distributed to stakers; the `Y * rewardRate` MGP tokens transferred/approved for this stream are never credited to `rewardPerTokenStored` and are stuck in the contract permanently, since `rescueReward()` is blocked once `rewardRate > 0`. [5](#0-4)

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
