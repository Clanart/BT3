### Title
`ReferralStorage.claimReward()` allows draining unbacked MGP balance because `trigger()` only updates ledger bookkeeping without any matching token transfer - ([File: rewards/ReferralStorage.sol])

### Summary
`ReferralStorage.trigger()`, called by `MasterMagpie` every time any staker claims MGP rewards, only increments `refererInfo.rewardAmount` and `refereeInfo.rewardAmount` in storage — it never receives or moves any MGP tokens into `ReferralStorage`. `claimReward()` then calls `MGP.safeTransfer(msg.sender, userInfo.rewardAmount)` against `ReferralStorage`'s actual token balance, which has no guaranteed relationship to the sum of all `rewardAmount` ledger entries, making the referral pool permanently under-collateralized and turning claims into a race where earlier claimants drain funds owed to later ones.

### Finding Description
`MasterMagpie._multiClaim`/harvest flow calls `IReferralStorage(referral).trigger(_user, totalReward)` whenever any staker (referee) claims their MGP reward: [1](#0-0) . This is a fully permissionless, routine user action (stake → claim), not a privileged operation.

Inside `trigger()`, the referer's and referee's `rewardAmount` ledger fields are simply incremented based on a percentage of `_amount`, with no corresponding `IERC20(MGP).safeTransferFrom`/`transfer` bringing tokens into `ReferralStorage`: [2](#0-1) .

`claimReward()` then pays out strictly from whatever MGP balance `ReferralStorage` happens to hold, with no invariant check that `balanceOf(ReferralStorage) >= sum(rewardAmount)`: [3](#0-2) .

There is no `fund()`/`deposit()` function anywhere in `ReferralStorage.sol`, and no other code path in `MasterMagpie.sol` transfers MGP into the `ReferralStorage` address matching the amounts credited by `trigger()`. This means the ledger (`rewardAmount` across all users) can grow arbitrarily through normal staking/claiming activity while the actual token balance of the contract depends entirely on whatever was deposited externally (e.g., manually by an admin, out of scope to assume it always matches). Any unprivileged user who has referred others (or is self-referred via a second address they control) accrues `rewardAmount` through ordinary claim activity and can call `claimReward()` first — before other pending referrers/referees claim — to drain the entire available balance, leaving subsequent legitimate claimants with a revert (`ERC20: transfer amount exceeds balance`), i.e., their unclaimed yield is permanently inaccessible/frozen.

No modifier, reentrancy guard, or balance check in `claimReward()` prevents this; `nonReentrant`/`whenNotPaused` are inherited by the contract but not applied to `claimReward()` or `trigger()`, and even if they were, they would not fix the underlying backing-invariant break.

### Impact Explanation
This breaks solvency of the referral reward pool: the internal accounting (`sum of rewardAmount`) can exceed `MGP.balanceOf(ReferralStorage)`, so whichever claimant calls `claimReward()` first receives their full share while others are left with unclaimed/frozen yield or outright reverts. This matches the "protocol insolvency" / "theft or permanent freezing of unclaimed yield" Immunefi impact classes, since ordinary unprivileged usage of the staking/claim/referral flow (no admin action required) produces a state where honest referrers cannot recover funds they are legitimately owed.

### Likelihood Explanation
Highly feasible and requires no special capital or privilege: any two addresses (attacker controls both, or just uses `registerCode`/`useCode` normally) engaging in the standard stake → claim → `trigger()` flow accrues `rewardAmount` credits. Because there is no on-transfer funding tied to `trigger()`, this condition is present under ordinary operation, not just adversarial manipulation — the attacker's only advantage is racing to call `claimReward()` before others once the ledger exceeds the actual balance, which is trivial to front-run given `claimReward()` has no queue, cooldown, or pro-rata distribution logic.

### Recommendation
Tie `trigger()`'s bookkeeping to an actual, verifiable funding source: either have `MasterMagpie` transfer the corresponding MGP amount into `ReferralStorage` atomically within the same transaction as `trigger()` (e.g., `IERC20(mgp).safeTransfer(referral, totalReward * referralShare)` before calling `trigger`), or have `claimReward()` mint/pull from a reserve contract rather than relying on `ReferralStorage`'s own balance. Additionally, enforce an invariant check (e.g., `require(MGP.balanceOf(address(this)) >= userInfo.rewardAmount)`) and consider pro-rata scaling if the pool is genuinely underfunded, rather than first-come-first-served drainage.

### Proof of Concept
Foundry/Hardhat test plan:
1. Deploy `MasterMagpie`, `VLMGP`, `ReferralStorage`, and MGP token; fund `ReferralStorage` with a small MGP balance (e.g., 100 MGP) to simulate partial/no funding.
2. Set up referral: `userB.registerCode(code)`; `userA.useCode(code)` so `userA`'s referrer is `userB`.
3. Have `userA` stake and repeatedly claim MGP rewards through `MasterMagpie`, causing multiple `trigger(userA, amount)` calls that inflate `userB.rewardAmount` (referer) and `userA.rewardAmount` (referee) far beyond the 100 MGP actually held by `ReferralStorage`.
4. Assert `sum(userInfos[*].rewardAmount) > MGP.balanceOf(ReferralStorage)` — confirming the backing invariant is broken.
5. Call `userB.claimReward()` first; assert it succeeds and drains most/all of the 100 MGP.
6. Call `userA.claimReward()` afterward; assert it reverts with `ERC20: transfer amount exceeds balance` despite having a valid, non-zero `rewardAmount`, proving permanent loss of accrued yield for the later claimant.

### Citations

**File:** rewards/MasterMagpie.sol (L576-581)
```text
        uint256 totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount;

        if (totalReward > 0 && referral != address(0)) {
            IReferralStorage(referral).trigger(_user, totalReward);
        }
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
