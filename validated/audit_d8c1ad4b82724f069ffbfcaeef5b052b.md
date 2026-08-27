### Title
Incentive reward shortfalls in ArbWomUp3 (and ArbWomUp/ArbWomUp2) are silently forfeited with no tracking or recovery mechanism when contract reward balance is insufficient - ([File: wombat/ArbWomUp3.sol])

### Summary
`ArbWomUp3.incentiveDeposit()` promises users a tiered MGP reward (locked via `vlMGP.lockFor`) proportional to their cumulative WOM/mWomSV deposit progress. When the contract's own MGP balance is insufficient to cover the computed reward, `getRewardAmount()` silently caps the payout to whatever balance is available, and the shortfall is never tracked anywhere, so it can never be recovered by the user later.

### Finding Description
In `wombat/ArbWomUp3.sol`, `getRewardAmount()` computes the tier-based reward and then caps it to the contract's own balance without recording the difference: [1](#0-0) 

The "already-paid" amount used to avoid double counting, `calDoubledCounted()`, is derived purely from the user's actual `mWomSV.getUserTotalLocked(_account)` balance — i.e., from how much WOM they have deposited/locked — not from how much reward was actually transferred to them in the past: [2](#0-1) 

Because `mWomSV`'s locked balance is fully updated by `_deposit()` regardless of whether the corresponding MGP reward payout was capped, any shortfall between the "entitled" tier reward and the actually-transferred (balance-capped) reward is **not represented in any state variable**. Unlike `ArbWomUp`/`ArbWomUp2`, which at least track `claimedReward[msg.sender]` (only incremented by the amount actually sent, meaning a shortfall implicitly resurfaces and can self-heal on the user's next deposit as long as the contract is later topped up), `ArbWomUp3` has no such bookkeeping at all — the tier progress used to gate future rewards is driven entirely by the locked WOM/mWomSV balance, which advances independently of whether the user was actually paid. [3](#0-2) 

This mirrors the referenced `TokenSender.send()` bug class: the contract checks its own balance, silently underpays when insufficient, and provides no mapping of amounts owed nor any claim function for users to recover the difference once the contract is refilled.

### Impact Explanation
Any user calling `incentiveDeposit()` when the contract's MGP balance is insufficient to cover their computed tier reward receives less MGP than they are entitled to, and that shortfall is permanently and silently written off — it cannot be recovered even if the contract is later topped up with MGP, because there is no tracking of the unpaid amount and the tier-progress accounting (based on `mWomSV.getUserTotalLocked`) does not "remember" the deficit. This constitutes a permanent, unrecoverable loss of promised/owed yield to an ordinary user, reachable purely through normal use of the public `incentiveDeposit()` function.

### Likelihood Explanation
This triggers under ordinary conditions whenever the contract's MGP balance runs low relative to demand (e.g., many users incentivizing/locking WOM around the same time, or the campaign nearing its funded allocation), which is a normal and expected operational state for a time-limited incentive/airdrop-style campaign contract. No privileged action, governance decision, or external protocol manipulation is required — a legitimate wallet simply calling `incentiveDeposit()` at the "wrong" time suffers the loss.

### Recommendation
Track any shortfall between the computed entitled reward and the amount actually transferred/locked (e.g., an `owedReward[account]` mapping updated whenever `mgpReward > mgpleft`), and expose a separate `claimOwedReward()` function that lets users pull their outstanding balance once the contract is refilled with MGP, mirroring the mitigation recommended for `TokenSender` in the referenced report. Apply the same fix consistently to `ArbWomUp.sol` and `ArbWomUp2.sol`, whose `claimedReward` self-healing only works if the user happens to deposit again later and is not a guaranteed recovery path.

### Proof of Concept
1. Owner funds `ArbWomUp3` with a limited amount of MGP and sets reward tiers via `setMultiplier()`.
2. Alice calls `incentiveDeposit(largeAmount, ratio, false, 2)`, whose tier-based reward computation (`getRewardAmount`) yields an MGP amount `X` larger than the contract's current MGP balance `Y` (`X > Y`). [1](#0-0) 
3. `getRewardAmount` returns `mgpleft = Y` instead of `X`; `incentiveDeposit` locks only `Y` MGP for Alice via `vlMGP.lockFor`. [4](#0-3) 
4. Alice's `mWomSV.getUserTotalLocked` has already increased by her full deposit, so `calDoubledCounted(Alice)` now reflects the full tier progress as if she had been paid `X`, even though she only received `Y`.
5. The owner later tops up the contract with more MGP (`adminWithdrawReward` is the only admin lever, and there is no top-up-and-claim flow). Alice has no function to call to claim the outstanding `X - Y`; if she deposits more WOM later, the new reward calculation still subtracts the full tier value already implied by her locked balance, so the `X - Y` deficit is never reissued. The shortfall is permanently lost.

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

**File:** wombat/ArbWomUp3.sol (L107-129)
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
