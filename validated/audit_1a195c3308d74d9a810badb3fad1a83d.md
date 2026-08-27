### Title
Two-account referral ring bypasses `Circled()` self-check, allowing mutual referral reward extraction - ([File: rewards/ReferralStorage.sol])

### Summary
`ReferralStorage.useCode()` only rejects a user from using their own code (`codeOwners[_code] == msg.sender`), but does not prevent two attacker-controlled addresses from registering codes and using each other's codes, forming a closed two-account referral ring. Because `myReferer` mapping and `trigger()` reward-splitting logic treat any registered relationship as legitimate, an attacker with two addresses can make each address act as the other's "referrer," extracting `refererAmount` on every `trigger()` call triggered by the other address's activity.

### Finding Description
`registerCode()` [1](#0-0)  lets any address register an arbitrary unused code with no relationship checks. `useCode()` [2](#0-1)  only guards against direct self-referral via `Circled()` (`codeOwners[_code] == msg.sender`) and against a user already having a referrer (`HasReferral()`), but does nothing to prevent A and B, both controlled by the same attacker, from cross-registering: A registers `codeA`, B registers `codeB`, B calls `useCode(codeA)` (sets `myReferer[B] = A`), and A calls `useCode(codeB)` (sets `myReferer[A] = B`). This creates a mutual referral relationship where each address is simultaneously "referrer" and "referee" of the other.

When `MasterMagpie` calls `trigger(_referee, _amount)` [3](#0-2)  upon either A's or B's reward claim elsewhere in the protocol, the referrer side of the ring (the other attacker-controlled address) accrues `refererAmount` into `userInfos[_referer].rewardAmount`, and the "referee" side gets `refereeAmount`, both claimable via `claimReward()` [4](#0-3) . Since both directions of the ring are triggered independently (once when A claims and once when B claims), the same attacker collects boosted extra reward shares on both sides — an amount that would not exist without the artificial referral link, since a real, unrelated referrer would have been required to create this reward split.

### Impact Explanation
This does not print unbacked new tokens outright — `refererAmount`/`refereeAmount` are fractions of `_amount` computed from `tiers[tierId].rewardPercentage` and `_calBoosted()`, funded by whatever MGP balance is provisioned to `ReferralStorage`. However, this still represents a diversion of referral incentive rewards that are meant to reward genuine referral relationships to a single self-owned entity, i.e., theft/diversion of unclaimed yield allocated for the referral program, matching the "theft of unclaimed yield" impact class described in the prompt.

### Likelihood Explanation
This is trivially exploitable by any unprivileged attacker: it requires only deploying two EOAs, holding some MGP for locking (to gain boost factor via `updateTotalFactor`), and calling `registerCode`/`useCode` from each account — no special privileges, flash loans, or governance access needed. It is fully repeatable for every reward-triggering claim.

### Recommendation
Add a check in `useCode()` (or `trigger()`) that detects and rejects circular referral relationships beyond direct self-reference — e.g., disallow setting `myReferer[msg.sender] = codeOwners[_code]` if `myReferer[codeOwners[_code]] == msg.sender` (i.e., the code owner already lists `msg.sender` as their referrer), which blocks the two-account ring. More robust protection may require tracking a chain/ancestor check or restricting one referral code use per unique verified identity, though on-chain identity verification is inherently limited.

### Proof of Concept
Hardhat test plan:
1. Deploy `ReferralStorage`, mock `MasterMagpie`/`vlMGP`.
2. From address A: `registerCode(codeA)`.
3. From address B: `registerCode(codeB)`.
4. From B: `useCode(codeA)` — succeeds, sets `myReferer[B] = A`.
5. From A: `useCode(codeB)` — succeeds despite ring, sets `myReferer[A] = B`.
6. Mock `MasterMagpie` calls `trigger(B, amount1)` and `trigger(A, amount2)`.
7. Assert `userInfos[A].rewardAmount + userInfos[B].rewardAmount` includes both `refererAmount` and `refereeAmount` components from both calls, i.e., strictly greater than the reward a single one-directional (non-circular) referral relationship would produce for the same total `_amount` inputs.
8. Confirm both A and B can call `claimReward()` to withdraw the combined self-generated referral yield.

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
