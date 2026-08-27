### Title
Missing `nonReentrant` on `cancelUnlock` allows reentrant double-decrement of `totalAmountInCoolDown` during `forceUnLock`/`unlock`, desyncing `totalLocked()` from real backing - (File: VLMGP.sol)

### Summary
`forceUnLock` and `unlock` call `_unlock()`, which invokes the external call `IMasterMagpie(masterMagpie).withdrawVlMGPFor(...)` **before** decrementing `totalAmountInCoolDown`/`totalAmount`, and the calling function only zeroes `slot.amountInCoolDown` **after** `_unlock()` returns. Because `cancelUnlock` lacks the `nonReentrant` modifier, an attacker who can trigger a callback during that external call window can re-enter `cancelUnlock` on the same slot while it is still "in cooldown" from `cancelUnlock`'s point of view, causing `totalAmountInCoolDown` to be decremented twice for a single unit of actual backing while `totalAmount`/MasterMagpie stake is decremented only once. This permanently inflates `totalLocked()` (`totalSupply() - totalAmountInCoolDown()`) relative to real locked MGP.

### Finding Description
- `forceUnLock` (VLMGP.sol:352-367) requires the slot to still be in cooldown (`_checkInCoolDown` requires `slot.endTime > block.timestamp`), then calls `_unlock(slot.amountInCoolDown)`.
- `_unlock` (VLMGP.sol:455-459) first performs the external call `IMasterMagpie(masterMagpie).withdrawVlMGPFor(_unlockedAmount, msg.sender)`, and only afterwards decrements `totalAmountInCoolDown` and `totalAmount`.
- Back in `forceUnLock`, `slot.amountInCoolDown` is not zeroed until **after** `_unlock()` returns (VLMGP.sol:363), and `expectedPenaltyAmount` re-reads `slot.amountInCoolDown` from storage (VLMGP.sol:234-248).
- `cancelUnlock` (VLMGP.sol:339-349) has no `nonReentrant` modifier, and its guard `_checkInCoolDown` requires `slot.endTime > block.timestamp` — the exact same precondition state that still holds while `forceUnLock`'s `slot.amountInCoolDown` has not yet been zeroed and `totalAmountInCoolDown` has not yet been decremented.
- If the external call inside `_unlock()` (`withdrawVlMGPFor`, which is itself downstream of `MasterMagpie`'s internal accounting/reward logic) creates any reentrancy window back to attacker-controlled code, the attacker can call `cancelUnlock(_slotIndex)` on the *same slot* mid-call:
  - `totalAmountInCoolDown -= slot.amountInCoolDown` executes once inside the reentrant `cancelUnlock` call (decrement #1), and `slot.amountInCoolDown` is set to 0.
  - Control returns to the outer `_unlock()`, which unconditionally executes `totalAmountInCoolDown -= _unlockedAmount` using the amount captured **before** the external call was made (decrement #2), because Solidity evaluates `slot.amountInCoolDown` as the call argument prior to entering `_unlock`.
  - `totalAmount` and the MasterMagpie `stakingInfo` balance are only reduced once (correctly), but `totalAmountInCoolDown` is reduced twice for a single real withdrawal.
- Existing modifiers do not stop this: `forceUnLock` and `unlock` are `nonReentrant`, blocking reentry into themselves, but this does not protect `cancelUnlock`, which has no reentrancy guard at all and shares mutable state (`totalAmountInCoolDown`, `userUnlockings`) with the guarded functions.
- **Caveat / unverified assumption**: this path is contingent on the external call chain inside `MasterMagpie.withdrawVlMGPFor` (or the `multiclaimFor` call in `unlock`) actually being able to hand control back to attacker-controlled code (e.g., via a reward-token transfer with hooks, a wrapped-native withdrawal, or a downstream helper/rewarder call). I was not able to fully trace `MasterMagpie.sol`'s internal implementation within the available tool budget to confirm a concrete callback surface exists; this should be verified directly against `rewards/MasterMagpie.sol` before treating this as fully proven exploitable.

### Impact Explanation
If reentrancy is achievable, `totalAmountInCoolDown` becomes desynced (understated) relative to the true sum of `userUnlockings[*].amountInCoolDown`, while `totalAmount` remains correctly backed. Since `totalLocked() = totalSupply() - totalAmountInCoolDown()` (VLMGP.sol:118-120), an understated `totalAmountInCoolDown` causes `totalLocked()` to be **overstated** system-wide, for every consumer of that value, indefinitely. If `WombatBribeManager` uses `totalLocked()` (or values derived from it) as a global denominator/reference for vote-weight normalization, this could misprice systemic vote weight for all users simultaneously — matching the "governance voting result manipulation" impact class.

### Likelihood Explanation
The attacker needs only: (1) an existing unlock slot in cooldown, (2) the ability to trigger a reentrant call during the external call inside `_unlock()`/`multiclaimFor`. No privileged role is required — only a self-controlled EOA/contract and normal `lock`/`startUnlock` usage. However, feasibility strictly depends on whether any reachable code path inside `MasterMagpie.withdrawVlMGPFor` or the reward-claim flow actually calls out to attacker-controlled code (e.g., a reward token with transfer hooks) before returning — this was not confirmed in this review.

### Recommendation
Add `nonReentrant` to `cancelUnlock`, and restructure `_unlock()`/`forceUnLock()`/`unlock()` so that all storage updates (`slot.amountInCoolDown = 0`, `totalAmountInCoolDown -=`, `totalAmount -=`) happen strictly before any external call (checks-effects-interactions), rather than relying solely on function-level reentrancy guards.

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `MasterMagpie` (or a mock implementing `depositVlMGPFor`/`withdrawVlMGPFor`/`multiclaimFor` that performs a controllable external call to `msg.sender`, e.g., simulating a reward token transfer with a hook).
2. Attacker contract: `lock(X)`, then `startUnlock(X)` to create a slot still within `coolDownInSecs`.
3. Attacker calls `forceUnLock(slotIndex)`; inside the mocked `withdrawVlMGPFor` external call, have the attacker contract's fallback/hook re-enter `cancelUnlock(slotIndex)`.
4. Assert: after the outer `forceUnLock` completes, `totalAmountInCoolDown` has decreased by `2*X` while `sum(userUnlockings[attacker][*].amountInCoolDown)` and MasterMagpie `stakingInfo` deposit have decreased by only `X`.
5. Assert invariant violation: `totalAmountInCoolDown != sum(userUnlockings[*][*].amountInCoolDown)` and `totalLocked()` returns a value greater than the actual backing MGP balance/locked supply. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** VLMGP.sol (L109-120)
```text
    function totalSupply() public override view returns (uint256) {
        return totalAmount;
    }

    function balanceOf(address _user) public override view returns (uint256) {
        return getUserTotalLocked(_user) + getUserAmountInCoolDown(_user);
    }

    // total Mgp locked, excluding the ones in cool down
    function totalLocked() override public view returns (uint256) {
        return this.totalSupply() - this.totalAmountInCoolDown();
    }
```

**File:** VLMGP.sol (L339-349)
```text
    function cancelUnlock(uint256 _slotIndex) external override whenNotPaused {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        _checkInCoolDown(msg.sender, _slotIndex);

        totalAmountInCoolDown -= slot.amountInCoolDown; // reduce amount to cool down accordingly
        slot.amountInCoolDown = 0; // not in cool down anymore

        emit ReLock(msg.sender, _slotIndex, slot.amountInCoolDown);
    }
```

**File:** VLMGP.sol (L352-367)
```text
    function forceUnLock(uint256 _slotIndex) external whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];
        _checkInCoolDown(msg.sender, _slotIndex);

        _unlock(slot.amountInCoolDown);
        (uint256 penaltyAmount, uint256 amountToUser) = expectedPenaltyAmount(_slotIndex);

        IERC20(MGP).safeTransfer(msg.sender, amountToUser);
        totalPenalty += penaltyAmount;

        slot.amountInCoolDown = 0;
        slot.endTime = block.timestamp;

        emit ForceUnLock(msg.sender, _slotIndex, amountToUser, penaltyAmount);
    }
```

**File:** VLMGP.sol (L446-453)
```text
    function _checkInCoolDown(address _user, uint256 _slotIdx) internal view {
        UserUnlocking storage slot = userUnlockings[_user][_slotIdx];
        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();
            
        if(slot.endTime <= block.timestamp)
            revert NotInCoolDown();
    }
```

**File:** VLMGP.sol (L455-459)
```text
    function _unlock(uint256 _unlockedAmount) internal {
        IMasterMagpie(masterMagpie).withdrawVlMGPFor(_unlockedAmount, msg.sender); // trigers update pool share, so happens before total amount reducing
        totalAmountInCoolDown -= _unlockedAmount;
        totalAmount -= _unlockedAmount;
    }
```
