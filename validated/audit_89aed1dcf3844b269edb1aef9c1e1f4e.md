## Title
Rewards permanently lost when `WomUp` staking pool has zero total supply - (File: `wombat/WomUp.sol`)

### Summary
`WomUp.sol` implements a Synthetix-style staking reward pool for `mWom`/vlMGP rewards. Its `updateReward` modifier advances `lastUpdateTime` unconditionally, even when `_totalSupply` is zero, exactly matching the reported bug class where reward accounting variables are updated while `totalSupply == 0`, causing MGP rewards accrued during that window to be permanently lost.

### Finding Description
The `updateReward` modifier always executes:
```solidity
modifier updateReward(address account) {
    rewardPerTokenStored = rewardPerToken();
    lastUpdateTime = lastTimeRewardApplicable();
    ...
}
``` [1](#0-0) 

and `rewardPerToken()` short-circuits when `totalSupply() == 0`, returning the stored value unchanged rather than accumulating the elapsed-time reward:
```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalSupply() == 0) {
        return rewardPerTokenStored;
    }
    return rewardPerTokenStored + (
        (lastTimeRewardApplicable() - (lastUpdateTime)) * rewardRate * (1e18) / (totalSupply())
    );
}
``` [2](#0-1) 

Because `lastUpdateTime` is still advanced to `block.timestamp` (via `lastTimeRewardApplicable()`) whenever any account calls `stake`, `withdraw`, `migrate`, or `getReward` — all of which apply the `updateReward` modifier — any elapsed time during which `_totalSupply == 0` is "consumed" from the reward stream without ever being credited to `rewardPerTokenStored`. The `rewardRate * elapsed` amount of MGP that would have accrued during that window is never distributed to any user and is not requeued anywhere; it remains stuck in the contract with no accounting reference to later recover it.

This mirrors the reported root cause precisely: the timestamp/accumulator used to track "how much time has this rate been active for" advances unconditionally, while the actual reward-per-token accumulation is skipped when supply is zero — so the reward for that skipped window is silently and permanently forfeited.

### Impact Explanation
This causes a permanent freeze of unclaimed yield: MGP tokens that were meant to be distributed as `WomUp` rewards during any zero-total-supply window become unrecoverable — they sit in the contract balance but no `rewardPerTokenStored` accounting exists to make them claimable by any user, and there is no rescue/requeue path (`rescueReward()` is only usable before `rewardRate > 0` is set). This satisfies the "theft or permanent freezing of unclaimed yield" impact bar.

### Likelihood Explanation
This is trivially reachable by ordinary wallets with no privileged role required:
1. Owner calls `initializeRewards()` once to start the reward stream (an intended, ordinary admin action, not an attack — same precondition assumption as in the original report where a distributor simply funds a gauge).
2. All stakers withdraw fully via `withdraw()`, driving `_totalSupply` to zero (perfectly ordinary user behavior, e.g., end of a promotion or general churn).
3. Time passes while `_totalSupply == 0`.
4. The next ordinary user calls `stake()`, which triggers `updateReward` and silently advances `lastUpdateTime`, permanently forfeiting the reward accrued during the gap.

No special timing or malicious coordination is needed; this can happen naturally any time the pool is temporarily empty, and any regular staker's `stake`/`withdraw`/`getReward` call afterward finalizes the loss.

### Recommendation
Only advance `lastUpdateTime` (and only treat time as "used up" against `rewardRate`) when `_totalSupply > 0`. If `totalSupply() == 0`, leave `lastUpdateTime` unchanged (or track/pause the reward clock) so that once someone stakes again, the reward-per-token calculation still accounts for the full elapsed emission, e.g.:
```solidity
modifier updateReward(address account) {
    rewardPerTokenStored = rewardPerToken();
    if (totalSupply() > 0) {
        lastUpdateTime = lastTimeRewardApplicable();
    }
    ...
}
```

### Proof of Concept
1. Owner calls `initializeRewards()` — sets `rewardRate`, `lastUpdateTime = now`, `periodFinish = now + duration`. [3](#0-2) 
2. Alice stakes; later Alice calls `withdraw(amount, false)` for her full balance, bringing `_totalSupply` to 0. This call runs `updateReward`, setting `lastUpdateTime = block.timestamp` and `rewardPerTokenStored` unchanged from her withdrawal moment. [4](#0-3) 
3. Time passes (e.g., 2 days) with `_totalSupply == 0`; `rewardRate` continues to notionally "emit" over `[lastUpdateTime, now]`.
4. Bob calls `stake(amount)`. The `updateReward` modifier runs `rewardPerTokenStored = rewardPerToken()`, which — because `totalSupply() == 0` at the time of evaluation (before `_totalSupply` is incremented later in the same function) — returns the unchanged stored value, then sets `lastUpdateTime = block.timestamp`, permanently skipping accrual for the 2-day gap. [5](#0-4) 
5. The MGP corresponding to `rewardRate * 2 days` remains in the contract's MGP balance but is never reflected in `rewardPerTokenStored` for any subsequent claim — it is permanently frozen.

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

**File:** wombat/WomUp.sol (L134-148)
```text
    function withdraw(uint256 amount, bool claim) public updateReward(msg.sender) returns (bool) {
        if (amount == 0) revert MustNotZero();

        _totalSupply = _totalSupply - (amount);
        _balances[msg.sender] = _balances[msg.sender] - (amount);

        IERC20(mWom).safeTransfer(msg.sender, amount);
        emit Withdrawn(msg.sender, amount);

        if (claim) {
            getReward();
        }

        return true;
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
