### Title
Sybil-split vlMGP locks let an attacker inflate `totalBoostFactor` via the concavity of `sqrt()`, diluting/stealing other referrers' BoostPoint-derived MGP referral yield - (File: rewards/ReferralStorage.sol)

### Summary
`ReferralStorage.registerCode` has no cost, sybil-resistance, or minimum-lock requirement, and `updateTotalFactor` computes each account's boost `factor` as `sqrt(lockedAmount)` per address rather than `sqrt(totalLockedByController)`. Because `sqrt()` is concave, splitting one locked position of size `X` across `N` sybil accounts each locking `X/N` produces `sum(sqrt(X/N)) = sqrt(N)*sqrt(X) > sqrt(X)`, letting an attacker inflate their aggregate contribution to the shared `totalBoostFactor` denominator without adding real capital, which increases their own `boosted%` share and simultaneously lowers every other real referrer's `boosted%` (since `_calBoosted = BoostPoint * factor / totalBoostFactor` for everyone shares the same denominator).

### Finding Description
`registerCode(bytes32 _code)` [1](#0-0)  is permissionless, free, and requires no vlMGP balance - any EOA can register an arbitrary number of unique codes.

`updateTotalFactor(address _account)`, invoked by the `vlMGP` contract on lock/relock events, sets `userInfo.factor = DSMath.sqrt(vlMGPLockedAmount)` for that individual account and folds it into the *global* `totalBoostFactor`: [2](#0-1) 

`_calBoosted` then computes every referrer's boost bonus as `BoostPoint * factor / totalBoostFactor`, i.e., each referrer's factor is measured against the same shared denominator: [3](#0-2) 

and `trigger()` (called by `masterMagpie` on referee reward events) uses this per-referrer `boosted` value to size the MGP percentage bonus paid to that referrer (and referee) out of `_amount`: [4](#0-3) 

Exploit flow:
1. Attacker deploys N sybil EOAs.
2. Each sybil calls `registerCode` with a unique code (free, no capital, no admin rights needed).
3. Attacker locks `X` total vlMGP split evenly (`X/N` each) across the sybils instead of locking `X` from one account.
4. Each lock triggers `updateTotalFactor`, and per-account factor `sqrt(X/N)` is summed N times into `totalBoostFactor`, yielding `sqrt(N)*sqrt(X)` — strictly larger than the `sqrt(X)` a single honest account with the same total capital would contribute.
5. Since `f(m) = m/(T+m)` (share of a fixed `BoostPoint` scaling term) is monotonically increasing in the attacker's own factor contribution `m`, the attacker's aggregate share of `BoostPoint` rises above what an honest single-account locker with identical capital would get, while `totalBoostFactor`'s growth simultaneously depresses every other real referrer's `factor/totalBoostFactor` ratio, lowering their `boosted` bonus and therefore the MGP amount they receive per `trigger()` call.

No existing check (no minimum lock size, no per-address KYC/dedup, no fee on `registerCode`, no cap on number of codes per controller) prevents this. `_onlyVlMGP`/`_onlyMasterMagpie` modifiers only gate *who* can call `updateTotalFactor`/`trigger`, not the sybil-splitting of capital across addresses that call `registerCode` freely beforehand.

### Impact Explanation
This is a violation of the reward-conservation invariant: reward share should scale with real qualifying stake, not with the number of controlled accounts. The concrete effect is a redistribution of the fixed `BoostPoint` scaling factor away from honest referrers toward the sybil attacker, reducing the MGP `refererAmount`/`refereeAmount` computed for legitimate referrers on every `trigger()` call after the sybil inflates `totalBoostFactor`. This matches the "theft of unclaimed yield" impact class (Immunefi: theft of unclaimed yield), since real referrers' future accrued `rewardAmount` is permanently reduced by the artificially inflated denominator, and the attacker's own MGP referral share is inflated beyond what their real economic stake should earn.

### Likelihood Explanation
Extremely feasible: `registerCode` costs only gas, and splitting an existing vlMGP position across N wallets requires no additional capital versus locking as one account (the same `X` total vlMGP is used, just distributed). The only requirement is the attacker's referral code(s) be used by referees so `trigger()` fires with a nonzero `boostesd`, which is a normal usage pattern already required for any referrer to earn rewards. The attack is fully repeatable and scales with N (more sybils → more dilution of other referrers, more inflation of attacker's own factor sum) with negligible additional cost (gas only).

### Recommendation
Compute the boost factor from a per-controller aggregated lock amount instead of a raw per-address value, e.g., track a canonical "beneficiary" via an off-chain-verified identity, or require `registerCode`/lock-linking to be tied to a single non-transferable identity, or change the factor formula to be linear (`factor = lockedAmount`) rather than `sqrt`, since a linear factor is not exploitable by splitting (`sum(X/N) == X`). If the sqrt boost curve is a deliberate design choice to diminish whale dominance, add a minimum-lock threshold and/or Sybil-resistance (e.g., require KYC/whitelist, or bind `registerCode` eligibility to accounts holding a non-forkable/non-duplicable credential) so that splitting capital across addresses provides no scoring advantage.

### Proof of Concept
Foundry test plan:
1. Deploy `ReferralStorage`, mock `vlMGP` (implementing `getUserTotalLocked` and calling `updateTotalFactor` on lock), and mock MGP token.
2. Scenario A (single account): Account `A` calls `registerCode(codeA)`, then locks `X` vlMGP. Assert `userInfos[A].factor == sqrt(X)` and record `totalBoostFactor_A`.
3. Reset state. Scenario B (10 sybils): Deploy sybils `S1..S10`, each calls `registerCode` with unique code, each locks `X/10` vlMGP. Assert `sum(userInfos[Si].factor) == 10 * sqrt(X/10) ≈ sqrt(10) * sqrt(X)`, and compare against Scenario A's single factor `sqrt(X)`.
4. For both scenarios, simulate an identical referee `trigger(referee, amount)` call routed to each referrer/sybil's code and sum `_calBoosted()`-derived `refererAmount` across all sybils vs. the single account.
5. Assert: `sum(sybil refererAmounts) - single_account_refererAmount > acceptable_rounding_error`, proving the sybil split extracts strictly more MGP-bonus share for the same total locked capital, at the expense of any other referrer sharing the same `totalBoostFactor` denominator (add a third, honest referrer `H` with fixed lock and show their `_calBoosted(H)` value decreases between Scenario A and B due to the larger `totalBoostFactor`).

### Citations

**File:** rewards/ReferralStorage.sol (L147-156)
```text
    function registerCode(bytes32 _code) external {
        if (_code == bytes32(0)) revert InvalidCode();
        if (codeOwners[_code] != address(0)) revert CodeOccupied();

        codeOwners[_code] = msg.sender;
        userInfos[msg.sender].myCode = _code;
        userInfos[msg.sender].tier = 1; // tier 1 as default

        emit RegisterCode(msg.sender, _code);
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

**File:** rewards/ReferralStorage.sol (L242-246)
```text
    // The boosted part is share among all vlMGP holders who created referral link.
    function _calBoosted(address _account) private view returns(uint256) {
        if (totalBoostFactor == 0) return 0;
        return BoostPoint * userInfos[_account].factor / totalBoostFactor;
    }
```
