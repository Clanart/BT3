## Analog Found: Insufficient Validation of Referrer Legitimacy in `ReferralStorage`

### Title
Self-Referral via Sybil Wallets Drains `ReferralStorage` MGP Reward Pool - (File: `rewards/ReferralStorage.sol`)

### Summary
The external report flags unvalidated referral addresses in a mint discount flow. The direct analog in this codebase is `ReferralStorage.sol`, part of the in-scope referral distribution system tied to `MasterMagpie`. Registering as a referrer via `registerCode` has zero eligibility/KYC checks, and `useCode` only blocks a wallet from referring *itself* — it does not prevent a user from controlling both the referrer and referee wallets. This lets any unprivileged actor mint themselves free MGP rewards on top of their normal emissions, indefinitely, by claiming rewards through a "referee" wallet that is credited by their own "referrer" wallet.

### Finding Description
`registerCode` allows any address to become a referrer with no restriction: [1](#0-0) 

`useCode` only rejects the case where the caller is literally the same address as the code owner (`Circled`), but does nothing to verify the referrer is a distinct, legitimate third party: [2](#0-1) 

When a user claims MGP rewards through `MasterMagpie`, `trigger()` is invoked and unconditionally credits **both** the referrer and referee with extra `rewardAmount`, computed purely from `_amount` (the referee's legitimate claim) and the referrer's tier/boost, with no check that the referrer provided any real value or is a distinct economic actor: [3](#0-2)  This is wired into every reward claim path through `MasterMagpie._claim`/`multiClaim`: [4](#0-3) 

Because `registerCode`/`useCode` have no Sybil resistance, a single actor can:
1. Create wallet A, call `registerCode` to mint a referral code for free.
2. Create wallet B, call `useCode` with A's code (A ≠ B, so the `Circled` check passes trivially even though both are controlled by the same person).
3. Stake/claim MGP rewards repeatedly from wallet B (or many such wallet pairs).
4. Each claim triggers `trigger()`, crediting **extra**, unearned `rewardAmount` to both A and B — real MGP that is later withdrawn via `claimReward()`: [5](#0-4) 

This repeats without limit for every claim cycle, since there is no cap on the number of codes, referees, or claims, and `totalBoostFactor`/tier boosts can further be inflated by locking vlMGP in the same Sybil wallets.

### Impact Explanation
`ReferralStorage` holds/receives MGP intended to reward genuine referral-driven growth. With no validation of referral legitimacy, any unprivileged wallet pair can continuously mint themselves bonus MGP rewards from the referral pool with no real marketing/referral activity, directly draining the reward pool — this is a direct theft of protocol-held/unclaimed yield reserved for referral incentives, at the expense of legitimate referrers and the protocol's token supply/economics.

### Likelihood Explanation
Trivial and fully permissionless: creating two wallets, calling `registerCode`/`useCode`, and staking/claiming through `MasterMagpie` requires no special privileges, capital beyond gas, or race conditions. It can be repeated indefinitely and scaled with more Sybil wallet pairs.

### Recommendation
Add Sybil-resistance / eligibility checks for referral relationships, e.g.: require a minimum unlocked/locked stake or KYC-gated referrer registration, rate-limit or cap total referral reward accrual per referrer/referee pair, and/or detect and block referrer/referee wallets that share common on-chain provenance (e.g., funded from the same source, or enforce a lock-up/vesting period before referral rewards can be claimed to allow admin review).

### Proof of Concept
```solidity
// Wallet A
referralStorage.registerCode(codeA);

// Wallet B (different address, same controller)
referralStorage.useCode(codeA);

// Wallet B stakes MGP/mWOM/LP into MasterMagpie, then repeatedly calls
// multiClaim(...) -> MasterMagpie._claim() -> referral.trigger(B, totalReward)
// Each call credits both A.rewardAmount and B.rewardAmount with extra MGP
// beyond B's actual earned emission, with no cap on repetition.

// Wallets A and B each call claimReward() to withdraw the inflated MGP.
``` [6](#0-5)

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
