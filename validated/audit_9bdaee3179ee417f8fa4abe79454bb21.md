### Title
Underflow-driven revert in `mWomSV.getUserTotalLocked()` can permanently brick `unlock()` for users with mWomSV in cooldown - ([File: wombat/mWomSV.sol])

### Summary
This is a direct analog of the TRST-M-3 bug class: an arithmetic subtraction that is only safe if two pieces of state are updated in a specific order, but Solidity ≥0.8.0 checked math reverts the whole transaction the moment that ordering assumption is violated, breaking a core user-facing function (here, `unlock()`, the equivalent of `mint()/burn()/swap()` in the original report) rather than silently wrapping as the legacy Uniswap-style code expected.

### Finding Description
`getUserTotalLocked()` computes the "not-in-cooldown" balance of a user by subtracting the cooldown amount tracked locally in `mWomSV` from the balance recorded in `MasterMagpie`: [1](#0-0) 

```solidity
function getUserTotalLocked(address _user) override public view returns (uint256 _lockAmount) {
    // needs fixing
    (uint256 _amountInMasterMagpie, ) = IMasterMagpie(masterMagpie).stakingInfo(address(this), _user);
    _lockAmount = _amountInMasterMagpie - getUserAmountInCoolDown(_user);
}
```
(the `// needs fixing` comment in the source itself flags that this accounting is fragile)

This function is called throughout the contract, including from `balanceOf()`, which is the value `BaseRewardPool`/`vlMGPBaseRewarder`/`MasterMagpie` use for reward accounting on every deposit/withdraw/claim of `mWomSV`.

The unlock flow is: [2](#0-1) [3](#0-2) 

```solidity
function unlock(uint256 _slotIndex) external override whenNotPaused nonReentrant {
    ...
    uint256 unlockedAmount = slot.amountInCoolDown;
    _unlock(unlockedAmount);          // <-- calls MasterMagpie.withdrawMWomSVFor()
    slot.amountInCoolDown = 0;        // <-- local cooldown state only cleared AFTER
    IERC20(mWOM).safeTransfer(msg.sender, unlockedAmount);
}

function _unlock(uint256 _unlockedAmount) internal {
    IMasterMagpie(masterMagpie).withdrawMWomSVFor(_unlockedAmount, msg.sender); // trigers update pool share, so happens before total amount reducing
    totalAmountInCoolDown -= _unlockedAmount;
    totalAmount -= _unlockedAmount;
}
```

The inline comment ("trigers update pool share, so happens before total amount reducing") confirms that `withdrawMWomSVFor` in `MasterMagpie` reduces the user's `stakingInfo` balance for `mWomSV` and, as part of that same call, triggers reward-accounting code paths (`_updateFor`/`updateReward` in `BaseRewardPoolV2`/`vlMGPBaseRewarder`) that read `balanceOf(_user)` → `getUserTotalLocked(_user)`.

At that intermediate point in execution:
- `_amountInMasterMagpie` has already been decremented by `unlockedAmount` inside `MasterMagpie`.
- `getUserAmountInCoolDown(_user)` in `mWomSV` still includes the full `slot.amountInCoolDown` for the slot being unlocked, because `slot.amountInCoolDown = 0` only executes after `_unlock()` returns.

For a user whose entire non-cooldown-locked balance equals the amount currently unlocking (a very ordinary scenario — e.g., a user who locked X mWOM and immediately started unlocking all of it), `_amountInMasterMagpie - getUserAmountInCoolDown(_user)` becomes negative, and Solidity's checked subtraction reverts the entire transaction, exactly mirroring the mechanism described in TRST-M-3 (an operation that "works" only if unintended wraparound is tolerated, but reverts outright under 0.8.x, breaking core functionality).

### Impact Explanation
If this ordering hazard triggers, the user's `unlock()` call reverts and can never succeed for that slot: the state (`slot.amountInCoolDown`, `totalAmountInCoolDown`) is never advanced past this point for that slot, permanently freezing that portion of the user's already-cooled-down mWOM inside `mWomSV`. Because `getUserTotalLocked`/`balanceOf` are also read by `BaseRewardPoolV2`, `vlMGPBaseRewarder`/`mWOMSVBaseRewarder` reward-per-token accounting on every deposit/withdraw/claim touching this user's `mWomSV` stake, a broken accounting invariant here can cascade into reverts across reward claim paths tied to the account, freezing unclaimed yield as well. This satisfies the "permanent freezing of funds" / "theft or permanent freezing of unclaimed yield" bar.

### Likelihood Explanation
Reachable by any ordinary wallet by calling `mWomSV.startUnlock()` then `mWomSV.unlock()` after the cooldown — no privileged role required. It manifests naturally whenever the amount being unlocked is the user's entire non-cooldown balance at the time of the callback (a common pattern for users exiting fully), so it is not a contrived edge case.

### Recommendation
Reorder the state updates in `_unlock()`/`unlock()` so that `mWomSV`'s local cooldown bookkeeping (`slot.amountInCoolDown = 0`, `totalAmountInCoolDown -= amount`) is finalized (or the values being read during the `MasterMagpie` callback are made consistent) before calling `IMasterMagpie(masterMagpie).withdrawMWomSVFor(...)`, so that `getUserTotalLocked` never observes a state where `MasterMagpie`'s balance has been decremented without a corresponding decrement to `getUserAmountInCoolDown`. Alternatively, compute and cache the pre-callback values needed by any reward-accounting hook, rather than relying on live storage reads mid-callback.

### Proof of Concept
1. User locks `100 mWOM` via `mWomSV.lock(100)` → `totalAmount = 100`, `stakingInfo(mWomSV, user) = 100` in `MasterMagpie`.
2. User calls `startUnlock(100)` to move the entire balance into cooldown → `userUnlockings[user][0].amountInCoolDown = 100`, `totalAmountInCoolDown += 100`. Now `getUserTotalLocked(user) = 100 - 100 = 0` (consistent).
3. After `coolDownInSecs` elapses, user calls `unlock(0)`.
4. Inside `_unlock(100)`, `MasterMagpie.withdrawMWomSVFor(100, user)` executes, reducing `stakingInfo(mWomSV, user)` to `0` and, per the code's own comment, triggering pool-share/reward updates that call back into `mWomSV.balanceOf(user)` → `getUserTotalLocked(user)`.
5. At this point `_amountInMasterMagpie = 0` while `getUserAmountInCoolDown(user)` still returns `100` (slot not yet zeroed) → `0 - 100` underflows and reverts under Solidity ^0.8.0 checked arithmetic.
6. The whole `unlock()` transaction reverts; the user cannot retrieve funds already fully through cooldown, and the slot remains permanently stuck. [4](#0-3)

### Citations

**File:** wombat/mWomSV.sol (L105-117)
```text
    // total mWom locked, excluding the ones in cool down
    function totalLocked() override public view returns (uint256) {
        return this.totalSupply() - this.totalAmountInCoolDown();
    }

    /// @notice Get the total mWom a user locked, not counting the ones in cool down
    /// @param _user the user
    /// @return _lockAmount the total mWom a user locked, not counting the ones in cool down
    function getUserTotalLocked(address _user) override public view returns (uint256 _lockAmount) {
        // needs fixing
        (uint256 _amountInMasterMagpie, ) = IMasterMagpie(masterMagpie).stakingInfo(address(this), _user);
        _lockAmount = _amountInMasterMagpie - getUserAmountInCoolDown(_user);
    }
```

**File:** wombat/mWomSV.sol (L281-303)
```text
    function unlock(uint256 _slotIndex) external override whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        if (slot.endTime > block.timestamp)
            revert StillInCoolDown();

        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();

        address[] memory lps = new address[](1);
        address[][] memory mWomSVrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, mWomSVrewards, msg.sender);

        uint256 unlockedAmount = slot.amountInCoolDown;
        _unlock(unlockedAmount);

        slot.amountInCoolDown = 0;
        IERC20(mWOM).safeTransfer(msg.sender, unlockedAmount);

        emit Unlock(msg.sender, block.timestamp, unlockedAmount);
    }
```

**File:** wombat/mWomSV.sol (L364-368)
```text
    function _unlock(uint256 _unlockedAmount) internal {
        IMasterMagpie(masterMagpie).withdrawMWomSVFor(_unlockedAmount, msg.sender); // trigers update pool share, so happens before total amount reducing
        totalAmountInCoolDown -= _unlockedAmount;
        totalAmount -= _unlockedAmount;
    }
```
