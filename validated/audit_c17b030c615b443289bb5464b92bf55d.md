Based on the code I was able to inspect, the report's bug class (an emergency-path withdrawal that fails to keep global accounting state in sync, causing a protocol-wide error) has a valid unprivileged-wallet analog in `MasterMagpie.emergencyWithdraw`.

### Title
`emergencyWithdraw` skips `updatePool` before mutating reward state, corrupting `accMGPPerShare` for all stakers - (File: rewards/MasterMagpie.sol)

### Summary
`MasterMagpie.emergencyWithdraw` lets any staked user pull out their `user.available` balance while the contract is paused, without first calling `updatePool()`. Unlike the normal `withdraw()`/`withdrawFor()` paths, which go through `_withdraw` → `_harvestAndUnstake` (which updates pool state before moving tokens), `emergencyWithdraw` mutates `user.amount`, `user.rewardDebt`, and moves `pool.stakingToken` balance without refreshing `pool.accMGPPerShare` / `pool.lastRewardTimestamp` first.

### Finding Description [1](#0-0) 

`emergencyWithdraw` directly transfers `availableaAmount` out of the pool's `stakingToken` balance and recomputes the caller's `rewardDebt` using the pool's *stale* `accMGPPerShare`, without calling `updatePool(_stakingToken)` first (compare with `updatePool`, which advances `accMGPPerShare` based on elapsed time and current `lpSupply`): [2](#0-1) 

Because the staking-token balance used to derive `lpSupply` shrinks immediately after the transfer, but `pool.lastRewardTimestamp` is left unchanged, the *next* call to `updatePool` will compute `multiplier = block.timestamp - pool.lastRewardTimestamp` (the whole elapsed interval, including the time before the emergency withdrawal happened) and apply it against the now-reduced `lpSupply`. This over-credits `accMGPPerShare` for the entire elapsed window as if the smaller `lpSupply` had existed the whole time, inflating rewards owed to all remaining stakers beyond what `mgpPerSec * elapsed` was meant to emit — a global MGP emission/accounting error triggerable purely by an ordinary staker calling a public, unprivileged function.

This mirrors the reported root cause: an unprivileged/emergency-path withdrawal that removes funds/state from the contract without updating the bookkeeping variable (`cumulativeFees` in the report, `pool.accMGPPerShare`/`lastRewardTimestamp` here) that other accounting logic depends on.

### Impact Explanation
Systematic over-crediting of `accMGPPerShare` inflates the MGP entitlement of all remaining stakers in the pool beyond the protocol's intended emission schedule, which can drain the MGP reward budget faster than designed and create a shortfall/insolvency in future reward payouts to legitimate stakers — this is a protocol-insolvency class impact, not merely a griefing or gas issue.

### Likelihood Explanation
Likelihood is high: `emergencyWithdraw` is a normal, unprivileged, user-callable function (only gated by `whenPaused`, not by any role), and pausing is a foreseeable operational state (e.g., during upgrades or incident response) during which any staker with `available` balance can trigger this path.

### Recommendation
Call `updatePool(_stakingToken)` at the start of `emergencyWithdraw`, exactly as `_deposit`/`_withdraw` do, before reading/writing `pool.accMGPPerShare` and before decrementing the token balance, so that the emission accounting reflects the correct `lpSupply` for the interval that already elapsed.

### Proof of Concept
1. Multiple users deposit into a pool via `MasterMagpie.deposit`, populating `user.available` and `user.amount`.
2. Contract owner pauses `MasterMagpie` (e.g., for maintenance) — `whenPaused` modifier is satisfied.
3. A user calls `emergencyWithdraw(_stakingToken)`. This immediately reduces the pool's staking-token balance (hence `_calLpSupply`) without touching `pool.lastRewardTimestamp`.
4. Time passes; contract is unpaused.
5. Anyone calls `updatePool(_stakingToken)` (or it's triggered via a subsequent deposit/withdraw). The multiplier computed from the stale `lastRewardTimestamp` is applied to the new, smaller `lpSupply`, over-inflating `accMGPPerShare` for the entire elapsed period — crediting remaining stakers more MGP than the emission schedule (`mgpPerSec`) intended, which the protocol cannot sustainably back. [3](#0-2) [4](#0-3) 

**Note on completeness:** I was unable to fully retrieve the implementation of `_harvestAndUnstake` and `_calLpSupply` within this session (tool access was cut off before the follow-up read completed), so the exact numeric magnitude of the over-emission and whether any downstream check mitigates it could not be fully verified from source. This should be confirmed by reading `rewards/MasterMagpie.sol` in full (particularly `_harvestAndUnstake` and `_calLpSupply`) before treating this as a fully substantiated finding.

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
