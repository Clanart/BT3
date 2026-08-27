### Title
Uncapped sum of `basic + boostesd` percentages in `trigger()` allows referrer+referee reward credits to exceed the referee's actual reward `_amount` - ([File: rewards/ReferralStorage.sol])

### Finding Description
In `trigger()`, the referrer/referee reward split is computed as: [1](#0-0) 

`basic` is bounded to at most `DENOMINATOR` by the check in `setTier()`: [2](#0-1) 

but `boostesd`, returned by `_calBoosted()`, is only bounded by `BoostPoint`, not by `DENOMINATOR - basic`: [3](#0-2) 

`_calBoosted` computes `BoostPoint * userInfo.factor / totalBoostFactor`. Since `userInfo.factor <= totalBoostFactor` (as `totalBoostFactor` is the running sum of all registered users' factors, updated in `updateTotalFactor`), the maximum value `_calBoosted` can return is `BoostPoint` itself — reached when one user's factor dominates the total (e.g., they are the sole or overwhelmingly largest vlMGP locker who has registered a referral code).

`updateTotalFactor` is triggered by vlMGP lock/unlock actions: [4](#0-3) 

An attacker can `registerCode`, then lock a large amount of vlMGP (or unlock/relock) to make their `factor = sqrt(vlMGPLockedAmount)` dominate `totalBoostFactor`, especially early in the system's life when few other users have registered codes and locked vlMGP. In that case `boostesd → BoostPoint`.

If `basic + BoostPoint > DENOMINATOR` (a plausible parameter combination — e.g., a modest tier reward of 10% combined with a `BoostPoint` of 95%, both legitimate, non-misconfigured admin values used to incentivize top referrers), then `refererPercentage + refereePercentage > DENOMINATOR`, and thus:

`refererAmount + refereeAmount = _amount * (basic + boostesd) / DENOMINATOR > _amount`

This means the combined bookkeeping credit (`refererInfo.rewardAmount` + `refereeInfo.rewardAmount`) added on each `trigger()` call can exceed the actual `_amount` that was supposed to be split, over the referee's true accrual. There is no check anywhere in `trigger()` or `_calBoosted()` that caps `basic + boostesd <= DENOMINATOR`, unlike the individual tier-level cap enforced in `setTier`.

### Impact Explanation
Every `trigger()` call under these conditions credits more MGP (via `rewardAmount`, later paid out in `claimReward()`) than the referee's actual reward amount justifies. Repeated over many referee claims, this systematically over-credits `rewardAmount` balances that are eventually paid out via real `MGP.safeTransfer` in `claimReward()`: [5](#0-4) 

This can drain the `ReferralStorage` contract's MGP balance beyond what is backed by real referee accrual, causing insolvency of the referral reward pool and theft of unclaimed yield for other referrers/referees whose legitimate claims may later fail due to insufficient MGP balance.

### Likelihood Explanation
- Requires only normal, plausible admin configuration (a tier percentage and a `BoostPoint` whose sum can exceed `DENOMINATOR` — not an extreme/malicious misconfiguration like `setTier(1, 10000)`; even modest values like `basic=1000` and `BoostPoint=9500` suffice).
- Requires the attacker to be an unprivileged actor: call `registerCode`, lock vlMGP via `VLMGP`, and dominate `totalBoostFactor` — most feasible early in protocol life or with a large capital lock, which is within an ordinary user's reach (whales, or first mover).
- Repeatable on every `trigger()` call as long as the attacker retains a dominant factor share.

### Recommendation
Cap the combined percentage before splitting: 
```solidity
uint256 totalPercentage = basic + boostesd;
if (totalPercentage > DENOMINATOR) totalPercentage = DENOMINATOR;
```
and use `totalPercentage` in place of `basic + boostesd` for `refererPercentage`/`refereePercentage`, ensuring `refererAmount + refereeAmount <= _amount` always holds.

### Proof of Concept
Foundry test outline:
1. Deploy `ReferralStorage`, initialize with `BoostPoint = 9500`, `sharePercent` arbitrary (e.g., 5000).
2. Owner calls `setTier(1, 1000)` (10%, a reasonable, non-extreme tier value).
3. Attacker calls `registerCode(code)`.
4. Attacker locks a large amount of vlMGP in `VLMGP`, triggering `updateTotalFactor(attacker)`; ensure no other user has a comparable factor so `totalBoostFactor ≈ attacker.factor`.
5. A referee calls `useCode(code)` then accrues a reward and `masterMagpie` calls `trigger(referee, _amount)`.
6. Assert `refererAmount + refereeAmount > _amount` (query emitted `RefererRewardHarvested` / `RefereeRewardHarvested` amounts, or read `rewardAmount` deltas), confirming the over-credit.
7. Repeat `trigger()` calls and show cumulative `rewardAmount` credited across referrer+referee exceeds cumulative `_amount` forwarded by `masterMagpie`, demonstrating the deficit that would appear when both call `claimReward()`.

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

**File:** rewards/ReferralStorage.sol (L182-188)
```text
        uint256 basic = tiers[tierId].rewardPercentage;
        uint256 boostesd = _calBoosted(_referer);

        uint256 refererPercentage = (basic + boostesd) * (DENOMINATOR - sharePercent)  / DENOMINATOR;
        uint256 refereePercentage = (basic + boostesd) *  sharePercent / DENOMINATOR;
        uint256 refererAmount = _amount * refererPercentage / DENOMINATOR;
        uint256 refereeAmount = _amount * refereePercentage / DENOMINATOR;
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

**File:** rewards/ReferralStorage.sol (L224-231)
```text
    function setTier(uint256 _tierId, uint256 _rewardPercentage) external override onlyOwner {
        if (_rewardPercentage > DENOMINATOR) revert InvalidPercentage();

        Tier memory tier = tiers[_tierId];
        tier.rewardPercentage = _rewardPercentage;
        tiers[_tierId] = tier;
        emit SetTier(_tierId, _rewardPercentage);
    }
```

**File:** rewards/ReferralStorage.sol (L243-246)
```text
    function _calBoosted(address _account) private view returns(uint256) {
        if (totalBoostFactor == 0) return 0;
        return BoostPoint * userInfos[_account].factor / totalBoostFactor;
    }
```
