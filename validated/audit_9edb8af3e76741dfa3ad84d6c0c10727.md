### Title
Rounding-down in `expectedPenaltyAmount`'s `unlockFactor` calculation causes users to receive less MGP on `forceUnLock` - (File: VLMGP.sol)

### Summary
`VLMGP.expectedPenaltyAmount` computes a time-based `unlockFactor` used to determine how much of a user's cooling-down MGP is returned versus forfeited as penalty when the user calls `forceUnLock`. The factor is computed by performing division before the squaring/multiplication step, causing avoidable precision loss that is squared, resulting in the user receiving a systematically smaller `amountToUser` (and thus paying a larger penalty) than the exact math would produce.

### Finding Description
In `VLMGP.sol`:
```solidity
uint256 unlockFactor = 1e12;
if((block.timestamp - slot.startTime) <= (slot.endTime - slot.startTime))
    unlockFactor = ((block.timestamp - slot.startTime) * 1e12 / (slot.endTime - slot.startTime)) ** 2 / 1e12;

uint256 unlockAmount = waitingAmount * unlockFactor / 1e12;
amontToUser = baseAmountToUser + unlockAmount;
penaltyAmount = coolDownAmount - amontToUser;
``` [1](#0-0) 

The intended calculation is `unlockFactor = (elapsed / duration)^2` scaled by `1e12`. The exact form should be `elapsed^2 * 1e12 / duration^2`. Instead, the code first computes `elapsed * 1e12 / duration` (an integer division that rounds down, discarding the fractional remainder), then squares that already-truncated intermediate result, and finally divides by `1e12` again. Squaring a rounded-down fraction compounds the precision loss (the error is roughly doubled relative to the true fractional value, and a second division by `1e12` further truncates it), so `unlockFactor` ends up strictly smaller than or equal to the mathematically correct value in virtually all non-trivial cases. This is the same bug class as the external report's `getClaimableFlux`: multiplication is carried out on the result of a division rather than division being deferred until after all multiplications, producing a rounded-down reward/return amount. [2](#0-1) 

Since `unlockAmount = waitingAmount * unlockFactor / 1e12` directly feeds `amontToUser`, and `amontToUser` is the amount transferred to the user in `forceUnLock`, any user calling `forceUnLock` receives strictly less MGP back than they are entitled to, while `penaltyAmount` (added to `totalPenalty`, later swept to `penaltyDestination`) is correspondingly inflated:
```solidity
function forceUnLock(uint256 _slotIndex) external whenNotPaused nonReentrant {
    ...
    (uint256 penaltyAmount, uint256 amountToUser) = expectedPenaltyAmount(_slotIndex);
    IERC20(MGP).safeTransfer(msg.sender, amountToUser);
    totalPenalty += penaltyAmount;
    ...
}
``` [3](#0-2) 

This is a genuinely reachable path for any ordinary wallet: a user locks MGP via `lock`/`lockFor`, starts the cooldown via `startUnlock`, and then calls `forceUnLock` before the cooldown ends — all fully permissionless, unprivileged user flows. [4](#0-3) [3](#0-2) 

Note the sibling `mWomSV.sol` contract, which implements the same vlMGP-style lock-slot mechanism, was inspected for the same pattern; its `getRewardablePercentWAD` correctly defers division (multiplies first, then divides), so it does not exhibit this rounding-down issue. [5](#0-4)  I could not locate an `expectedPenaltyAmount`-equivalent function in `mWomSV.sol` within the available index, so the analog is confined to `VLMGP.sol`.

### Impact Explanation
Every user who force-unlocks MGP during the cooldown period receives a permanently smaller amount of MGP than the protocol's own penalty schedule intends, and the difference is effectively lost/misallocated to the penalty pool. Because both the truncated `unlockFactor` and the final `unlockAmount` division floor the result, the shortfall is not recoverable by the user afterward — the underpaid difference is a permanent loss of otherwise-claimable/returnable MGP for the user, i.e., permanent freezing/forfeiture of funds that are not what the penalty formula intends to withhold. Given `forceUnLock` is a frequently used exit path (any time a user wants MGP before the cooldown ends), this systematically skims value from users on every such call.

### Likelihood Explanation
High likelihood: it triggers deterministically and automatically on every `forceUnLock` call whenever `(block.timestamp - slot.startTime) <= (slot.endTime - slot.startTime)` (i.e., essentially every valid force-unlock during cooldown), requires no special preconditions, and is reachable by any unprivileged wallet holding locked MGP.

### Recommendation
Avoid computing the fractional ratio before squaring it. Perform all multiplications before any division, e.g.:
```solidity
uint256 elapsed = block.timestamp - slot.startTime;
uint256 duration = slot.endTime - slot.startTime;
uint256 unlockFactor = 1e12;
if (elapsed <= duration) {
    unlockFactor = (elapsed * elapsed * 1e12) / (duration * duration);
}
uint256 unlockAmount = waitingAmount * unlockFactor / 1e12;
```
This preserves precision by squaring the un-truncated elapsed/duration values before any division, matching the mathematically intended `(elapsed/duration)^2`.

### Proof of Concept
Using representative values (durations comparable to a multi-day cooldown), the current formula:
```
unlockFactor_current = ((elapsed * 1e12) / duration) ** 2 / 1e12
```
loses precision twice: once when `elapsed*1e12/duration` truncates its fractional remainder, and again when the squared (already truncated) value is divided by `1e12`. Compare against the corrected formula:
```
unlockFactor_correct  = (elapsed * elapsed * 1e12) / (duration * duration)
```
For any `elapsed/duration` ratio that isn't an exact multiple that divides evenly (the overwhelming majority of real-world cooldown timestamps), `unlockFactor_current < unlockFactor_correct`, which directly reduces `unlockAmount` and thus `amontToUser` returned to the caller of `forceUnLock`, while inflating `penaltyAmount` retained by the protocol — mirroring the exact rounding-down-then-multiplying defect described in the external report for `getClaimableFlux`. [6](#0-5)

### Citations

**File:** VLMGP.sol (L234-248)
```text
    function expectedPenaltyAmount(uint256 _slotIndex) public view returns(uint256 penaltyAmount, uint256 amontToUser) {
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        uint256 coolDownAmount = slot.amountInCoolDown;
        uint256 baseAmountToUser = slot.amountInCoolDown / 5;
        uint256 waitingAmount = coolDownAmount - baseAmountToUser;

        uint256 unlockFactor = 1e12;
        if((block.timestamp - slot.startTime) <= (slot.endTime - slot.startTime))
            unlockFactor = ((block.timestamp - slot.startTime) * 1e12 / (slot.endTime - slot.startTime)) ** 2 / 1e12;

        uint256 unlockAmount = waitingAmount * unlockFactor / 1e12;
        amontToUser = baseAmountToUser + unlockAmount;
        penaltyAmount = coolDownAmount - amontToUser;
    }
```

**File:** VLMGP.sol (L275-311)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMPG();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        if (address(wombatBribeManager) != address(0) && 
            totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
            revert NotEnoughLockedMPG();

        address[] memory lps = new address[](1);
        address[][] memory vlMGPrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender);

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

        if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(msg.sender);

        emit UnlockStarts(msg.sender, block.timestamp, _amountToCoolDown);
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

**File:** wombat/mWomSV.sol (L181-206)
```text
    function getRewardablePercentWAD(address _user) override public view returns(uint256 percent) {
        uint256 fullyInLock = getUserTotalLocked(_user);
        uint256 inCoolDown = getUserAmountInCoolDown(_user);
        uint256 userTotalmWomSV = fullyInLock + inCoolDown;
        if (userTotalmWomSV == 0)
            return 0;
        percent = fullyInLock * 1e18 / userTotalmWomSV;

        uint256 timeNow = block.timestamp;
        UserUnlocking[] storage userUnlocking = userUnlockings[_user];

        for (uint256 i; i < userUnlocking.length; i++) {
            if (userUnlocking[i].amountInCoolDown > 0) {
                if (block.timestamp > userUnlocking[i].endTime) {// fully unlocked 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 * (userUnlocking[i].endTime - userUnlocking[i].startTime)
                        / userTotalmWomSV / (timeNow - userUnlocking[i].startTime);
                }
                else {// still in cool down 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 / userTotalmWomSV;
                }

            }
        }

        return percent;
    }
```
