Confirmed: `setMultiplier` allows admin to set `rewardTier[0]` freely (owner-set, default config expected to start at 0), and `mWomSV.startUnlock` immediately reduces `getUserTotalLocked` (moves the amount to a cooldown slot which is excluded from `getUserTotalLocked`) without any delay — the cooldown only gates the final `unlock()` withdrawal, not the accounting change. This confirms the exploit path is fully reachable by an unprivileged EOA in two ordinary transactions.

### Title
Reward double-counting in `ArbWomUp3.incentiveDeposit`/`getRewardAmount` via `mWomSV.startUnlock` resetting the tier baseline - ([File: wombat/ArbWomUp3.sol])

### Summary
`ArbWomUp3.getRewardAmount`'s lock branch (mode 2) computes marginal MGP reward by taking a cumulative tier-based reward for `_amountToConvert + mWomSV.getUserTotalLocked(_account)` and subtracting `calDoubledCounted(_account)`, which is derived live from `mWomSV.getUserTotalLocked(_account)`. Because that value is not an append-only counter but a live, user-mutable balance (an unprivileged user can call `mWomSV.startUnlock` to instantly move locked mWom into a cooldown slot, which is excluded from `getUserTotalLocked`), an attacker can reset the "already rewarded" baseline downward between two `incentiveDeposit` calls and get re-rewarded for a WOM range they already collected MGP for.

### Finding Description
In `wombat/ArbWomUp3.sol`, `incentiveDeposit(_amount, _convertRatio, _bullMode, 2)` calls `getRewardAmount(_amount, msg.sender, true)`: [1](#0-0) 
```
getRewardAmount(..., true)` computes:
accumulated = _amountToConvert + mWomSV.getUserTotalLocked(_account)
rewardAmount = cumulative tier reward for accumulated
mgpReward = rewardAmount/DENOMINATOR - calDoubledCounted(_account)
``` [2](#0-1) 

`calDoubledCounted` itself reads `mWomSV.getUserTotalLocked(_account)` fresh at call time rather than referencing a persistent high-water-mark stored in `ArbWomUp3`'s own storage (unlike `ArbWomUp`/`ArbWomUp2`, which use a monotonic `userWOMDeposited`/`claimedReward` mapping local to the contract). This design assumes `mWomSV.getUserTotalLocked(_account)` only increases as the user locks more mWom through this contract.

That assumption is false: `mWomSV.startUnlock(_amountToCoolDown)` is a permissionless, unprivileged function that immediately moves `_amountToCoolDown` mWom into a cooldown slot: [3](#0-2) 
and `getUserTotalLocked` explicitly excludes amounts in cooldown: [4](#0-3) 
so calling `startUnlock` decreases `getUserTotalLocked(attacker)` instantly, with no waiting period for that state change (the cooldown only gates the later `unlock()` withdrawal).

Exploit flow:
1. Attacker calls `incentiveDeposit(amount1, ratio, false, 2)` with `getUserTotalLocked(attacker) == 0`. Reward is computed for the full `[0, amount1]` tier range and `~amount1` mWom is locked into `mWomSV` for the attacker via `_deposit`'s mode-2 branch, which locks the *entire* resulting mWom balance (not half) into `mWomSV`: [5](#0-4) 
2. Attacker immediately calls `mWomSV.startUnlock(amount1)`, moving the just-locked amount into cooldown. `getUserTotalLocked(attacker)` drops back toward 0 even though the tokens haven't left the vault yet.
3. Attacker calls `incentiveDeposit(amount2, ratio, false, 2)` again. `getRewardAmount` now sees a low `getUserTotalLocked`, so both `accumulated` and `calDoubledCounted`'s baseline are computed against the reduced value, causing the tier-based reward for a range that overlaps `[0, amount1]` (already paid) to be paid out again.

No modifier (`nonReentrant`, `whenNotPaused`) stops this because the two `incentiveDeposit` calls and the intervening `startUnlock` call are separate, non-reentrant transactions; the vulnerability is a logic/state-tracking flaw, not a reentrancy issue.

### Impact Explanation
This results in theft of unclaimed MGP/vlMGP incentive reserves held by `ArbWomUp3` (`IERC20(mgp).balanceOf(address(this))`), which are locked for the attacker via `vlMGP.lockFor`. Each repetition of steps 2–3 lets the attacker re-collect reward for previously-rewarded WOM tiers using only a small top-up deposit, draining the contract's reward budget disproportionately to net new stake contributed — a direct theft of unclaimed yield reserved for legitimate incentive distribution. This matches the Immunefi "theft of unclaimed yield" impact class.

### Likelihood Explanation
The attack requires only WOM tokens (which can be wash-traded/cycled) and two ordinary transactions plus one `mWomSV.startUnlock` call — no special privileges, no flash loan needed beyond capital for `amount1`/`amount2`, and it is fully repeatable (the attacker can keep cycling lock → startUnlock → deposit as long as the contract's MGP balance and available `mWomSV` unlock slots (`maxSlot`) permit, freeing slots via `unlock()`/`cancelUnlock()` once cooldown elapses or immediately via `cancelUnlock`). This makes it a low-cost, highly repeatable exploit for any unprivileged attacker.

### Recommendation
Replace the reliance on `mWomSV.getUserTotalLocked(_account)` as the "already rewarded" baseline in `ArbWomUp3.getRewardAmount`/`calDoubledCounted` with an append-only counter stored in `ArbWomUp3` itself (e.g., a `mapping(address => uint256) public totalRewardedWomIn` that only increases on `incentiveDeposit`, mirroring the pattern already used in `ArbWomUp`/`ArbWomUp2` with `userWOMDeposited`/`claimedReward`), so that reward computation is independent of the user's ability to move tokens into/out of `mWomSV` cooldown.

### Proof of Concept
Foundry test plan:
1. Deploy `ArbWomUp3`, `mWomSV`, `vlMGP`, mock `mWom`/`smartWomConvert`, set `rewardTier = [0, T1, T2]`, `rewardMultiplier = [m0, m1, m2]`, fund contract with a large MGP balance.
2. Attacker calls `incentiveDeposit(amount1, ratio, false, 2)` where `amount1` pushes them into tier 1 (`amount1` just above `T1`). Record `vlMGPBalance1 = vlMGP.getUserTotalLocked(attacker)` and `lockedAfter1 = mWomSV.getUserTotalLocked(attacker)`.
3. Attacker calls `mWomSV.startUnlock(lockedAfter1)` (or a large fraction of it) to move it into cooldown, then reads `mWomSV.getUserTotalLocked(attacker)` to confirm it dropped near 0.
4. Attacker calls `incentiveDeposit(amount2, ratio, false, 2)` with a small `amount2` (e.g., dust) and records `vlMGPBalance2 - vlMGPBalance1`.
5. Assert that `vlMGPBalance2` (cumulative vlMGP minted to attacker) exceeds `rewardMultiplier[finalTier] * (amount1 + amount2) / DENOMINATOR` (accounting for the mode-2 `*2` bonus), demonstrating reward paid for overlapping WOM ranges — i.e., cumulative payout is not bounded by conservation relative to total net WOM actually still locked in `mWomSV`.

### Citations

**File:** wombat/ArbWomUp3.sol (L88-105)
```text
    function incentiveDeposit(
        uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode // 1 stake, 2 lock
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        if (_amount == 0) return;
        
        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender, _mode == 2);

        // giving out 50% more bonus
        if (_mode == 2)
            rewardToSend = rewardToSend * 2;

        _deposit(msg.sender, _convertRatio, _amount, _mode);

        IERC20(mgp).safeApprove(address(vlMGP), rewardToSend);
        vlMGP.lockFor(rewardToSend, msg.sender);
        // _bullMGP(rewardToSend, _minMGPRec, msg.sender);
        emit VLMGPRewarded(msg.sender, 0, rewardToSend);
    }
