### Title
`trigger()` sums tier `basic` reward and `_calBoosted()` boost without capping against `DENOMINATOR`, allowing referral payouts to exceed 100% of the claimed amount - (File: rewards/ReferralStorage.sol)

### Summary
`ReferralStorage.trigger()` computes the referrer/referee split as `(basic + boostesd) * ... / DENOMINATOR`, where `basic` is capped only individually at `DENOMINATOR` by `setTier()`, and `boostesd` is a separate, unbounded additive term from `_calBoosted()`. Because the two terms are never checked as a combined value against `DENOMINATOR`, the total percentage paid out on a referee's claim can exceed 100%, over-crediting `rewardAmount` for both the referrer and the referee relative to `_amount`.

### Finding Description
`setTier()` only bounds each tier's `rewardPercentage` individually: [1](#0-0) 

`trigger()`, called by `MasterMagpie` (via `_onlyMasterMagpie`) on every referee claim, adds `basic` (the referrer's tier percentage) to `boostesd` (`_calBoosted(_referer)`) with no ceiling check against `DENOMINATOR` before using the sum to derive `refererPercentage`/`refereePercentage`: [2](#0-1) 

`_calBoosted()` returns `BoostPoint * userInfos[_account].factor / totalBoostFactor`: [3](#0-2) 

If the referrer is the only account that has ever registered a code (and thus the only contributor to `totalBoostFactor` via `updateTotalFactor`), then `factor == totalBoostFactor`, so `_calBoosted()` returns the full `BoostPoint` value, unreduced by any other participant's share. If the owner has set the referrer's tier `rewardPercentage` (via `setTier`) close to or at `DENOMINATOR` (a value the function explicitly permits, since 10000 ≤ `DENOMINATOR` passes the check), then `basic + boostesd` can exceed `DENOMINATOR`. Since `refererPercentage + refereePercentage == basic + boostesd` exactly (the `(DENOMINATOR - sharePercent)` and `sharePercent` factors sum to `DENOMINATOR` and cancel), the combined `refererAmount + refereeAmount` credited in `rewardAmount` mappings exceeds `_amount`, i.e., more than 100% of the referee's claimed amount is minted into claimable balances. No modifier or check in `trigger()`, `setTier()`, or `_calBoosted()` bounds the combined percentage.

### Impact Explanation
`rewardAmount` for both referrer and referee is over-credited beyond what the underlying claim (`_amount`) should back. Since `claimReward()` pays out `MGP.safeTransfer(msg.sender, userInfo.rewardAmount)` directly from the contract's MGP balance: [4](#0-3) 
this creates unbacked liabilities that can drain the MGP reward pool faster than it is funded, leading to insolvency where legitimate claimants cannot be paid — matching the "Critical - Protocol insolvency" impact class.

### Likelihood Explanation
The precondition (attacker being the sole/dominant registered code owner, or more generally any referrer whose tier percentage plus their boost share exceeds `DENOMINATOR`) is plausible during early bootstrapping of the referral program or via an attacker registering a code, self-locking vlMGP to build `factor`, and referring themselves-adjacent addresses. `trigger()` is invoked permissionlessly through the normal `multiclaimFor` reward-claim flow with no special privileges required beyond registering a code and having a referee claim rewards, so this is realistically repeatable and requires no admin/owner action beyond the initial (permitted) tier/`BoostPoint` configuration.

### Recommendation
Cap the combined percentage in `trigger()`, e.g.:
```solidity
uint256 combined = basic + boostesd;
if (combined > DENOMINATOR) combined = DENOMINATOR;
```
and use `combined` in place of `basic + boostesd` for both `refererPercentage` and `refereePercentage` calculations, ensuring the total payout never exceeds 100% of `_amount`.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `ReferralStorage`, `MasterMagpie` mock, and `VLMGP` mock; initialize with `_boostPoint` set to a nonzero value (e.g., 2000 = 20%).
2. As owner, call `setTier(1, 9000)` (90%), within the individually-allowed max.
3. Attacker registers a code via `registerCode()`, locks vlMGP to gain a nonzero `factor`, and calls (or has `VLMGP` call) `updateTotalFactor(attacker)` so `totalBoostFactor == attacker.factor` (attacker is the only registrant).
4. Referee calls `useCode(attacker's code)`.
5. Simulate `MasterMagpie.multiclaimFor` calling `trigger(referee, amount)` with `amount = 1000e18`.
6. Assert `_calBoosted(attacker) == BoostPoint` (2000, since factor/totalBoostFactor = 1).
7. Assert `refererInfo.rewardAmount + refereeInfo.rewardAmount > amount` (i.e., `(9000+2000)/10000 * amount = 1100e18 > 1000e18`), demonstrating over-100% payout and confirming the invariant "total percentage paid out on a claim must be bounded below 100%" is violated.

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
