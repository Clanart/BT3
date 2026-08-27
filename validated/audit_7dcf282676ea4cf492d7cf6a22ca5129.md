### Title
Non-bracketed, tier-boundary reward inflation in `incentiveDeposit`/`getRewardAmount` (mode 1) allows disproportionate MGP extraction from the fixed reward pool - (File: `wombat/ArbWomUp3.sol`)

### Summary
For `_mode == 1` (stake, no lock), `getRewardAmount` (line 111) multiplies the *entire* `_amountToConvert` by a single tier multiplier chosen solely from `getUserTier(_account)`, which reads only `mWomSV.getUserTotalLocked(_account)` — a persistent, externally-reusable balance completely decoupled from the amount the user is currently depositing or has historically deposited through `ArbWomUp3`. Unlike the `_lock == true` branch (lines 112-124), which correctly walks the tier brackets and blends multipliers across the accumulated amount, the non-lock branch applies the *top qualifying tier's rate to the whole lump sum* in one shot, with no bracket blending and no per-account cumulative-deposit tracking (the `userWOMDeposited` mechanism present in `ArbWomUp2.sol` was removed here).

### Finding Description
`getUserTier` (`wombat/ArbWomUp3.sol:146-155`) derives the tier purely from `mWomSV.getUserTotalLocked(_account)`. `mWomSV.lockFor(_amount, _for)` (`wombat/mWomSV.sol:236-240`) is a permissionless external function that transfers `mWOM` from `msg.sender` and credits the lock to any address `_for`. This lets an attacker with zero prior locked balance:

1. Acquire/convert a small amount of `mWom` (e.g., via `IMWom(mWom).deposit`) and call `mWomSV.lockFor(smallAmount, attacker)` directly (bypassing `ArbWomUp3` entirely) to cross into a higher `rewardTier[i]` threshold with minimal capital relative to the multiplier tier it unlocks.
2. Call `incentiveDeposit(largeAmount, ratio, false, 1)` on `ArbWomUp3`. `getRewardAmount` (line 111) then computes `mgpReward = largeAmount * rewardMultiplier[topTier] / DENOMINATOR`, applying the *top-tier rate to the entire large deposit*, even though only a tiny fraction of the attacker's total WOM contribution actually reached that tier threshold.

Contrast this with the `_lock == true` branch, where `accumulated = _amountToConvert + mWomSV.getUserTotalLocked(_account)` is walked bracket-by-bracket (lines 117-123) and `calDoubledCounted` (line 124) subtracts what was already rewarded for the pre-existing locked balance — this correctly prevents exactly this kind of lump-sum tier-jump overpayment. No equivalent bracket walk or double-count subtraction exists for the `!_lock` path, and there is no tracking of `_account`'s historical mode-1 deposits at all, so the same minimal `mWomSV` lock can be reused across unlimited, unbounded-size `incentiveDeposit(..., false, 1)` calls, each paid at the top qualified rate on the full new amount. [1](#0-0) [2](#0-1) [3](#0-2) 

`nonReentrant`/`whenNotPaused` modifiers do not mitigate this since it is not a reentrancy or pause bypass — it's a reward-formula/accounting defect reachable via a single unprivileged transaction sequence.

### Impact Explanation
`getRewardAmount`'s result is minted out as `vlMGP` locks funded from `ArbWomUp3`'s finite `mgp` balance (capped by `mgpleft`, line 127-128). By reaching a high tier with disproportionately small locked capital and then depositing arbitrarily large `_amount` in the non-lock mode, an attacker extracts a share of the MGP reward pool far exceeding what the documented tiered-multiplier schedule intends for their real contribution, at the direct expense of the shared, finite reward balance meant for other/future depositors (`adminWithdrawReward`/`mgpleft` cap confirms the pool is finite). This is a theft of unclaimed yield / protocol-fund conservation violation, scoped to the `mgp` balance held by `ArbWomUp3`.

### Likelihood Explanation
Feasible for any unprivileged attacker with capital roughly equal to the lowest tier threshold they wish to reach (a one-time, comparatively small `mWomSV` lock) plus the WOM they intend to convert at the inflated rate. `mWomSV.lockFor` is public/permissionless and requires no special role. The exploit is repeatable indefinitely as long as the `mgp` balance in `ArbWomUp3` is not exhausted, and does not require flash loans, reentrancy, or governance/admin access — only sequencing two ordinary transactions (a lock, then a deposit).

### Recommendation
Make the non-lock (`_mode == 1`) reward calculation consistent with the lock path: track a per-account cumulative "qualifying deposit" amount for mode-1 deposits (as `ArbWomUp2` did with `userWOMDeposited`) and compute the reward via the same bracket-walking algorithm used for `_lock == true`, subtracting previously rewarded brackets (via a `calDoubledCounted`-style function keyed on the mode-1 cumulative amount, not on the freely reusable `mWomSV.getUserTotalLocked`). Alternatively, base tier qualification only on the user's own cumulative contribution through `ArbWomUp3` rather than an externally manipulable/reusable `mWomSV` balance.

### Proof of Concept
Hardhat test outline:
1. Deploy `ArbWomUp3`, `mWomSV`, `mWom`, mock `MasterMagpie`, and configure `rewardTier = [0, T1, T2]`, `rewardMultiplier = [m0, m1, m2]` with `m2 >> m0` (e.g., `m0=100`, `m2=300` bps).
2. Fund `ArbWomUp3` with a large `mgp` balance.
3. As attacker: convert/acquire just over `T2` worth of `mWom`, call `mWomSV.lockFor(T2, attacker)` directly (bypassing `ArbWomUp3`).
4. Call `getUserTier(attacker)` and assert it returns the top tier index.
5. Call `incentiveDeposit(largeAmount, ratio, false, 1)` where `largeAmount >> T2` (e.g., `100 * T2`).
6. Assert `rewardToSend == largeAmount * m2 / DENOMINATOR` (i.e., paid entirely at top-tier rate) and that this exceeds the reward that a bracket-blended calculation (as used in the `_lock == true` branch) would have produced for the same total contribution — demonstrating the reward-per-WOM ratio exceeds the documented tiered schedule, draining `ArbWomUp3`'s `mgp` balance disproportionately relative to genuine tier-qualifying capital.

### Citations

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

**File:** wombat/ArbWomUp3.sol (L146-155)
```text
    function getUserTier(address _account) public view returns (uint256) {
        uint256 userMWOMSVBal = mWomSV.getUserTotalLocked(_account);
        for (uint256 i = tierLength - 1; i >= 1; i--) {
            if (userMWOMSVBal >= rewardTier[i]) {
                return i;
            }
        }

        return 0;
    }
```

**File:** wombat/mWomSV.sol (L236-240)
```text
    function lockFor(uint256 _amount, address _for) override external whenNotPaused nonReentrant {
        _lock(msg.sender, _for, _amount);

        emit NewLock(_for, block.timestamp, _amount);
    }
```
