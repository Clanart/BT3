### Title
Sybil-splitting a vlMGP lock across many addresses inflates `boosted()` share and drains the referral reward pool - (File: rewards/ReferralStorage.sol)

### Summary
`ReferralStorage.trigger()`, reachable from any address via the permissionless `MasterMagpie.multiclaimFor()`, computes a referrer's bonus percentage as `BoostPoint * userInfo.factor / totalBoostFactor`, where `factor = DSMath.sqrt(lockedAmount)`. Because `sqrt` is strictly concave, splitting a fixed amount of locked MGP across many self-controlled, code-registered addresses increases the *sum* of those addresses' factors relative to keeping it in one address, letting an attacker capture a disproportionate, ever-growing share of the shared `BoostPoint` pie at the expense of honest referrers and the shared reward pool.

### Finding Description
`updateTotalFactor()` sets `userInfo.factor = DSMath.sqrt(vlMGPLockedAmount)` and adds it into the global `totalBoostFactor` for any account that has registered a referral code (`myCode != 0`) [1](#0-0) . `_calBoosted()` then computes a referrer's boost as `BoostPoint * userInfos[_account].factor / totalBoostFactor` [2](#0-1) , and `trigger()` uses `basic + boostesd` to split `_amount` between referrer and referee [3](#0-2) .

Because `sqrt(a)+sqrt(b) > sqrt(a+b)` for `a,b>0`, an attacker who owns a fixed total of locked MGP `L` can register `N` separate addresses, lock `L/N` in each, and register a referral code for each. The combined factor of the attacker's addresses grows as `sqrt(N*L)`, i.e., proportional to `sqrt(N)`, while the rest of `totalBoostFactor` (from honest, non-split referrers) stays fixed. As `N` increases, the attacker's aggregate share of `totalBoostFactor` — and hence of the shared `BoostPoint` percentage baked into every `trigger()` call — approaches 100%, diluting every other referrer's `boosted()` value.

`trigger()` is reachable by any unprivileged address because `MasterMagpie.multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` has no access control beyond `whenNotPaused`, and lets a caller trigger a claim/harvest flow for an arbitrary `_account` [4](#0-3) . At the end of `_multiClaim`, if `totalReward > 0` and `referral != 0`, it unconditionally calls `IReferralStorage(referral).trigger(_user, totalReward)` [5](#0-4) . An attacker can therefore use wallets they control as "referees" (each referred via `useCode()` to one of the attacker's split codes) and repeatedly call `multiclaimFor` on those wallets to invoke `trigger()`, crediting inflated `refererInfo.rewardAmount`/`refereeInfo.rewardAmount` that are later withdrawn via `claimReward()`, transferring real MGP out of `ReferralStorage`'s balance [6](#0-5) .

No existing check (no per-account cap on code registrations, no minimum lock size, no anti-Sybil mechanism, no reconciliation between `totalBoostFactor` and a Sybil-resistant aggregate) prevents this; `HasReferral`/`Circled` only stop a single referee from having two referrers or self-referral with their own code, not from an attacker operating multiple independent referrer identities.

### Impact Explanation
The referral reward pot in `ReferralStorage` (funded by MGP transferred to the contract, paid out via `claimReward()`) is a shared, limited resource. By Sybil-splitting a lock, an attacker can capture a growing, unbounded share of the `BoostPoint` bonus applied on every referee claim, extracting disproportionately more MGP from that shared pool than an honest single-address referrer with the same capital would, while diluting/starving honest referrers' `boosted()` share. This matches "High - Theft of unclaimed yield" since it results in economic value being redirected from the intended distribution to the Sybil attacker.

### Likelihood Explanation
The attack requires only: locking MGP in vlMGP (an existing, legitimate capability), registering multiple referral codes across attacker-controlled addresses (`registerCode()` is permissionless and free besides gas), setting up self-referred "referee" wallets, and repeatedly calling the fully permissionless `MasterMagpie.multiclaimFor()`. It requires `sharePercent` to route a meaningful cut to the referee side but that is a normal admin-configured parameter, not a misconfiguration precondition unique to the exploit. No privileged role is needed, and the attack is repeatable and scales with the number of Sybil addresses used, bounded mainly by gas costs.

### Recommendation
Replace the per-address `sqrt(individual lock)` summation with a Sybil-resistant aggregation, e.g., compute `factor` from a single canonical identity's *total* locked amount across all addresses it controls (not feasible on-chain without identity linking), or switch the boost formula to one that is linear in locked amount (removing the concavity that rewards splitting), or cap the number of registered codes / minimum lock amount per code, or track `totalBoostFactor` off of `sqrt(sum of locked amounts)` rather than `sum of sqrt(locked amounts)`.

### Proof of Concept
1. Deploy/use test harness with `ReferralStorage`, `MasterMagpie`, `VLMGP` wired together as in tests.
2. Baseline: address `R` locks `L` MGP, calls `registerCode(codeR)`. Address `A` uses `codeR`, accrues a MasterMagpie claimable reward, calls `multiclaimFor` for `A` to trigger `trigger(A, amount)`. Record `refererInfo(R).rewardAmount` delta.
3. Sybil case: create `N` addresses `R_1..R_N`, each locks `L/N` MGP and registers its own code; create `N` referee addresses `A_1..A_N` each using one `R_i`'s code and each with an equivalent claimable reward `amount/N`. Call `multiclaimFor` for each `A_i`.
4. Assert: `sum(refererInfo(R_i).rewardAmount for i in 1..N) > refererInfo(R).rewardAmount` from step 2 for the same total locked capital `L` and same total triggered `amount`, demonstrating the boost-share inflation from splitting.
5. Assert `userInfos[R_i].factor == DSMath.sqrt(getUserTotalLocked(R_i))` individually holds (per-account invariant intact) while the aggregate economic share captured by the Sybil identity `{R_1..R_N}` exceeds the non-split baseline, confirming the exploitable divergence at the aggregate/economic level.

### Citations

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

**File:** rewards/MasterMagpie.sol (L412-417)
```text
    /// @notice Claims for each of the pools with specified rewards to claim for each pool
    function multiclaimFor(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, _account, _account, _rewardTokens);
    }
```

**File:** rewards/MasterMagpie.sol (L576-581)
```text
        uint256 totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount;

        if (totalReward > 0 && referral != address(0)) {
            IReferralStorage(referral).trigger(_user, totalReward);
        }
    }
```
