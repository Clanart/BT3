### Title
Self-referral cashback allows users to capture the entire referral reward pool for themselves - (File: rewards/ReferralStorage.sol)

### Summary
`ReferralStorage` lets any wallet self-refer using a second address it controls, harvesting both the "referrer" share and the "referee" share of the referral bonus that `MasterMagpie` triggers on every claim, exactly the "cashback on referral" bug class from the external report.

### Finding Description
`registerCode` lets any address register a referral code for itself, and `useCode` lets a caller attach that code as its referrer, with the only restriction being that the code owner cannot equal `msg.sender` directly (`if (codeOwners[_code] == msg.sender) revert Circled();`). [1](#0-0) 

There is no check tying the referrer address to a different beneficial owner (e.g. no KYC/whitelist of legitimate referrers, no on-chain link preventing two attacker-controlled EOAs from referring each other), so an attacker can trivially:
1. Register a code from wallet A (`registerCode`).
2. Use that code from wallet B (`useCode`), setting `myReferer[B] = A`.

Whenever wallet B harvests rewards through `MasterMagpie._multiClaim`, the contract unconditionally calls `IReferralStorage(referral).trigger(_user, totalReward)` for any harvested amount. [2](#0-1) 

`trigger` then splits a percentage of the harvested amount between the referrer and referee based on tier/boost settings and credits both `rewardAmount` balances: [3](#0-2) 

Since wallets A and B are both controlled by the same attacker, both the `refererAmount` and `refereeAmount` accrue to the same person, who can later withdraw both via `claimReward`: [4](#0-3) 

This is functionally identical to the cited report: the "referral discount/bonus" mechanism can be captured entirely by a single actor operating two addresses, since the code only blocks `codeOwners[_code] == msg.sender`, not the case of a second, attacker-controlled address.

### Impact Explanation
The referral reward pool (funded MGP held by `ReferralStorage`, meant to reward genuine third-party referral growth as an incentive/yield stream distributed via `trigger`) can be entirely harvested by self-referring attackers rather than legitimate independent referrers. Because `_calBoosted` also weights rewards by `userInfos[_account].factor / totalBoostFactor`, a rational actor is incentivized to register alt-accounts to inflate the share of referral yield they can extract, diverting funds that were meant to be distributed as referral yield to third parties. This is a theft of the referral distribution's yield by an unprivileged wallet acting as both referrer and referee for itself.

### Likelihood Explanation
Likelihood is high: `registerCode` and `useCode` are both fully permissionless external functions requiring no privileged role, and the only anti-self-referral check (`codeOwners[_code] == msg.sender`) is trivially bypassed by using a second wallet the same person controls. As soon as any user harvests through `MasterMagpie`, `trigger` is invoked automatically with no way to distinguish colluding wallets from genuine referrer/referee pairs.

### Recommendation
Do not rely on address-level self-check alone. Either (a) require referrer codes to be issued/whitelisted by the protocol for verified frontends/infrastructure providers only (as recommended in the original report), or (b) cap/eliminate the referrer-side reward when suspicious patterns (e.g., referrer and referee first-interacting in the same block, or referrer wallets with no independent activity) are detected, acknowledging this can only be mitigated, not fully prevented, at the smart-contract level.

### Proof of Concept
1. Attacker controls wallets A and B.
2. From A: call `ReferralStorage.registerCode(codeA)` — `rewards/ReferralStorage.sol:147-156`.
3. From B: call `ReferralStorage.useCode(codeA)` — sets `myReferer[B] = A` — `rewards/ReferralStorage.sol:134-145`.
4. From B: stake into any `MasterMagpie` pool and later call `multiclaim`/`multiclaimSpec` to harvest rewards, which triggers `MasterMagpie._multiClaim` → `IReferralStorage(referral).trigger(B, totalReward)` — `rewards/MasterMagpie.sol:576-581`.
5. `trigger` credits `refererAmount` to A's `rewardAmount` and `refereeAmount` to B's `rewardAmount` — `rewards/ReferralStorage.sol:172-195`.
6. Attacker calls `claimReward()` from both A and B to withdraw the full referral bonus split, having paid nothing to any independent third-party referrer — `rewards/ReferralStorage.sol:158-168`.

### Citations

**File:** rewards/ReferralStorage.sol (L134-156)
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

**File:** rewards/MasterMagpie.sol (L576-581)
```text
        uint256 totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount;

        if (totalReward > 0 && referral != address(0)) {
            IReferralStorage(referral).trigger(_user, totalReward);
        }
    }
```
