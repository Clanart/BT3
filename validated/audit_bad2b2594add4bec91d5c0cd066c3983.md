### Title
Front-Running of `registerCode()` Allows Theft of Referral Rewards - (File: rewards/ReferralStorage.sol)

### Summary
`ReferralStorage.registerCode()` lets any address claim an arbitrary `bytes32` referral code by simply being the first to submit a transaction with that code, with no fee, cooldown, or per-user code derivation. An attacker monitoring the mempool can front-run a legitimate user's `registerCode(_code)` call, claim the same `_code` for themselves, and cause the legitimate user's transaction to revert while permanently diverting all future referral rewards tied to that code to the attacker.

### Finding Description
`registerCode()` only checks that the code is unclaimed before assigning ownership: [1](#0-0) 

```solidity
function registerCode(bytes32 _code) external {
    if (_code == bytes32(0)) revert InvalidCode();
    if (codeOwners[_code] != address(0)) revert CodeOccupied();

    codeOwners[_code] = msg.sender;
    userInfos[msg.sender].myCode = _code;
    userInfos[msg.sender].tier = 1; // tier 1 as default

    emit RegisterCode(msg.sender, _code);
}
```

This is structurally identical to the reported `enrollCourier()` pattern: a first-come-first-served identifier claim with `require(<slot is empty>)` gating, and no binding of the code to the caller's identity (e.g., derived from `msg.sender`) or any anti-squatting mechanism (fee, rate limit, commit-reveal). An attacker watching the mempool for a `registerCode` transaction with a particular `_code` (e.g., a vanity/marketing code a project or influencer is about to publicize) can submit their own `registerCode(_code)` with higher gas, becoming `codeOwners[_code]` first. The legitimate user's transaction then reverts with `CodeOccupied()`.

Because `codeOwners` is a global, permanent (no re-registration/reset for a taken code within scope) mapping used by `useCode()` to attribute referees to a referrer, and by `trigger()` to route referral rewards: [2](#0-1) [3](#0-2) 

Once the attacker owns the code, every referee who later calls `useCode(_code)` — believing they are linking to the intended referrer's referral link — instead links to the attacker via `myReferer[msg.sender] = codeOwners[_code]`. All subsequent referral rewards computed in `trigger()` (`refererAmount`) accrue to the attacker's `userInfos[attacker].rewardAmount` instead of the legitimate code owner, and are claimable by the attacker via `claimReward()`: [4](#0-3) 

### Impact Explanation
This results in concrete theft of unclaimed yield: referral reward shares that would have accrued to the intended referrer are permanently redirected to the front-running attacker for as long as the squatted code remains active and referees use it. Because `codeOwners[_code]` is never reassignable by the original intended user once occupied (only `owner` via `forceSetCodeOwner` can fix it, which is a privileged/manual remediation, not a protocol guarantee), the loss of referral earnings for the intended party is effectively permanent absent admin intervention.

### Likelihood Explanation
Exploitation only requires mempool monitoring and a marginally higher gas bid, which is trivial and cheap, especially on low-fee networks. Any publicly announced or predictable referral code (e.g., branded codes shared as part of marketing) is a natural, high-value target, making this a realistic and repeatable griefing/theft vector against ordinary users with no special privileges required by the attacker.

### Recommendation
Do not allow users to freely choose arbitrary global codes. Instead:
- Derive the code deterministically from `msg.sender` (e.g., a hash or truncation of the address) so no two users can contest the same code, or
- Maintain a protocol-incrementing code/id in contract state per caller, or
- If free-form codes must be supported, bind registration to `msg.sender` identity checks, add a registration fee/rate limit, or use a commit-reveal scheme so front-runners cannot observe and copy the code before it is finalized.

### Proof of Concept
1. Alice broadcasts `registerCode("MAGPIE10")` intending to become `codeOwners["MAGPIE10"]`.
2. Attacker observes this pending transaction in the mempool and submits `registerCode("MAGPIE10")` with higher gas price.
3. Attacker's transaction is mined first: `codeOwners["MAGPIE10"] = attacker`.
4. Alice's transaction reverts with `CodeOccupied()`.
5. Referees who intended to use Alice's promoted code call `useCode("MAGPIE10")`, linking themselves to `attacker` instead: `myReferer[referee] = attacker`.
6. On reward distribution, `trigger()` credits `refererAmount` to `userInfos[attacker].rewardAmount`, which the attacker withdraws via `claimReward()`, permanently diverting rewards that should have gone to Alice.

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
