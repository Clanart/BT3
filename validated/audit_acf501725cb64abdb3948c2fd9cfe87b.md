### Title
Missing `updatePool()` synchronization in `emergencyWithdraw` corrupts reward accounting for residual stakes - ([File: rewards/MasterMagpie.sol])

### Summary
`MasterMagpie.emergencyWithdraw` recomputes a user's `rewardDebt` against a **stale** `pool.accMGPPerShare` because, unlike every other state-mutating entry point in the contract (`_deposit`, `_withdraw`), it never calls `updatePool(_stakingToken)` first. This mirrors the root cause pattern in the referenced report: a mandatory state-synchronization step (there, "increment nonce"; here, "sync `accMGPPerShare` to the current timestamp") is omitted on a specific code path, leaving persistent on-chain accounting permanently out of sync for the affected user's remaining position.

### Finding Description
Every function in `MasterMagpie` that touches `UserInfo.rewardDebt` first calls `updatePool()` so that `pool.accMGPPerShare` reflects rewards accrued up to `block.timestamp` before the user's debt baseline is reset: [1](#0-0) 

`emergencyWithdraw`, however, skips this step entirely: [2](#0-1) 

A regular unprivileged user's `UserInfo.amount` can exceed `UserInfo.available` whenever a portion of their staked balance originates from a locked source (vlMGP/mWomSV), since `_deposit` only increments `available` for non-locked deposits: [3](#0-2) 

When such a user calls `emergencyWithdraw` (reachable by any ordinary wallet whenever the contract is paused — a normal, non-privileged, non-malicious-admin operating condition already built into the contract), only the `available` portion is transferred out, but the *entire remaining* `user.amount` is re-baselined using `pool.accMGPPerShare` frozen at `pool.lastRewardTimestamp`, instead of the value it would have after a proper `updatePool()` call:

```solidity
user.amount = user.amount - availableaAmount;
user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;
```

Because `rewardDebt` is set using an artificially low (stale) `accMGPPerShare`, the very next `updatePool()` call (triggered by any subsequent deposit/withdraw/harvest by any user of that pool) will retroactively credit this user with MGP rewards for the residual `user.amount` covering the entire elapsed pause/emergency period — a period during which no pending reward was legitimately owed to them for the withdrawn state transition (the emergency path is explicitly designed to forfeit pending rewards, not re-grant them for the remaining balance at a stale rate).

### Impact Explanation
This inflates one user's share of the fixed, protocol-wide `mgpPerSec` emission at the expense of all other stakers in the same pool, since `MasterMagpie`'s reward math is a shared per-second emission split by `accMGPPerShare`. The affected user extracts unclaimed yield that should have been distributed pro-rata to the rest of the pool — a direct theft-of-yield condition satisfying the "theft or permanent freezing of unclaimed yield" bar, reachable purely through normal `emergencyWithdraw` usage by an ordinary wallet holding a mixed vlMGP/LP position, with no admin collusion required (pausing is a routine operational state, not an attacker-controlled privilege).

### Likelihood Explanation
Any user who has both a locked (vlMGP/mWomSV) position and a directly-staked (available) position in the same `_stakingToken` pool can trigger this simply by calling `emergencyWithdraw` while the contract is paused — a state the protocol enters for routine maintenance/incident response, not an attacker-only precondition. No special timing or race condition is required.

### Recommendation
Call `updatePool(_stakingToken)` at the start of `emergencyWithdraw`, exactly as done in `_deposit`/`_withdraw`, before recomputing `rewardDebt`, so the residual `user.amount` is re-baselined against the current `accMGPPerShare` rather than a stale value.

### Proof of Concept
1. User deposits LP tokens normally into pool `X` (`available = amount = A`).
2. User also locks vlMGP, which increases `user.amount` for pool `X` to `A + L` via `depositVlMGPFor` while `available` stays at `A` (`_isVlmgp = true` path).
3. Time passes; `pool.accMGPPerShare` should increase but is only updated lazily via `updatePool()`.
4. Protocol is paused (routine operation). User calls `emergencyWithdraw(X)`.
5. `user.available` (`A`) is transferred out; `user.amount` becomes `L`; `rewardDebt` is set to `L * pool.accMGPPerShare(stale) / 1e12`.
6. Protocol unpauses; any interaction triggers `updatePool(X)`, bumping `accMGPPerShare` to reflect the entire elapsed period.
7. User's next harvest computes `pending = L * accMGPPerShare(new) / 1e12 - rewardDebt(stale)`, crediting them MGP for the full elapsed interval on their residual `L` balance — reward that was never legitimately accrued/allocated to them post-emergency-exit, extracted from the shared emission pool at other stakers' expense. [4](#0-3)

### Citations

**File:** rewards/MasterMagpie.sol (L372-396)
```text
    /// @notice Update reward variables of the given pool to be up-to-date.
    /// @param _stakingToken Staking token of the pool
    function updatePool(address _stakingToken) public whenNotPaused {
        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        if (block.timestamp <= pool.lastRewardTimestamp || totalAllocPoint == 0) {
            return;
        }
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

**File:** rewards/MasterMagpie.sol (L434-447)
```text
    /// @notice Withdraw all available tokens without caring about rewards. EMERGENCY ONLY. 
    ///         Locked Token can not be emergent withdraw.
    /// @param _stakingToken Staking token of the pool
    /// @dev withdrawFor of the rewarder with the third param at false is an emergency withdraw
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

**File:** rewards/MasterMagpie.sol (L481-505)
```text
    /// @notice internal function to deal with deposit staking token
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

        if (_amount > 0)
            if (!_isVlmgp)
                emit Deposit(_account, _stakingToken, _amount);
            else
                emit DepositNotAvailable(_account, _stakingToken, _amount);
    }
```
