### Title
Sybil self-referral via `registerCode`/`useCode` allows farming both referer and referee reward shares in `ReferralStorage.trigger()` - (File: rewards/ReferralStorage.sol)

### Summary
`ReferralStorage.useCode()` only prevents a single address from referring itself (`codeOwners[_code] == msg.sender` check), but performs no Sybil-resistance check across two attacker-controlled addresses. An attacker can register a code with address A and use it with address B (both controlled by the same entity), then have B generate MGP claim activity so that `trigger()` credits both the referer share (to A) and referee share (to B) to the same beneficiary, capturing the full referral incentive budget for a single unit of genuine staking activity instead of the referee-only share a normal unreferred user would receive.

### Finding Description
`registerCode()` at [1](#0-0)  lets any address register an arbitrary unused code with no restriction tying the registering address to a unique real-world identity. `useCode()` at [2](#0-1)  only blocks `codeOwners[_code] == msg.sender` (an address cannot refer itself) and blocks re-setting an existing referral (`myReferer[msg.sender] != address(0)`), but does nothing to prevent a *different* address controlled by the same attacker from using the code.

When `MasterMagpie` calls `trigger(_referee, _amount)` upon the referee's MGP claim ( [3](#0-2) ), the referer (`myReferer[_referee]`) and referee both get `rewardAmount` credited based on percentages of the claimed `_amount`. Since `myReferer[addrB] == addrA` and both addresses are controlled by the attacker, both the referer split and referee split accrue to the same attacker, who then withdraws both via `claimReward()` ( [4](#0-3) ). No modifier, receipt-token check, or reward-index logic in this contract distinguishes a genuine third-party referral from a self-controlled Sybil pair — the only anti-Sybil guard present (`Circled()`) is trivially bypassed by using two addresses instead of one.

### Impact Explanation
This lets an attacker capture the entire referral incentive allocation (referer share + referee share) for their own staking/claim activity, rather than only the base (unreferred) share a normal single staker would receive. This is a theft of unclaimed MGP referral yield that was intended to reward genuine third-party referrals, diverting protocol incentive budget to Sybil-controlled wallets, repeatable indefinitely with fresh code registrations across wallet pairs.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs two EOAs (trivial to generate, no privileged role needed) and normal MGP staking/claim capability through `MasterMagpie`, which any user already has. The exploit is fully permissionless, requires no flash loans or special capital beyond whatever stake the attacker would make anyway, and is repeatable across as many wallet pairs and codes as desired, making it highly feasible and high likelihood.

### Recommendation
Referral Sybil-resistance is inherently hard to enforce purely on-chain, but exposure can be reduced by: capping/removing referee-side extra rewards for the "referee" share so it doesn't create a straightforward doubling incentive for self-dealing, requiring meaningful economic separation (e.g., minimum locked/staked history or KYC/off-chain uniqueness attestation) before a code can be registered or used, and/or capping total referral reward payout per referer-referee pair relative to independently verifiable activity. At minimum, monitor/rate-limit `registerCode`/`useCode` pairs exhibiting circular fund flows (A pays B, B refers under A) to detect and blacklist Sybil clusters via `forceSetCodeOwner`.

### Proof of Concept
Foundry test outline:
1. Deploy `ReferralStorage`, `MasterMagpie` mock, and MGP token; fund `ReferralStorage` with MGP for reward payouts and set a tier via `setTier`.
2. `vm.prank(addrA); referralStorage.registerCode(code);`
3. `vm.prank(addrB); referralStorage.useCode(code);`
4. Simulate `MasterMagpie` claim flow for `addrB` of amount `X`, calling `referralStorage.trigger(addrB, X)` (via `_onlyMasterMagpie` caller).
5. Assert `userInfos[addrA].rewardAmount == refererAmount` and `userInfos[addrB].rewardAmount == refereeAmount`, both nonzero.
6. `addrA.claimReward()` and `addrB.claimReward()`; assert combined MGP received by attacker-controlled addresses (`refererAmount + refereeAmount`) exceeds the amount a single unreferred staker claiming the same `X` would receive (which is just `X`, with zero referral bonus), demonstrating the extra yield extracted purely from self-referral rather than genuine third-party referral activity.

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
