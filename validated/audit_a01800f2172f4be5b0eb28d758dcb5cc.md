### Title
Precision loss and griefing of `rewardPerTokenStored` in `WomUp.stake/withdraw/getReward` can permanently freeze staker yield - (File: wombat/WomUp.sol)

### Summary
`WomUp.sol` implements a Synthetix/MultiRewards-style staking reward accumulator (`rewardPerTokenStored`, `rewardRate`, `lastUpdateTime`) that is vulnerable to the same precision-loss griefing bug described in the reference report. Any unprivileged wallet can repeatedly call the permissionless `getReward()` function to shrink the time delta `dt` between reward-per-token updates, causing the division `dt * rewardRate * 1e18 / totalSupply()` to round down to zero while `lastUpdateTime` still advances, permanently freezing the MGP rewards accrued during those windows.

### Finding Description
`WomUp.sol` mirrors the MultiRewards pattern exactly: [1](#0-0) 

`rewardPerToken()` computes accrued rewards using integer division by `totalSupply()`: [2](#0-1) 

`getReward()` is a fully public function with no access restriction beyond the `updateReward(msg.sender)` modifier, meaning **any wallet** — staked or not — can invoke it to force a `rewardPerToken()`/`lastUpdateTime` update: [3](#0-2) 

Because the `updateReward` modifier unconditionally recomputes `rewardPerTokenStored` and advances `lastUpdateTime` to `lastTimeRewardApplicable()` regardless of the `account` argument (the `account != address(0)` check only gates the caller's personal `rewards`/`userRewardPerTokenPaid` bookkeeping, not the global state update), an attacker can call `getReward()` every block. Each call sets `dt = lastTimeRewardApplicable() - lastUpdateTime` to a minimal value (e.g. one block time). Whenever `dt * rewardRate * 1e18 < totalSupply()`, integer division causes the reward accrual for that whole `dt` window to round to zero, yet `lastUpdateTime` still advances — permanently erasing that reward-per-token contribution, exactly as in the referenced MultiRewards report. Since the underlying MGP reward tokens are already transferred into the contract by `initializeRewards()` (owner-provisioned) before accrual begins, the value that fails to be reflected in `rewardPerTokenStored` becomes unclaimable/stuck in the contract, permanently reducing (up to entirely zeroing) the yield available to legitimate `mWom` stakers.

### Impact Explanation
This meets the "theft or permanent freezing of unclaimed yield" bar: MGP rewards funded via `initializeRewards()` become permanently unclaimable for stakers whenever the griefing condition is satisfied, and even absent full zeroing, repeated invocation continuously chips away at `rewardPerTokenStored` accrual precision, causing an irrecoverable, ongoing loss of staker yield over the 7-day `duration`.

### Likelihood Explanation
`getReward()` requires no special privilege, no stake, and no cost beyond gas — any wallet can call it every block for the full reward `duration` (7 days), matching the attack scenario in the reference report. The severity of the griefing (partial precision loss vs. full zeroing) depends on `rewardRate` vs. `totalSupply()`, but even the partial-loss case (guaranteed to occur on every invocation due to integer division) is trivially and continuously exploitable by any actor.

### Recommendation
Track the residual amount `dt * rewardRate * 1e18 % totalSupply()` from each `rewardPerToken()` computation and carry it forward into the next update so intra-period precision loss does not accumulate or permit an attacker to zero-out reward accrual by shrinking `dt`. Alternatively, restrict permissionless calls to `updateReward` from updating global state at high frequency, or use higher fixed-point precision for `rewardPerTokenStored`.

### Proof of Concept
Analogous to the MultiRewards PoC in the reference report:
1. Owner funds `WomUp` with MGP and calls `initializeRewards()`, setting `rewardRate = rewardsAvailable / duration` and `lastUpdateTime = block.timestamp`.
2. A user stakes WOM via `stake()`, establishing `_totalSupply`.
3. An attacker (staked or not) repeatedly calls `getReward()` every block (`dt` ≈ block time), whenever `dt * rewardRate * 1e18 < _totalSupply()`.
4. Each call updates `lastUpdateTime = lastTimeRewardApplicable()` while `rewardPerToken()` rounds down to the previous `rewardPerTokenStored` value (unchanged).
5. Repeating this for the full `duration` results in `rewardPerTokenStored` never increasing, so `earned(user)` for the legitimate staker stays at 0 despite the MGP reward tokens sitting in the contract, permanently stuck and unclaimable.

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

**File:** wombat/WomUp.sol (L166-175)
```text
    function getReward() public updateReward(msg.sender) returns (bool) {
        uint256 reward = rewards[msg.sender];
        if (reward > 0) {
            rewards[msg.sender] = 0;
            IERC20(mgp).safeApprove(address(vlMGP), reward);
            vlMGP.lockFor(reward, msg.sender);
            emit RewardPaid(msg.sender, reward);
        }
        return true;
    }
```
