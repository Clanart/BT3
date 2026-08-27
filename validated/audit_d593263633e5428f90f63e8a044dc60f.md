### Title
Unbounded growth of `accMGPPerShare` via minimal-deposit pool inflation causes permanent overflow revert in reward accounting - (File: rewards/MasterMagpie.sol)

### Summary
`MasterMagpie.updatePool()` computes `pool.accMGPPerShare` by dividing an accumulated reward value by `lpSupply` and adding it to the existing accumulator, without any bound on `lpSupply` or on how long a pool can remain at a minimal supply. An unprivileged wallet can be the sole/first depositor into a pool (or any staking token pool with a naturally small supply, e.g. low-decimal tokens) and keep `lpSupply` at 1 wei, letting `accMGPPerShare` grow unboundedly over time. When this inflated accumulator is later multiplied by a legitimate user's `amount` in `_calNewMGP`/`emergencyWithdraw`, the multiplication can overflow and revert, permanently freezing deposits, withdrawals and harvests for that pool.

### Finding Description
`updatePool()` accrues MGP rewards per share using: [1](#0-0) 

`lpSupply` is derived directly from token balances via `_calLpSupply()`, which for ordinary pools simply returns `IERC20(_stakingToken).balanceOf(address(this))`: [2](#0-1) 

An attacker can be the only depositor of a given staking token pool and deposit a minimal amount (as low as 1 wei of the staking/LP token). As long as `lpSupply` stays at 1, every call to `updatePool` (or `massUpdatePools`) adds `(multiplier * mgpPerSec * allocPoint / totalAllocPoint) * 1e12 / 1` to `pool.accMGPPerShare`. Because this is an *addition* to the stored accumulator (not an overwrite), the value grows monotonically and without bound as time passes — there is no cap on `multiplier` (elapsed seconds since last update) or on how long the attacker can keep `lpSupply == 1`.

Once `accMGPPerShare` becomes very large, any subsequent computation that multiplies it by a legitimate user's `amount` can overflow: [3](#0-2) [4](#0-3) [5](#0-4) 

This is the same root-cause bug class as the referenced Sophon `accPointsPerShare` finding: a reward-per-share accumulator scaled by a large precision constant (`1e12` here vs `1e36` in Sophon) is divided by an attacker-controllable, minimal `lpSupply`, and the resulting inflated value is subsequently multiplied by a user's real staked amount, risking an arithmetic overflow revert (Solidity ^0.8 reverts on overflow instead of wrapping).

### Impact Explanation
Once `accMGPPerShare` for a pool is inflated to the point that `user.amount * accMGPPerShare` overflows `uint256`, every call path that reads or updates that pool's rewards for a normally-sized depositor reverts:
- `updatePool`/`massUpdatePools` (called on every `deposit`/`withdraw`/`multiclaim`) — blocking `deposit`, `withdraw`, and harvest across the whole contract if `massUpdatePools` is used, or at minimum for the affected pool via `_calNewMGP`.
- `emergencyWithdraw` also computes `user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12`, so even the emergency-withdraw escape hatch can revert.

This results in a permanent freeze of user funds staked in the affected pool (and potentially cascading effects if `massUpdatePools` reverts for the whole protocol), matching the "permanent freezing of funds" impact category.

### Likelihood Explanation
Likelihood is *moderate-to-low* in practice: reaching an overflow requires the accumulator to run for a very long elapsed time (or a very large `mgpPerSec`) while `lpSupply` stays at 1 wei, since `1e12` is a smaller precision multiplier than Sophon's `1e36`. An attacker fully controls this by being the sole depositor of a low-value/low-liquidity pool and simply waiting (calling `updatePool` periodically costs no attacker funds beyond gas). No privileged role is required — any wallet that can call `deposit`/`updatePool` can set this up. The attack is more practical for pools whose staking token allows extremely small supply (e.g., a newly added pool before others deposit, or a low-decimal/low-liquidity LP token) — this mirrors the exact scenario validated in the referenced Sherlock report.

### Recommendation
- Enforce a minimum deposit amount per pool (e.g., require `lpSupply` above some floor before allowing `updatePool` to accrue against it, or require a minimum first-deposit amount), consistent with the original report's accepted fix direction.
- Alternatively, do not accrue rewards when `lpSupply` is below a sane threshold (skip accrual and simply update `lastRewardTimestamp`), or cap `multiplier` growth.
- Consider computing pending rewards with intermediate scaling that avoids unbounded growth of the per-share accumulator, or track total accrued rewards independent of a divide-then-store pattern with attacker-controlled small denominators.

### Proof of Concept
Conceptual walk-through (mirrors the original Sophon PoC pattern applied to `MasterMagpie`):
1. Attacker calls `deposit(stakingToken, 1)` to become the sole depositor, so `_calLpSupply(stakingToken) == 1`.
2. Attacker (or anyone) repeatedly calls `updatePool(stakingToken)` (directly or via any `deposit`/`withdraw`/`multiclaim` call) across a long period, each time adding `(multiplier * mgpPerSec * allocPoint / totalAllocPoint) * 1e12` to `pool.accMGPPerShare` since dividing by `lpSupply == 1` does not shrink it — see the accumulation logic at [6](#0-5) .
3. After sufficient elapsed time, `pool.accMGPPerShare` reaches a magnitude such that, once a legitimate user deposits a realistic amount (e.g., thousands of tokens with 18 decimals) into the same pool, `_calNewMGP`'s computation `user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare` — [7](#0-6)  — overflows `uint256` and reverts, blocking `multiclaim`/`withdraw`/`updatePool` for that pool.

Note: exact time-to-overflow depends on `mgpPerSec` and `allocPoint` configuration, which was not fully enumerable from the indexed code; a background Devin session with fork/unit-test tooling would be needed to compute precise parameters and demonstrate the concrete revert, as was done in the original report's Foundry PoC.

### Citations

**File:** rewards/MasterMagpie.sol (L379-396)
```text
        uint256 lpSupply = _calLpSupply(_stakingToken);
        if (lpSupply == 0) {
            pool.lastRewardTimestamp = block.timestamp;
            return;
        }        
        uint256 multiplier = block.timestamp - pool.lastRewardTimestamp;
        uint256 mgpReward = (multiplier * mgpPerSec * pool.allocPoint) / totalAllocPoint;
        
        pool.accMGPPerShare = pool.accMGPPerShare + ((mgpReward * 1e12) / lpSupply);
        pool.lastRewardTimestamp = block.timestamp;

        emit UpdatePool(
            _stakingToken,
            pool.lastRewardTimestamp,
            lpSupply,
            pool.accMGPPerShare
        );
    }    
```

**File:** rewards/MasterMagpie.sol (L438-447)
```text
    function emergencyWithdraw(address _stakingToken) external whenPaused {
        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][msg.sender];
        uint256 availableaAmount = user.available;
        user.available = 0;
        IERC20(pool.stakingToken).safeTransfer(address(msg.sender), availableaAmount);
        emit EmergencyWithdraw(msg.sender, _stakingToken, availableaAmount);
        user.amount = user.amount - availableaAmount;
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;
    }
```

**File:** rewards/MasterMagpie.sol (L583-599)
```text
    /// @notice calculate MGP reward based at current timestamp, for frontend only
    function _calMGPReward(address _stakingToken, address _user) internal view returns(uint256 pendingMGP) {
        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][_user];
        uint256 accMGPPerShare = pool.accMGPPerShare;
        uint256 lpSupply = _calLpSupply(_stakingToken);

        if (block.timestamp > pool.lastRewardTimestamp && lpSupply != 0) {
            uint256 multiplier = block.timestamp - pool.lastRewardTimestamp;
            uint256 mgpReward = (multiplier * mgpPerSec * pool.allocPoint) /
            totalAllocPoint;
            accMGPPerShare = accMGPPerShare + (mgpReward * 1e12) / lpSupply;
        }

        pendingMGP = (user.amount * accMGPPerShare) / 1e12 - user.rewardDebt;
        pendingMGP += unClaimedMgp[_stakingToken][_user];
    }
```

**File:** rewards/MasterMagpie.sol (L609-616)
```text
    /// @notice calculate MGP reward based on current accMGPPerShare
    function _calNewMGP(address _stakingToken, address _account) view internal returns(uint256) {
        UserInfo storage user = userInfo[_stakingToken][_account];
        uint256 pending = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) /
            1e12 -
            user.rewardDebt;
        return pending;
    }
```

**File:** rewards/MasterMagpie.sol (L659-667)
```text
    function _calLpSupply(address _stakingToken) internal view returns (uint256) {
        if (_stakingToken == address(vlmgp)) {
            return IERC20(address(vlmgp)).totalSupply();
        }
        if (_stakingToken == address(mWomSV)) {
            return IERC20(address(mWomSV)).totalSupply();
        }
        return IERC20(_stakingToken).balanceOf(address(this));
    }
```
