### Title
Flash-loan manipulable referral boost factor via `VLMGP._lock`/`startUnlock` → `ReferralStorage.updateTotalFactor` allows instant, unearned referral reward inflation - (File: VLMGP.sol / rewards/ReferralStorage.sol)

### Summary
`VLMGP._lock()` and `startUnlock()` call `IReferralStorage(referralStorage).updateTotalFactor(_for)` which recomputes `userInfo.factor = sqrt(getUserTotalLocked(_account))` using the *instantaneous* locked balance, with no time-weighting, checkpoint, or minimum holding period. Because `lock()` only requires `MGP.safeTransferFrom` (satisfiable with a flash loan) and immediately updates the factor synchronously in the same call, an attacker can momentarily inflate their referral `factor`/`totalBoostFactor` share for the exact instant that `ReferralStorage.trigger()` is invoked, then reverse the lock in the same transaction.

### Finding Description
`_lock()` [1](#0-0)  transfers MGP in, credits `totalAmount`, and immediately calls `updateTotalFactor(_for)`. `startUnlock()` [2](#0-1)  moves the amount to cooldown and also calls `updateTotalFactor`, which recalculates the factor down since `getUserTotalLocked` excludes cooldown amounts [3](#0-2) .

In `ReferralStorage.updateTotalFactor`, the factor is computed purely from the live `getUserTotalLocked` snapshot with no historical/time-weighted component: [4](#0-3) . This factor is read later, at whatever moment `trigger()` happens to run, via `_calBoosted`: [5](#0-4) , and used to compute `refererAmount`/`refereeAmount` bonuses that are additively credited to `rewardAmount` (redeemable via `claimReward()`, which transfers real MGP out of the contract): [6](#0-5) [7](#0-6) . `trigger()` is invoked by `MasterMagpie` whenever a referee claims their (genuinely, time-accrued) MGP reward: [8](#0-7) .

Exploit flow (all within one atomic transaction, since flash loans require atomicity):
1. Attacker registers a referral code for address `A` (`registerCode`) and has a second address `B` use that code (`useCode`), with `B` having genuinely accrued unclaimed MGP reward from prior real staking.
2. Attacker flash-borrows MGP and calls `VLMGP.lock(amount)` as `A`. This instantly sets `A`'s `factor = sqrt(getUserTotalLocked(A))` including the flash-loaned amount, and inflates `totalBoostFactor`, boosting `A`'s share of the referral bonus pool (`_calBoosted`).
3. Still in the same transaction, `B` claims via `MasterMagpie.multiclaimFor`, which calls `ReferralStorage.trigger(B, totalReward)`. This uses the currently-inflated `A.factor` to compute `refererAmount`, crediting `A`'s `rewardAmount` with a disproportionately large referral bonus that does not reflect any genuine or lasting lock.
4. Attacker calls `startUnlock(amount)` then `forceUnLock(slotIndex)` to immediately reclaim the MGP (accepting the early-unlock penalty) and repay the flash loan.
5. Attacker calls `ReferralStorage.claimReward()` to redeem the inflated `rewardAmount` in real MGP.

None of `lock`, `startUnlock`, `forceUnLock`, `multiclaimFor`, or `trigger` enforce a minimum holding duration or use a time-weighted/checkpointed factor; `nonReentrant`/`whenNotPaused` modifiers do not prevent this sequence since it is not reentrancy and the contract is not paused.

### Impact Explanation
This allows theft of unclaimed referral yield disproportionate to genuine locked duration: an unprivileged attacker can extract a larger share of the `ReferralStorage` MGP reward pool than they are entitled to, funded by MGP the protocol/admin allocated to the referral system, without ever bearing real, lasting locked-capital risk. This matches "theft of unclaimed yield/referral bonus" impact.

### Likelihood Explanation
Feasibility requires: (a) the attacker to control both a referrer address with a registered code and a referee address using that code, (b) the referee to have pre-existing genuine unclaimed MGP reward at claim time (bounding the absolute magnitude of the theft to the size of that pending claim and the referral tier/boost parameters), and (c) availability of an MGP flash loan (or equivalent large temporary capital) plus tolerance for the early-unlock penalty and flash-loan fee. The attack is repeatable per referee-claim event and does not require any privileged role, but the profit is capped by `BoostPoint`, `sharePercent`, and the referee's `totalReward`, so real-world profitability depends on those values versus the penalty/flash-loan cost.

### Recommendation
Decouple the referral boost factor from instantaneous lock balance. Use a time-weighted or checkpointed measure of locked amount (e.g., minimum lock duration before the factor counts, or an average-balance/veToken-style accounting), and/or snapshot `factor` at the time `useCode`/registration happens rather than recomputing on every `lock`/`startUnlock` call. Alternatively, require a cooldown or lock-duration threshold before newly locked MGP can affect `updateTotalFactor`.

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `MasterMagpie`, `ReferralStorage`, mock MGP, and a mock flash lender (or use a simple `flashLoan`-style helper transferring MGP then requiring repayment in the same call).
2. Set up: `B` stakes MGP into `MasterMagpie` normally and lets rewards accrue over many blocks (`vm.warp`) to build a nontrivial pending reward.
3. `A.registerCode(code)`; `B.useCode(code)`.
4. In a single external call (attacker contract):
   - Flash-borrow large MGP amount.
   - `vlMGP.lock(amount)` as `A` — assert `ReferralStorage.userInfos(A).factor` jumped up and `totalBoostFactor` increased.
   - `masterMagpie.multiclaimFor(..., B)` — assert `ReferralStorage.userInfos(A).rewardAmount` increased by an amount reflecting the inflated boosted percentage (compare against a baseline where `A` never locked flash-loaned funds).
   - `vlMGP.startUnlock(amount)` then `vlMGP.forceUnLock(slotIndex)` — repay flash loan.
5. Assert: transaction succeeds atomically, `A`'s `rewardAmount` reflects the temporarily boosted factor even though `A`'s genuine locked balance returns to (near) zero by the end of the transaction, and `A.claimReward()` succeeds in transferring real MGP out.
6. Compare against a control run where `A` never flash-loans (keeps a much smaller genuine lock) to quantify the extra `refererAmount` gained purely from the transient inflation.

### Citations

**File:** VLMGP.sol (L125-129)
```text
    function getUserTotalLocked(address _user) override public view returns (uint256 _lockAmount) {
        // needs fixing
        (uint256 _amountInMasterMagpie, ) = IMasterMagpie(masterMagpie).stakingInfo(address(this), _user);
        _lockAmount = _amountInMasterMagpie - getUserAmountInCoolDown(_user);
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

**File:** VLMGP.sol (L461-470)
```text
    function _lock(
        address spender,
        address _for,
        uint256 _amount
    ) internal {
        MGP.safeTransferFrom(spender, address(this), _amount);
        IMasterMagpie(masterMagpie).depositVlMGPFor(_amount, _for);
        totalAmount += _amount; // trigers update pool share, so happens after toal amount increase
        if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(_for);
    }
```

**File:** rewards/ReferralStorage.sol (L158-168)
```text
    function claimReward() external {
        UserInfo storage userInfo = userInfos[msg.sender];

        uint256 rewardAmount = userInfo.rewardAmount;
          if (rewardAmount == 0) revert InsufficientRewardBalance(); 

        MGP.safeTransfer(msg.sender, userInfo.rewardAmount);

        emit RewardClaimed(msg.sender, userInfo.rewardAmount);
        userInfo.rewardAmount = 0;
    }
```

**File:** rewards/ReferralStorage.sol (L173-195)
```text
    function trigger(address _referee, uint256 _amount) external _onlyMasterMagpie {
        UserInfo storage refereeInfo = userInfos[_referee];
        address _referer = myReferer[_referee];

        if (_referer == address(0))
            return;

        UserInfo storage refererInfo = userInfos[_referer];
        uint256 tierId = userInfos[_referer].tier;
        uint256 basic = tiers[tierId].rewardPercentage;
        uint256 boostesd = _calBoosted(_referer);

        uint256 refererPercentage = (basic + boostesd) * (DENOMINATOR - sharePercent)  / DENOMINATOR;
        uint256 refereePercentage = (basic + boostesd) *  sharePercent / DENOMINATOR;
        uint256 refererAmount = _amount * refererPercentage / DENOMINATOR;
        uint256 refereeAmount = _amount * refereePercentage / DENOMINATOR;

        refererInfo.rewardAmount += refererAmount;
        refereeInfo.rewardAmount += refereeAmount;

        emit RefererRewardHarvested(_referer, refererAmount);
        emit RefereeRewardHarvested(_referee, refereeAmount);
    }
```

**File:** rewards/ReferralStorage.sol (L197-206)
```text
    function updateTotalFactor(address _account) external override _onlyVlMGP {
        UserInfo storage userInfo = userInfos[_account];
        if (userInfo.myCode == bytes32(0)) return; // user did not activate referral feature
        
        totalBoostFactor -= userInfo.factor;
        uint256 vlMGPLockedAmoubnt = IVLMGP(vlMGP).getUserTotalLocked(_account);
        userInfo.factor = DSMath.sqrt(vlMGPLockedAmoubnt);

        totalBoostFactor += userInfo.factor;
    }
```

**File:** rewards/ReferralStorage.sol (L242-246)
```text
    // The boosted part is share among all vlMGP holders who created referral link.
    function _calBoosted(address _account) private view returns(uint256) {
        if (totalBoostFactor == 0) return 0;
        return BoostPoint * userInfos[_account].factor / totalBoostFactor;
    }
```

**File:** rewards/MasterMagpie.sol (L576-580)
```text
        uint256 totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount;

        if (totalReward > 0 && referral != address(0)) {
            IReferralStorage(referral).trigger(_user, totalReward);
        }
```
