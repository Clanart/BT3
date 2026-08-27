Confirmed: `libraries/PoolHelperFactoryLib.sol` is unrelated code (a factory library for deploying `WombatPoolHelper`) and has nothing to do with `ReferralStorage`, `useCode`, or `updateTotalFactor`. This is a file-scope mismatch, but the substantive question is about `ReferralStorage.sol`, which I've fully analyzed.

### Title
Multi-hop self-referral cycle bypasses `Circled()` check to farm referral rewards - (File: rewards/ReferralStorage.sol)

### Summary
`useCode()` only blocks a direct one-hop self-referral (`codeOwners[_code] == msg.sender`), but does not prevent a longer cycle (A→B→C→A). Three attacker-controlled wallets can register codes and refer one another in a closed loop, then each locks vlMGP and calls `claimReward()`, collecting referral rebates from the pre-funded referral pool for locking their own stake instead of onboarding real external users.

### Finding Description
`useCode()` at [1](#0-0)  checks `codeOwners[_code] == msg.sender` (direct self-referral) and `myReferer[msg.sender] != address(0)` (already has a referrer), but nothing stops A, B, and C from using each other's codes in a cycle: A registers code, B uses A's code and registers its own, C uses B's code and registers its own, A uses C's code. Each of A/B/C is now the referrer of the next, closing the loop, and no check anywhere (in `useCode`, `registerCode`, or `trigger`) rejects cyclic referral graphs. When each wallet locks vlMGP, `_lock()` in `VLMGP.sol` calls `IReferralStorage(referralStorage).updateTotalFactor(_for)` [2](#0-1) , which sets `userInfo.factor = sqrt(lockedAmount)` and updates `totalBoostFactor` [3](#0-2) . When each wallet subsequently claims its own staking reward via `MasterMagpie`, `trigger(_referee, totalReward)` is invoked [4](#0-3) , which credits `refererInfo.rewardAmount` and `refereeInfo.rewardAmount` as fractions of the referee's own real yield, based on `basic + _calBoosted(_referer)` [5](#0-4) .

### Impact Explanation
This lets A, B, C collect extra MGP referral rebates (funded from the `ReferralStorage` contract's pre-loaded MGP balance, paid out via `claimReward()` [6](#0-5) ) purely by referring each other, without bringing any genuine external referral volume — the exact self-dealing scenario `Circled()` was meant to prevent, just executed over 3 hops instead of 1. This drains the tier/BoostPoint referral reward pool (an "unclaimed yield" pool) to attacker-controlled wallets rather than genuine referrers/referees, matching the "theft of unclaimed yield" impact class. Note the magnitude is bounded by the same `basic + boosted` percentage caps that apply to any legitimate referral relationship — it does not create unbounded minting, it only lets self-dealing wallets capture rebates intended for organic referral activity.

### Likelihood Explanation
Fully reachable by unprivileged EOAs: `registerCode`, `useCode`, locking vlMGP, and `claimReward` are all public/external with no special role required. Capital needed is only the vlMGP the attacker would lock anyway; the cycle can be repeated indefinitely and requires only 3 wallets coordinated by the same attacker.

### Recommendation
When processing `useCode`, walk/track the referral chain (or maintain a "root referrer" pointer) and reject any `_code` whose owner's referral chain eventually leads back to `msg.sender`, not just a direct match. Alternatively, cap referral depth to 1 (no forwarding of referral incentives through indirect chains) so cycles cannot form at all.

### Proof of Concept
Foundry test:
1. Deploy `ReferralStorage`, `VLMGP`, `MasterMagpie`, fund `ReferralStorage` with MGP for reward payouts, set tier rebate percentage.
2. Wallets A, B, C each acquire and lock equal amounts of MGP into `vlMGP` (no referral yet) — baseline: record `claimReward`-equivalent payout for an independent staker D with the same locked amount and no referral code.
3. A calls `registerCode(codeA)`; B calls `useCode(codeA)` then `registerCode(codeB)`; C calls `useCode(codeB)` then `registerCode(codeC)`; A calls `useCode(codeC)`.
4. Each of A, B, C locks vlMGP (triggering `updateTotalFactor`), then triggers a `MasterMagpie` claim (triggering `trigger()`), then calls `ReferralStorage.claimReward()`.
5. Assert: `sum(A.claimed + B.claimed + C.claimed)` from `ReferralStorage` > `0` and strictly greater than the referral-derived reward D (independent staker, no referral) would receive (which is `0`), demonstrating self-dealing extraction from the referral pool that `Circled()` was designed to block.

### Citations

**File:** rewards/ReferralStorage.sol (L134-145)
```text
    function useCode(bytes32 _code) external {
        if (_code == bytes32(0)) revert InvalidCode();
        if (codeOwners[_code] == address(0)) revert InvalidCode();
        if (codeOwners[_code] == msg.sender) revert Circled();
        if (myReferer[msg.sender] != address(0)) revert HasReferral();
        
        userInfos[msg.sender].codeIUsed = _code;
        myReferer[msg.sender] = codeOwners[_code];
        myReferees[codeOwners[_code]].push(msg.sender);

        emit SetReferal(msg.sender, codeOwners[_code]);
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

**File:** rewards/ReferralStorage.sol (L172-195)
```text
    // should be called from masterMagpie upon referee claiming reward
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

**File:** rewards/MasterMagpie.sol (L576-580)
```text
        uint256 totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount;

        if (totalReward > 0 && referral != address(0)) {
            IReferralStorage(referral).trigger(_user, totalReward);
        }
```
