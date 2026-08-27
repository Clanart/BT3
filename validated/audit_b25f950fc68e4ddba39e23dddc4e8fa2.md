### Title
Self-Referral Sybil Drains ReferralStorage MGP Reward Reserve - (File: rewards/ReferralStorage.sol)

### Summary
`ReferralStorage` pays out a MGP "referral bonus" on top of every normal reward claim a user makes through `MasterMagpie`. Because nothing prevents an attacker from controlling two wallets — one that registers a referral code and one that "uses" it — an attacker can perpetually route their own staking rewards through their own referral code, minting themselves extra MGP from the referral reward pool on every claim cycle. This mirrors the SponsorVault exploit: an attacker plays both "sponsor" and "beneficiary" roles to siphon a subsidy pool that was meant to reward genuine third-party referrals.

### Finding Description
`useCode` only blocks using a code that the *same* address registered (`codeOwners[_code] == msg.sender` reverts with `Circled`), but has no protection against a user controlling two separate EOAs/contracts: [1](#0-0) 

Address A calls `registerCode` to become a code owner, and Address B (also controlled by the attacker) calls `useCode` to link itself as A's referee: [2](#0-1) 

When Address B (the "referee") stakes normally in `MasterMagpie` and later claims its legitimate MGP rewards, `_claim` computes `totalReward` and forwards it to `ReferralStorage.trigger`: [3](#0-2) 

`trigger` then mints an *additional* bonus on top of the reward B already received, split between the "referrer" (A) and "referee" (B), based on tier percentage plus a vlMGP-lock-based boost factor — both of which are fully controlled by the attacker since A and B are the same person: [4](#0-3) 

These `rewardAmount` balances are later withdrawn as real MGP tokens via `claimReward`, which transfers from the contract's MGP balance: [5](#0-4) 

There is no check that the referrer and referee are economically independent parties, no cooldown, and no cap tied to genuine external referral activity — the attacker can repeat this every time they claim staking rewards, and can amplify the boost by locking MGP into `vlMGP` under address A to raise `_calBoosted`.

### Impact Explanation
Every claim cycle, the attacker extracts extra MGP from the protocol's referral reserve without bringing in any new user/capital — a direct, permanent transfer of protocol-held MGP to the attacker at the expense of the treasury/legitimate referral-program funding, analogous to draining `SponsorVault` in the reference report. This is a protocol insolvency/fund-theft vector rather than a one-off inefficiency, since it can be executed indefinitely and scaled by controlling more sybil wallet pairs and locking more MGP in vlMGP to raise the boost factor.

### Likelihood Explanation
The attack path requires only unprivileged wallet actions available to any user: `registerCode`, `useCode`, normal staking/claiming in `MasterMagpie`, and `claimReward` in `ReferralStorage`. No admin or governance interaction is required, and the cost is only gas plus the (fully recoverable) stake amount, making this trivially and repeatedly exploitable by any sophisticated user running two wallets.

### Recommendation
Add sybil resistance to the referral linkage, e.g.: disallow referrer/referee pairs that share on-chain relationships that are easy for the same owner to construct (at minimum this is a known-hard problem, so consider capping total referral payouts per code/tier over time, requiring KYC/whitelisting of referral codes, or funding referral rewards only from a fixed, non-inflationary pool with a global rate limit), and/or require that referral bonuses be paid only when the referee's rewards are realized from capital not controlled by the same beneficial owner as the referrer (impossible to fully verify on-chain, so the safer mitigation is strict per-referrer/day payout caps and monitoring).

### Proof of Concept
1. Attacker controls Address A and Address B.
2. A calls `ReferralStorage.registerCode(codeA)` — `rewards/ReferralStorage.sol:147-156`.
3. B calls `ReferralStorage.useCode(codeA)` — `rewards/ReferralStorage.sol:134-145` — linking B as A's referee.
4. B stakes LP/mWOM/vlMGP normally through `MasterMagpie` and calls the claim function; `_claim` sends B its legitimate `totalReward` and calls `IReferralStorage(referral).trigger(B, totalReward)` — `rewards/MasterMagpie.sol:576-580`.
5. `trigger` credits `refererInfo(A).rewardAmount` and `refereeInfo(B).rewardAmount` with `(basic + boosted)%` of `totalReward`, entirely additional MGP beyond what B already received — `rewards/ReferralStorage.sol:172-195`.
6. A and B each call `claimReward()` to withdraw the accrued MGP from the `ReferralStorage` contract balance — `rewards/ReferralStorage.sol:158-168`.
7. Repeat steps 4–6 on every staking/claim cycle, optionally locking MGP into `vlMGP` under A to raise `_calBoosted` and increase the siphoned percentage — `rewards/ReferralStorage.sol:243-246`.

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

**File:** rewards/MasterMagpie.sol (L576-580)
```text
        uint256 totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount;

        if (totalReward > 0 && referral != address(0)) {
            IReferralStorage(referral).trigger(_user, totalReward);
        }
```
