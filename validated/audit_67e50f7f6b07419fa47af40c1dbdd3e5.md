### Title
`ArbWomUp3._deposit` mode==2 sweeps the contract's entire mWom balance instead of only this deposit's converted share, letting an attacker capture donated/residual mWom into their own `mWomSV` lock and inflate future `getUserTier`/reward payouts - ([File: wombat/ArbWomUp3.sol])

### Summary
In `_deposit`'s `_mode == 2` branch, the amount locked into `mWomSV` for `_account` is computed as `IERC20(mWom).balanceOf(address(this))` — the contract's total current mWom balance — rather than the amount actually produced by this specific deposit (`toDeposit` minted + the converter's output for `toSwap`). Any unswept mWom sitting in `ArbWomUp3` (from a donation or leftover from a prior interaction) is credited entirely to whichever caller next invokes `incentiveDeposit(..., 2)`, permanently inflating that caller's `mWomSV.getUserTotalLocked`, which feeds `getUserTier`, `calDoubledCounted`, and `getRewardAmount` for that user's future deposits.

### Finding Description
`_deposit` mode 2 logic: [1](#0-0) 

```
} else if (_mode == 2) {
    uint256 toDeposit = _amount / 2;
    uint256 toSwap = _amount - toDeposit;
    IERC20(wom).safeApprove(mWom, toDeposit);
    IMWom(mWom).deposit(toDeposit);
    IERC20(wom).safeApprove(smartWomConvert, toSwap);
    IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0);
    uint256 mWomBal = IERC20(mWom).balanceOf(address(this));
    IERC20(mWom).safeApprove(address(mWomSV), mWomBal);
    ILocker(mWomSV).lockFor(mWomBal, _account);
}
```

`SmartWomConvert.convert()` calls `_convertFor(..., msg.sender, 0)`, and since `_mode` passed is `0`, the converter transfers the resulting mWom straight back to `msg.sender` — i.e., `ArbWomUp3` itself: [2](#0-1) [3](#0-2) 

Because `mWomBal` is read as the *current total balance*, rather than tracked as a before/after delta of this call, any mWom balance already sitting in `ArbWomUp3` before this call (e.g., from a direct ERC20 donation, or from a prior mode-2 deposit that for any reason left a residue) is swept in along with the caller's own converted amount and locked entirely under `_account`. `mWomSV.lockFor` performs a plain `transferFrom(msg.sender=ArbWomUp3, ...)` of the full `_amount` passed in without validating provenance: [4](#0-3) , and `getUserTotalLocked` simply reflects whatever was locked via `masterMagpie.stakingInfo`: [5](#0-4) .

This inflated `getUserTotalLocked(_account)` is then used by:
- `getUserTier`, which determines the reward multiplier tier for future deposits: [6](#0-5) 
- `calDoubledCounted`, subtracted from `getRewardAmount`'s lock-mode reward computation: [7](#0-6) 
- `getRewardAmount(_amountToConvert, _account, true)`, which adds `_amountToConvert + mWomSV.getUserTotalLocked(_account)` to determine tier-weighted `mgpReward`: [8](#0-7) 

Note: the *current* transaction's `rewardToSend` is computed from `getRewardAmount` **before** `_deposit()` runs, using pre-existing `mWomSV.getUserTotalLocked`, so the same-call reward is not directly inflated. However, the inflated `getUserTotalLocked` persists on-chain and benefits **every subsequent** `incentiveDeposit(..., 2)` call by the same account, since `getRewardAmount`/`getUserTier`/`calDoubledCounted` all read the post-inflation locked balance. An attacker can self-exploit within the same block: first donate 1 wei (or more) of mWom directly to `ArbWomUp3`, call `incentiveDeposit(smallAmount, ratio, false, 2)` to sweep the donation into their own `mWomSV` lock, then immediately call `incentiveDeposit` again — this second call now sees a higher `mWomSV.getUserTotalLocked(attacker)`, pushing them into a higher reward tier and yielding a larger `vlMGP.lockFor` payout than their own converted WOM justifies. No modifier (`nonReentrant`, `whenNotPaused`) prevents this, since it's simply an accounting flaw — the function does not diff pre/post balances to isolate only this deposit's proceeds.

### Impact Explanation
This is a "Backing/Conservation" violation: `mWomSV` locked balance credited to a user should be 1:1 backed by that user's own converted WOM, not another party's residual or donated mWom. The scoped impact is theft of unclaimed yield — an attacker can inflate their tier standing in `getUserTier`/`calDoubledCounted` cheaply (potentially just 1 wei of dust, or self-donated mWom) and extract a disproportionately large `vlMGP` (locked MGP) reward via `getRewardAmount` on subsequent deposits, without matching qualifying WOM contribution equal to that reward tier.

### Likelihood Explanation
Feasibility is high for a self-triggered exploit: the attacker needs only to (1) acquire and transfer a small amount of mWom directly to `ArbWomUp3` (donation, no special permission required — mWom is a standard ERC20), and (2) call `incentiveDeposit` twice in sequence (or across blocks). No privileged role is required; this is achievable by any unprivileged EOA holding purchased/donated mWom. It is repeatable each time residual/donated mWom can be introduced into the contract, and the marginal cost (gas + the donated/dust amount) can be far smaller than the resulting extra `vlMGP` reward extracted over successive deposits, especially as `_account`'s tier crosses reward-tier thresholds in `rewardTier`/`rewardMultiplier`.

### Recommendation
Track the mWom amount produced strictly by this deposit's operations rather than reading the contract's total balance. E.g., snapshot `uint256 balBefore = IERC20(mWom).balanceOf(address(this))` immediately before the mode-2 branch's mint/convert calls, and after them compute `uint256 mWomBal = IERC20(mWom).balanceOf(address(this)) - balBefore` before approving/locking. Alternatively, have `SmartWomConvert.convert` return `obtainedmWomAmount` and sum it explicitly with the minted `toDeposit` amount (accounting for `IMWom.deposit`'s actual minted output too) instead of relying on `balanceOf`.

### Proof of Concept
Foundry test plan:
1. Deploy/mock `ArbWomUp3`, `mWom`, `mWomSV`, `SmartWomConvert`, `vlMGP`, and WOM token with realistic tier arrays (`rewardTier`, `rewardMultiplier`).
2. Fund attacker with WOM and a small amount of mWom (to simulate "dust"/donation).
3. Attacker directly transfers 1 mWom (or a small dust amount) to `ArbWomUp3` via `IERC20(mWom).transfer(address(ArbWomUp3), dustAmount)`.
4. Attacker calls `incentiveDeposit(smallAmount, ratio, false, 2)`.
5. Assert `mWomSV.getUserTotalLocked(attacker)` strictly exceeds `smallAmount/2 + convertedShare(smallAmount/2, ratio)` (i.e., it also includes `dustAmount`).
6. Attacker calls `incentiveDeposit(smallAmount2, ratio, false, 2)` again.
7. Assert the second call's `rewardToSend` (via `getRewardAmount`) and resulting `vlMGP` locked balance for attacker exceed what would be computed if `getUserTotalLocked` had not included the swept dust from step 4 (compare against a control scenario without the pre-donation).
8. Confirm total MGP paid out to the attacker across both calls exceeds the amount that would be justified by `smallAmount + smallAmount2` alone (excluding `dustAmount`), demonstrating yield theft proportional to `dustAmount`'s tier-crossing effect.

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

**File:** wombat/SmartWomConvert.sol (L121-123)
```text
    function convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, msg.sender, _mode);
    }
```

**File:** wombat/SmartWomConvert.sol (L209-217)
```text
        if (_mode == 1) {
            IERC20(mWom).safeApprove(masterMagpie, obtainedmWomAmount);
            IMasterMagpie(masterMagpie).depositFor(mWom, obtainedmWomAmount, _for);
        } else if (_mode == 2) {
            IERC20(mWom).safeApprove(address(mWomSV), obtainedmWomAmount);
            mWomSV.lockFor(obtainedmWomAmount, _for);
        } else {
            IERC20(mWom).safeTransfer(_for, obtainedmWomAmount);
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

**File:** wombat/mWomSV.sol (L236-240)
```text
    function lockFor(uint256 _amount, address _for) override external whenNotPaused nonReentrant {
        _lock(msg.sender, _for, _amount);

        emit NewLock(_for, block.timestamp, _amount);
    }
```
