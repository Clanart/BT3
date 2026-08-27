Based on my review, the reentrancy bug class from the report (state updated **after** an external token transfer, with no reentrancy guard) has a direct analog in `ReferralStorage.sol`.

### Title
Reentrancy in `ReferralStorage.claimReward` allows draining of accrued MGP referral rewards - (File: rewards/ReferralStorage.sol)

### Summary
`claimReward()` in `ReferralStorage.sol` transfers the caller's accrued MGP reward and only zeroes the internal accounting *after* the external `safeTransfer` call, with no reentrancy guard on the function. This is the same checks-effects-interaction violation described in the `collectFees` report: state (the fee/reward counter) is updated after, not before, the external transfer.

### Finding Description
`claimReward` reads `userInfo.rewardAmount`, performs `MGP.safeTransfer(msg.sender, userInfo.rewardAmount)`, and only afterwards sets `userInfo.rewardAmount = 0`: [1](#0-0) 

There is no `nonReentrant` modifier on `claimReward`, and no other guard prevents the external `safeTransfer` call from re-entering `claimReward` before `rewardAmount` is reset. This is exactly the check-effect-interaction violation flagged in the external report for `collectFees` — the "effect" (zeroing the balance) happens after the "interaction" (the token transfer), and the function is unprotected.

Reward accounting itself is populated from `MasterMagpie` via the `trigger` function, which is called during ordinary user claim flows (`_multiClaim` → `IReferralStorage(referral).trigger(...)`): [2](#0-1) [3](#0-2) 

Any ordinary wallet that registers/uses a referral code accrues `rewardAmount` through normal, unprivileged usage of `MasterMagpie`, then calls `claimReward()` directly on `ReferralStorage` (this function has no access restriction and no reentrancy guard).

### Impact Explanation
If the referral reward token (`MGP`) transfer can trigger a callback into the receiving contract (e.g., if `msg.sender` is a contract that hooks into `safeTransfer`/`tokensReceived` semantics, or a future/alternate reward token integration reuses this same pattern), the attacker's contract can re-enter `claimReward()` before `rewardAmount` is reset to 0, receiving the same reward multiple times. This directly drains the `MGP` balance held by `ReferralStorage`, an unprivileged-wallet-reachable theft of protocol funds, matching the "direct theft of user funds" / "protocol insolvency" impact bar.

### Likelihood Explanation
The call path is fully reachable by any ordinary wallet: register/use a referral code (`registerCode`/`useCode`), accrue reward via normal `MasterMagpie` claim flows (`trigger`), then call `claimReward()`. No privileged role, governance action, or external protocol dependency is required — only the CEI-violating code path in `claimReward` itself.

### Recommendation
Apply checks-effects-interactions: zero `userInfo.rewardAmount` **before** calling `MGP.safeTransfer`, and/or add a `nonReentrant` guard to `claimReward()`, consistent with how `getRewards` (plural) is already protected with `nonReentrant` in `vlMGPBaseRewarder.sol` and `mWOMSVBaseRewarder.sol`: [4](#0-3) 

### Proof of Concept
```
1. Attacker deploys a malicious contract with a fallback/hook that calls ReferralStorage.claimReward() again.
2. Attacker registers a referral code and gets other users to use it (useCode), or is itself referred.
3. Ordinary claim flow (MasterMagpie._multiClaim -> ReferralStorage.trigger) credits userInfo.rewardAmount for the attacker's contract address.
4. Attacker's contract calls claimReward():
   - MGP.safeTransfer(attackerContract, rewardAmount) executes.
   - If this transfer/callback re-enters claimReward() before the line
     `userInfo.rewardAmount = 0` executes, rewardAmount is read again as
     the same non-zero value and transferred a second time.
5. Repeat until ReferralStorage's MGP balance is drained.
```

Note: exploitability depends on whether the `MGP` token implementation (`Mgp.sol`) or a token used with this same accounting pattern can invoke a callback during `safeTransfer` to the receiving address; I was not able to fully inspect `Mgp.sol`'s transfer implementation within this session to confirm hook behavior. Regardless, the code as written is a clear check-effects-interactions violation and lacks the `nonReentrant` protection applied elsewhere in the same codebase (e.g., `getRewards` in `vlMGPBaseRewarder.sol`/`mWOMSVBaseRewarder.sol`), making it the closest unprivileged-wallet analog to the reported `collectFees` reentrancy bug class.

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

**File:** rewards/MasterMagpie.sol (L576-580)
```text
        uint256 totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount;

        if (totalReward > 0 && referral != address(0)) {
            IReferralStorage(referral).trigger(_user, totalReward);
        }
```

**File:** rewards/vlMGPBaseRewarder.sol (L248-260)
```text
    function getRewards(address _account, address _receiver, address[] memory _rewardTokens)
        public
        onlyMasterMagpie
        updateRewards(_account, _rewardTokens)
        nonReentrant
    {
        uint256 length = _rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }
    }
```