```

**File:** wombat/ArbWomUp3.sol (L107-144)
```text
    function getRewardAmount(uint256 _amountToConvert, address _account, bool _lock) external view returns (uint256) {
        uint256 mgpReward = 0;

        if (!_lock) {
            mgpReward = _amountToConvert * rewardMultiplier[getUserTier(_account)] / DENOMINATOR;
        } else {
            uint256 accumulated = _amountToConvert + mWomSV.getUserTotalLocked(_account);
            uint256 rewardAmount = 0;
            uint256 i = 1;

            while (i < rewardTier.length && accumulated > rewardTier[i]) {
                rewardAmount +=
                    (rewardTier[i] - rewardTier[i - 1]) *
                    rewardMultiplier[i - 1];
                i++;
            }
            rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1];
            mgpReward = (rewardAmount / DENOMINATOR) - calDoubledCounted(_account);
        }

        uint256 mgpleft = IERC20(mgp).balanceOf(address(this));
        return mgpReward > mgpleft ? mgpleft : mgpReward;
    }

    function calDoubledCounted(address _account) public view returns (uint256) {
        uint256 accuIn1 = mWomSV.getUserTotalLocked(_account);
        uint256 rewardAmount = 0;
        uint256 i = 1;
        while (i < rewardTier.length && accuIn1 > rewardTier[i]) {
            rewardAmount +=
                (rewardTier[i] - rewardTier[i - 1]) *
                rewardMultiplier[i - 1];
            i++;
        }

        rewardAmount += (accuIn1 - rewardTier[i - 1]) * rewardMultiplier[i - 1];
        return rewardAmount / DENOMINATOR;
    }    
```

**File:** wombat/ArbWomUp3.sol (L189-204)
```text
        } else if (_mode == 2) {
            uint256 toDeposit = _amount / 2;
            uint256 toSwap = _amount - toDeposit;

            // 50% goes to deposit
            IERC20(wom).safeApprove(mWom, toDeposit);
            IMWom(mWom).deposit(toDeposit); 

            // 50% smart smart convert
            IERC20(wom).safeApprove(smartWomConvert, toSwap);
            IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0);

            uint256 mWomBal = IERC20(mWom).balanceOf(address(this));
            IERC20(mWom).safeApprove(address(mWomSV), mWomBal);
            ILocker(mWomSV).lockFor(mWomBal, _account);

```

**File:** wombat/mWomSV.sol (L113-117)
```text
    function getUserTotalLocked(address _user) override public view returns (uint256 _lockAmount) {
        // needs fixing
        (uint256 _amountInMasterMagpie, ) = IMasterMagpie(masterMagpie).stakingInfo(address(this), _user);
        _lockAmount = _amountInMasterMagpie - getUserAmountInCoolDown(_user);
    }
```

**File:** wombat/mWomSV.sol (L247-277)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMWOM();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        address[] memory lps = new address[](1);
        address[][] memory mWomSVrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, mWomSVrewards, msg.sender);

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
