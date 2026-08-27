### Title
Reward double-counting reset via mWomSV lock-reduction inflates MGP rewards in `incentiveDeposit` - ([File: wombat/ArbWomUp3.sol])

### Finding Description
`incentiveDeposit(_amount, _convertRatio, _bullMode, _mode)` with `_mode == 2` computes `rewardToSend` via `getRewardAmount(_amount, msg.sender, true)` **before** performing the deposit/lock [1](#0-0) . Inside `getRewardAmount`, the reward tier calculation adds the new amount to the user's *current* `mWomSV.getUserTotalLocked(_account)` to get `accumulated`, computes the total tiered reward for that accumulated balance, and then subtracts `calDoubledCounted(_account)` — which is itself computed purely from the **live** `mWomSV.getUserTotalLocked(_account)` value, not from any persisted record of previously granted rewards [2](#0-1) .

This design assumes `mWomSV.getUserTotalLocked(_account)` is monotonically non-decreasing over time for a given user, so that `calDoubledCounted` always reflects "reward already paid for prior locked amount." However, `mWomSV` is a lock/unlock contract (`ILocker`), and a user can reduce their locked balance (via `startUnlock`/withdrawal-style flows) between two `incentiveDeposit` calls. Once `getUserTotalLocked` drops (e.g., to near zero), `calDoubledCounted` also drops correspondingly, even though the user already received vlMGP rewards for that higher tier in a prior call. The next `incentiveDeposit` call then computes `rewardAmount/DENOMINATOR - calDoubledCounted(_account)` against the deflated baseline, producing a `rewardToSend` far larger than what is actually still owed, and this excess MGP is locked to the attacker via `vlMGP.lockFor(rewardToSend, msg.sender)` at line 102 [3](#0-2) .

There is no persisted "cumulative WOM/mWom already rewarded" state per account (the leftover `bracketRewarded` mapping is explicitly marked "not in use") [4](#0-3) , so nothing prevents the tier-reward baseline from resetting whenever the user's live lock balance decreases. No modifier, reentrancy guard, or accounting check in `incentiveDeposit` addresses this, since `nonReentrant`/`whenNotPaused` only guard against reentrancy/pausing, not against the attacker legitimately calling `mWomSV`'s own unlock function between two separate top-level transactions [5](#0-4) .

### Impact Explanation
This is theft of unclaimed/incremental MGP incentive rewards from the `ArbWomUp3` contract's MGP balance. An attacker can repeatedly: (1) lock mWom via `incentiveDeposit(..., 2)` to cross a reward tier and receive vlMGP, (2) call `mWomSV`'s unlock/withdraw function to drop their tracked locked balance, and (3) call `incentiveDeposit` again with a small top-up amount, causing `calDoubledCounted` to under-report previously rewarded WOM and `getRewardAmount` to reissue tier rewards that were already paid. Repeating this drains the contract's MGP reward balance (bounded only by `mgpleft` check at line 127-128) beyond what the reward-tier schedule intends, at the expense of the protocol's incentive budget / other eligible participants.

### Likelihood Explanation
Preconditions are minimal and fully within attacker control: the attacker only needs to hold WOM to convert/lock and to be able to call the public, unprivileged functions `incentiveDeposit` and `mWomSV`'s own unlock/withdraw entrypoint — no special role is required. The attack is repeatable across multiple lock/unlock cycles as long as the contract still holds MGP to distribute, making it a straightforward, capital-light, repeatable exploit for anyone who already participates in the WOM-up incentive program.

### Recommendation
Do not derive the "already rewarded" baseline from the live, mutable `mWomSV.getUserTotalLocked(_account)`. Instead, persist a monotonically increasing per-account state (e.g., `cumulativeWomRewarded[_account]` or `highWaterMarkLocked[_account]`) that is updated only when a reward is actually paid, and use `max(currentLocked, highWaterMark)` (or the persisted cumulative-rewarded amount directly) as the basis for tier calculations in `calDoubledCounted`/`getRewardAmount`, so that reducing the live lock via unlock/withdraw can never lower the reward baseline.

### Proof of Concept
Foundry test outline:
1. Deploy/mocks: `ArbWomUp3`, `mWomSV` (real or minimal mock implementing `getUserTotalLocked`, `lockFor`, and an unlock/withdraw path that reduces `getUserTotalLocked`), `vlMGP`, `wom`/`mgp` tokens, `smartWomConvert` mock; configure `rewardTier`/`rewardMultiplier` via `setMultiplier` with at least 2 tiers; fund the contract with MGP.
2. Attacker calls `incentiveDeposit(A, ratio, false, 2)` where `A` crosses tier boundary `rewardTier[1]`; record `reward1` from the `VLMGPRewarded` event / `vlMGP.lockFor` amount, and record `L1 = mWomSV.getUserTotalLocked(attacker)` after the call.
3. Attacker calls `mWomSV.startUnlock`/withdraw to reduce `getUserTotalLocked(attacker)` to near 0; assert `calDoubledCounted(attacker)` now returns near 0 (previously it reflected `L1`'s tier reward).
4. Attacker calls `incentiveDeposit(A2, ratio, false, 2)` with a small `A2` that alone would not cross any tier from a zero baseline; capture `reward2`.
5. Assert `reward1 + reward2 > getRewardAmount` computed once for `A + A2` from a genuinely zero starting `mWomSV` balance (i.e., compute expected single-shot tiered reward for cumulative WOM `A+A2` and assert `reward1+reward2` exceeds it) — demonstrating a strictly greater cumulative vlMGP payout than the tier schedule intends for the same total locked/converted WOM.

### Citations

**File:** wombat/ArbWomUp3.sol (L47-47)
```text
    mapping(address => uint) public bracketRewarded;   // not in use
```

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
