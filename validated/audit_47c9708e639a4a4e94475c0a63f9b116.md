### Title
`calDoubledCounted` recomputes already-rewarded amount from a live, user-lowerable `mWomSV.getUserTotalLocked` balance, enabling repeated tier-bracket rewards - (File: `wombat/ArbWomUp3.sol`)

### Summary
`ArbWomUp3.incentiveDeposit` (mode 2, "lock") computes the reward for a deposit by summing all tier brackets up to the user's total accumulated mWomSV balance and then subtracting `calDoubledCounted(_account)`, the amount attributable to brackets already paid. `calDoubledCounted` derives this "already paid" figure purely from `mWomSV.getUserTotalLocked(_account)` at call time, rather than from a stored, monotonic ledger. Because a user can instantly lower `getUserTotalLocked` by calling `mWomSV.startUnlock()` (which moves part of the balance into cooldown before any waiting period elapses), the subtracted amount shrinks and the same tier bracket can be paid out again on a subsequent small deposit.

### Finding Description
In `getRewardAmount` (lock path) and `calDoubledCounted`: [1](#0-0) [2](#0-1) 

Both functions call `mWomSV.getUserTotalLocked(_account)`, defined as: [3](#0-2) 

which subtracts `getUserAmountInCoolDown(_user)` — the sum of `amountInCoolDown` across the user's unlock slots — from the amount deposited in MasterMagpie. Crucially, `startUnlock` immediately increases `amountInCoolDown` for a slot the instant it is called, with no delay: [4](#0-3) 

So `getUserTotalLocked` drops the moment `startUnlock` is called, well before the cooldown period (`coolDownInSecs`) has elapsed and before the underlying mWom is actually withdrawn.

The contract even has an unused mapping that looks like it was intended to be the monotonic ledger but was abandoned: [5](#0-4) 

Exploit flow:
1. Attacker calls `incentiveDeposit(_amount1, ..., _bullMode, 2)` with `_amount1` large enough to cross several `rewardTier` boundaries. `getRewardAmount` computes `rewardAmount` for the full accumulated balance and subtracts `calDoubledCounted` (which is 0 pre-deposit), then doubles the reward (`rewardToSend * 2` for mode 2). This locks mWom into `mWomSV` via `lockFor`, raising `getUserTotalLocked(attacker)`.
2. Attacker calls `mWomSV.startUnlock(_amountToCoolDown)` directly (a public, unprivileged function on `mWomSV`), moving a chunk of the just-locked balance into cooldown. This instantly reduces `getUserTotalLocked(attacker)` — no time needs to pass, no penalty is applied at this step.
3. Attacker calls `incentiveDeposit` again with a small `_amount2` (mode 2). `accumulated = _amount2 + getUserTotalLocked(attacker)` (now artificially low), and `calDoubledCounted(attacker)` is also computed against the same lowered `getUserTotalLocked`. Because the "already rewarded" baseline shrank, the bracket-sum arithmetic re-credits reward for tiers that were already paid in step 1, producing `rewardToSend` largely disconnected from any genuinely new locked balance.
4. Repeat steps 2-3 across multiple slots (bounded by `maxSlot`) to keep re-extracting reward from previously-crossed tiers.

The only backstop is `mgpleft` capping (`mgpReward > mgpleft ? mgpleft : mgpReward`), which prevents insolvency of `mgp` token itself but does not prevent this attacker from draining the entire `mgp` reward reserve meant for all future legitimate depositors — i.e., `rewardToSend` distributed to the attacker no longer reconciles with the reward that a correctly-tracked, monotonic ledger would have permitted, and `IERC20(mgp).balanceOf(address(this))` can be driven down disproportionately by one attacker abusing tier recomputation.

`nonReentrant` and `whenNotPaused` on both `incentiveDeposit` and `startUnlock` do not address this because the two calls are sequential, not reentrant — this is a state-manipulation/live-recompute bug, not a reentrancy bug.

### Impact Explanation
The vlMGP reward pool (`mgp` tokens held by `ArbWomUp3`) is a shared resource meant to reward all qualifying depositors proportional to genuinely new locked balance. By exploiting the live-balance recomputation in `calDoubledCounted`, an attacker can repeatedly re-claim reward for tier brackets already paid, draining `mgp` balance intended for other/future legitimate users — a direct theft of pooled reward funds, matching Critical - Direct theft of user funds.

### Likelihood Explanation
No privileged role is required: `startUnlock` on `mWomSV` is a public, unprivileged function, and `incentiveDeposit` is externally callable by any EOA holding `wom`. The only precondition is that the attacker's deposit crosses multiple tier boundaries (`rewardTier`) in one call and that the reward pool (`mgpleft`) has sufficient balance to pay out — both realistic given `setMultiplier` configures multiple tiers and the contract is funded ahead of time for the airdrop program. The attack is repeatable up to `mWomSV.maxSlot` cooldown slots per cycle and scales with capital (`_amount`) the attacker is willing to cycle through mWom/mWomSV, which itself can be looped back since `startUnlock`+`cancelUnlock` do not require completing the actual unlock.

### Recommendation
Replace the live-balance-derived `calDoubledCounted` with a persistent, monotonically-increasing per-user ledger (e.g., actually use the existing `bracketRewarded` mapping) that records cumulative reward already paid and is only ever incremented, never recomputed from `mWomSV.getUserTotalLocked`. Reward for a new deposit should be `newTierRewardForCumulativeLockedIncludingHistoricalUnlocks - bracketRewarded[account]`, where the cumulative locked figure used for tier lookups must never decrease due to `startUnlock`/`cancelUnlock` cooldown movements (track high-water-mark locked amount separately, or track total ever deposited through this contract instead of relying on `mWomSV`'s current balance).

### Proof of Concept
Hardhat test plan:
1. Deploy `ArbWomUp3`, `mWomSV`, `mWom`, mock `mgp`, `vlMGP`, and a mock/stub MasterMagpie backing `mWomSV.stakingInfo`. Configure `rewardTier = [0, T1, T2, T3]` and matching `rewardMultiplier` via `setMultiplier`. Fund `ArbWomUp3` with a large `mgp` balance.
2. Attacker calls `incentiveDeposit(amount1, ratio, false, 2)` with `amount1` sized so `accumulated` crosses `T1` and `T2`. Record `rewardToSend1` (from `VLMGPRewarded` event) and `mgpBalanceAfter1 = mgp.balanceOf(ArbWomUp3)`.
3. Attacker calls `mWomSV.startUnlock(x)` directly, where `x` is chosen to drop `getUserTotalLocked(attacker)` back below `T2` (or `T1`), immediately (no `time.increase` needed since `getUserTotalLocked` subtracts cooldown amount instantly).
4. Assert `mWomSV.getUserTotalLocked(attacker)` decreased by `x` immediately after step 3, and `calDoubledCounted(attacker)` decreased accordingly.
5. Attacker calls `incentiveDeposit(amount2, ratio, false, 2)` with a small `amount2`. Record `rewardToSend2` and `mgpBalanceAfter2`.
6. Assert: `rewardToSend1 + rewardToSend2 > getRewardAmount` that would be computed for a single deposit of `amount1 + amount2` done atomically (i.e., total paid out exceeds what the tier schedule should allow for the attacker's actual net new locked balance), demonstrating double-counting.
7. Assert `mgpBalanceAfter2` dropped disproportionately relative to attacker's real net contribution, confirming `rewardToSend` diverges from a correctly reconciled `IERC20(mgp).balanceOf(address(this))` allocation and directly reduces funds available to other legitimate depositors.

### Citations

**File:** wombat/ArbWomUp3.sol (L47-47)
```text
    mapping(address => uint) public bracketRewarded;   // not in use
```

**File:** wombat/ArbWomUp3.sol (L112-129)
```text
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
```

**File:** wombat/ArbWomUp3.sol (L131-144)
```text
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
