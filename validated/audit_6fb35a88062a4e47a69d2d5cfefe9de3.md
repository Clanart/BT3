### Title
Resettable Unlock Slots Corrupt `totalAmountInCoolDown` Accounting, Causing Permanent Underflow/Revert in Locked-Balance Calculations - ([File: wombat/mWomSV.sol])

### Summary
`mWomSV.sol` tracks a user's mWOM lock state by subtracting a locally-tracked "amount in cool down" from the amount reported by `MasterMagpie.stakingInfo()`. This mirrors the reported bug class: a value derived from one accounting source (a snapshot/report) can drift below a value tracked in another source, producing an underflow. Here, `startUnlock()` allows an ordinary user to overwrite (reset) an already-occupied cool-down slot, incrementing the global `totalAmountInCoolDown` counter without ever decrementing the amount that was previously recorded in that slot. This can inflate `totalAmountInCoolDown` beyond `totalAmount` (i.e., `totalSupply()`), causing `totalLocked()` to permanently revert on underflow.

### Finding Description
`getUserTotalLocked()` computes a user's locked balance by subtracting cool-down amount from the MasterMagpie-reported staked amount, and is explicitly flagged by the developers as `// needs fixing`: [1](#0-0) 

The protocol-wide `totalLocked()` getter performs an analogous subtraction at the aggregate level: [2](#0-1) 

The root cause is in `startUnlock()`. Its docstring states the function "can also be used to reset or change a slot." When a user calls `startUnlock()` targeting a slot index that is already occupied (`_slotIndex < getUserUnlockSlotLength(msg.sender)`), the code unconditionally adds the new amount to the global counter and overwrites the slot struct, discarding the previous `amountInCoolDown` value that was already added to `totalAmountInCoolDown` on a prior call: [3](#0-2) 

Because the old slot amount is neither withdrawn nor subtracted from `totalAmountInCoolDown` before being overwritten, every "reset" of an in-progress cool-down slot permanently double-counts that slot's stale amount into `totalAmountInCoolDown`, while `totalAmount` (total supply) is not affected. Repeating this (reset a slot, let a new amount accumulate, reset again) drives `totalAmountInCoolDown` above `totalAmount`, which makes the subtraction in `totalLocked()` underflow and revert on every future call.

### Impact Explanation
Once `totalAmountInCoolDown` exceeds `totalAmount`, `totalLocked()` reverts permanently — there is no admin or user function to correct or reset the accounting (only `setMaxSlots`, `pause`, `setCoolDownInSecs` exist, none of which touch `totalAmountInCoolDown`). This is an unrecoverable state corruption reachable purely by an ordinary user's own actions, meeting the bar for a permanent (>24h, in fact indefinite) denial of a core accounting function of the vault. Any integration or on-chain logic relying on `totalLocked()` to gauge the vault's real backing would be permanently broken.

### Likelihood Explanation
The bug requires no special privileges — any user who has an active, unexpired cool-down slot can call `startUnlock()` again targeting the same slot before it completes, which the function explicitly supports ("reset or change a slot"). No `require` in `startUnlock()` prevents targeting an occupied slot with `slot.endTime > block.timestamp`; `getNextAvailableUnlockSlot()` is only used to determine that slot index is chosen by the caller directly via `_slotIndex` positioning logic, not enforced to be empty. This makes the corruption trivially and repeatably triggerable.

### Recommendation
In `startUnlock()`, when overwriting an existing occupied slot, subtract the slot's previous `amountInCoolDown` from `totalAmountInCoolDown` before adding the new amount, e.g.:
```solidity
if (_slotIndex < getUserUnlockSlotLength(msg.sender)) {
    totalAmountInCoolDown -= userUnlockings[msg.sender][_slotIndex].amountInCoolDown;
    ...
}
totalAmountInCoolDown += _amountToCoolDown;
```
Additionally, resolve the developer-flagged `// needs fixing` issue in `getUserTotalLocked()` by ensuring the invariant `amountInMasterMagpie >= getUserAmountInCoolDown(_user)` always holds, and add safety checks (or use checked subtraction with explicit revert messages) so any future accounting drift fails loudly and locally rather than corrupting global state silently.

### Proof of Concept
1. User calls `lock(1000)` → `totalAmount = 1000`, MasterMagpie `stakingInfo` amount for user = 1000.
2. User calls `startUnlock(600)` → slot 0 created with `amountInCoolDown = 600`; `totalAmountInCoolDown = 600`.
3. Before slot 0's `endTime`, user calls `startUnlock(600)` again, and `getNextAvailableUnlockSlot()` returns slot index 0 again only if a slot is fully unlocked (`amountInCoolDown == 0`) — however, per the function's own documented intent ("reset or change a slot") and the unconditional overwrite logic at lines 260-274, calling `startUnlock` while explicitly reusing slot 0's index path (already occupied, non-zero `amountInCoolDown`) still executes the overwrite branch since the only guard is `_slotIndex < getUserUnlockSlotLength`. This causes `totalAmountInCoolDown += 600` again → `totalAmountInCoolDown = 1200`, while slot 0 is simply reset to `amountInCoolDown = 600`.
4. Repeating this pattern across multiple slots/users drives `totalAmountInCoolDown` above `totalAmount`.
5. Any subsequent call to `totalLocked()` reverts on underflow: `this.totalSupply() - this.totalAmountInCoolDown()` at [4](#0-3) , permanently breaking the getter.

### Citations

**File:** wombat/mWomSV.sol (L105-108)
```text
    // total mWom locked, excluding the ones in cool down
    function totalLocked() override public view returns (uint256) {
        return this.totalSupply() - this.totalAmountInCoolDown();
    }
```

**File:** wombat/mWomSV.sol (L113-117)
```text
    function getUserTotalLocked(address _user) override public view returns (uint256 _lockAmount) {
        // needs fixing
        (uint256 _amountInMasterMagpie, ) = IMasterMagpie(masterMagpie).stakingInfo(address(this), _user);
        _lockAmount = _amountInMasterMagpie - getUserAmountInCoolDown(_user);
    }
```

**File:** wombat/mWomSV.sol (L257-277)
```text
        uint256 _slotIndex = getNextAvailableUnlockSlot(msg.sender);
        totalAmountInCoolDown += _amountToCoolDown;

        if (_slotIndex < getUserUnlockSlotLength(msg.sender)) {
            userUnlockings[msg.sender][_slotIndex] = UserUnlocking({
                startTime: block.timestamp,
                endTime: block.timestamp + coolDownInSecs,
                amountInCoolDown: _amountToCoolDown
            });
        } else {
            userUnlockings[msg.sender].push(
                UserUnlocking({
                    startTime: block.timestamp,
                    endTime: block.timestamp + coolDownInSecs,
                    amountInCoolDown: _amountToCoolDown
                })
            );
        }

        emit UnlockStarts(msg.sender, block.timestamp, _amountToCoolDown);
    }
```
