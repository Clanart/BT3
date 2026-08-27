### Title
Sybil Self-Referral Allows Users to Drain the Protocol-Funded Referral Reward Pool - ([File: rewards/ReferralStorage.sol])

### Summary
`ReferralStorage.sol` implements a referral rebate program tied into `MasterMagpie._multiClaim`, where a referrer and referee split a percentage of the referee's harvested MGP reward, paid out of MGP tokens held by `ReferralStorage`. While direct self-referral (using the same address as both referrer and referee) is blocked, the contract has no protection against a user controlling two separate wallets to register a code with one address and use it with another, capturing both the referrer and referee rebate on their own harvest activity.

### Finding Description
`registerCode` lets any address register a referral code with no restrictions [1](#0-0) . `useCode` only prevents the exact same address from using its own code (`Circled()` check), but does not — and cannot — prevent a Sybil pair of addresses controlled by the same actor [2](#0-1) .

When the referee later claims MGP rewards through `MasterMagpie._multiClaim`, the admin-set `referral` contract (`ReferralStorage`) is invoked via `trigger(_user, totalReward)` [3](#0-2) . `trigger` splits a percentage of `_amount` (the referee's already-harvested reward) between the referrer and referee, crediting both `rewardAmount` fields, which are later paid out in MGP via `claimReward` [4](#0-3) [5](#0-4) . This MGP is transferred out of `ReferralStorage`'s own balance, which is a fixed, admin/team-funded incentive reserve for genuine referral relationships, not new emissions tied to the referee's real reward.

By deploying two wallets — Wallet A registers a code via `registerCode`, Wallet B calls `useCode` to link to Wallet A — the same actor can harvest normally through Wallet B and simultaneously credit both the referrer rebate (to Wallet A) and referee rebate (to Wallet B) to themselves, exactly mirroring the external report's bug class: a user can set the "referral" relationship to an address they control and reclaim a share of protocol-managed incentive funds that should only go to independent third-party referrers.

### Impact Explanation
Every harvest performed by a Sybil-linked referee wallet drains `(basic + boosted) * totalReward / DENOMINATOR` in MGP from `ReferralStorage`'s finite MGP balance that would otherwise be reserved for legitimate referral partners. This is a direct, unprivileged extraction of protocol-held funds (the referral incentive pool) with no genuine referral activity behind it, and it can be repeated indefinitely across all of a user's harvests, permanently depleting funds intended for real referrers/referees.

### Likelihood Explanation
The attack requires only two ordinary wallets and calls to public functions (`registerCode`, `useCode`, then normal MasterMagpie deposit/harvest flow) — no privileged role, governance action, or external protocol dependency is needed. Sybil wallet creation is trivial and costless, making this readily and repeatedly exploitable by any user who wants to maximize their own harvested value at the referral pool's expense.

### Recommendation
Do not treat address-level self-referral checks as sufficient Sybil resistance. Consider requiring referral registration/linking to be gated by a trusted/admin-approved process (e.g., admin-approved referrer whitelisting, KYC-linked codes, or a cap on total rebate a single harvesting identity/cluster can draw), and/or fund the referral rebate from protocol emission rather than a static admin-funded balance so that self-referral, if unavoidable, is bounded to the same-address emission it targets rather than being an out-of-band withdrawal from a shared incentive pool.

### Proof of Concept
1. Wallet A calls `ReferralStorage.registerCode(codeA)` [1](#0-0) , becoming `codeOwners[codeA] = A`.
2. Wallet B (controlled by the same actor) calls `ReferralStorage.useCode(codeA)`, setting `myReferer[B] = A` [2](#0-1) . This passes because `B != A`, so `Circled()` does not revert.
3. Wallet B stakes tokens in `MasterMagpie` and later calls `multiclaim`/`multiclaimSpec`, triggering `_multiClaim`, which calls `IReferralStorage(referral).trigger(B, totalReward)` [3](#0-2) .
4. `trigger` credits `refererAmount` to A's `rewardAmount` and `refereeAmount` to B's `rewardAmount`, both payable in MGP from `ReferralStorage`'s balance [4](#0-3) .
5. Both A and B call `claimReward()` to withdraw the MGP, meaning the same actor captures the full referrer+referee split for what is effectively a self-generated harvest, at the expense of the shared referral incentive pool [5](#0-4) .

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
