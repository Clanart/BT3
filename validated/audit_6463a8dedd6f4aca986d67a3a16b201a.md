### Title
Reentrancy in `MasterMagpie.emergencyWithdraw()` allows reward accounting manipulation via stale `user.amount` - (File: rewards/MasterMagpie.sol)

### Summary
`MasterMagpie.emergencyWithdraw()` reproduces the same bug class as the Vyper `Crowdfund.refund()` fix: an external token transfer is executed before all of the caller's accounting state is fully updated, and — unlike every other state-changing entry point in the same contract (`deposit`, `withdraw`, `depositFor`, `withdrawFor`, `_multiClaim`) — this function carries **no `nonReentrant` guard**.

### Finding Description
`emergencyWithdraw` reads a user's `available` balance, zeroes it, sends `pool.stakingToken` to `msg.sender`, and only *afterwards* decrements `user.amount` and recomputes `user.rewardDebt`: [1](#0-0) 

```
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

Every comparable function in the contract (`withdraw`, `deposit`, `depositFor`, `withdrawFor`, `_multiClaim`) is protected by `nonReentrant`: [2](#0-1) 

But `emergencyWithdraw` has only `whenPaused`, with no reentrancy protection. During the external `safeTransfer` call, `user.amount` still holds the pre-withdrawal (stale, larger) value while `user.available` has already been zeroed. If the token used as `pool.stakingToken` for a given pool invokes recipient code on transfer (e.g. an ERC-777-style token, or any token with transfer hooks that could be registered for a pool by `registerPool`/`add`), the recipient can reenter `deposit()`, `multiclaimSpec()`, or other MGP-harvesting entry points for the same staking token while `user.amount` is still artificially inflated. Reward computations such as `_calNewMGP`/`_harvestMGP` (and `rewardDebt` bookkeeping) key off `user.amount`, so harvesting during this window lets the attacker extract MGP yield proportional to stake that has already been withdrawn — a direct theft of unclaimed protocol yield, mirroring the "external call before finishing state updates" flaw in the original `Crowdfund.refund()` bug.

### Impact Explanation
Exploiting this window lets an attacker claim MGP rewards computed against stake amounts that are simultaneously being withdrawn, effectively double-counting principal for reward purposes. This is a direct theft of unclaimed yield/rewards from the shared reward pool (paid for by other stakers/allocations), satisfying the "theft of unclaimed yield" impact bar.

### Likelihood Explanation
Likelihood is contingent on whether any registered `pool.stakingToken` can trigger callback logic to the withdrawing address on `transfer`/`safeTransfer` (e.g., ERC-777, or a future/alt-chain token with hooks). This is not verifiable purely from `MasterMagpie.sol`/`WombatStaking.sol` — the staking tokens observed in this repo (protocol-minted receipt tokens via `ERC20FactoryLib.createERC20`) appear to be standard OZ ERC20 without hooks, which would not by itself invoke recipient code. Regardless of token-hook dependency, the missing `nonReentrant` modifier and checks-effects-interactions violation is an objective code defect inconsistent with the rest of the contract's withdraw/deposit paths, and becomes directly exploitable the moment any hook-capable token is used as a staking token (or on chains/tokens where transfer semantics include callbacks).

### Recommendation
Add the `nonReentrant` modifier to `emergencyWithdraw()`, consistent with `withdraw`/`deposit`/`withdrawFor`/`depositFor`, and reorder the function so all state (`user.available`, `user.amount`, `user.rewardDebt`) is fully finalized before the external `safeTransfer` call (checks-effects-interactions).

### Proof of Concept
Conceptual (depends on a hook-capable `pool.stakingToken`):
1. User stakes `X` of `stakingToken` (a token with transfer hooks) into `MasterMagpie`, `user.amount = X`, `user.available = X`.
2. Protocol is paused; user calls `emergencyWithdraw(stakingToken)`.
3. Inside, `user.available` is set to 0, then `safeTransfer(msg.sender, X)` is invoked. Because `stakingToken` has a transfer hook, the user's contract is invoked mid-call, **before** `user.amount` is decremented.
4. In the reentrant callback, the user (or another allowed path) triggers a reward harvest/claim for the same pool; `user.amount` is still `X` (not yet reduced), so pending-MGP math (`user.amount * accMGPPerShare / 1e12 - user.rewardDebt`) computes rewards as if the stake were still fully present.
5. The outer call resumes and finishes decrementing `user.amount` to 0, but the inflated reward has already been paid out — the attacker has extracted yield they were no longer entitled to. [3](#0-2)

### Citations

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
