### Title
Self-referral via Sybil addresses bypasses the `Circled()` anti-self-dealing check in `ReferralStorage.useCode`/`trigger` - ([File: rewards/ReferralStorage.sol])

### Summary
`ReferralStorage.useCode` explicitly blocks `codeOwners[_code] == msg.sender` (the `Circled()` check) to prevent a user from referring themselves, but this protection is trivially bypassed by using a second, attacker-controlled address. An attacker who deploys two addresses (A registers a code, B uses it) can have B perform genuine staking/locking and, upon B's claim, `trigger()` credits both the "referrer" (A) and "referee" (B) shares to addresses fully controlled by the same attacker.

### Finding Description
`registerCode` and `useCode` only check identity equality, not economic distinctness: [1](#0-0) 
`useCode` reverts only if `codeOwners[_code] == msg.sender` (`Circled()`), which prevents `A` from calling `useCode` with its own code, but does nothing to stop `A` and `B` (two Sybil addresses of the same attacker) from forming a referrer/referee pair.

When `B` accrues and claims MGP rewards through `MasterMagpie` (via real staking/locking activity), `MasterMagpie._claim` invokes: [2](#0-1) 
which calls `ReferralStorage.trigger(B, totalReward)`: [3](#0-2) 
This computes `refererAmount` and `refereeAmount` as percentages of `B`'s own reward and credits `refererInfo.rewardAmount` (address `A`) and `refereeInfo.rewardAmount` (address `B`) — both under the attacker's control. Both can later be drained via `claimReward()`: [4](#0-3) 

The `_calBoosted`/`updateTotalFactor` factor boosting mechanism further scales the referrer share up with `vlMGP` locked amount, so an attacker can additionally boost `A`'s cut by having `A` lock its own MGP: [5](#0-4) 

The root cause is that "distinctness" of referrer/referee is enforced only by address inequality, not by any Sybil resistance, while the code's own `Circled()` error demonstrates the developers' explicit intent to prevent self-referral profit extraction.

### Impact Explanation
The referral rebate (`refererAmount` + `refereeAmount`) is paid out of `ReferralStorage`'s MGP balance via `claimReward`'s `MGP.safeTransfer`, which is a shared, presumably treasury-funded pool intended to reward genuine third-party referral growth. By splitting one identity into two addresses, an attacker converts a portion of their own already-earned MGP reward into an *additional* rebate payout that the protocol did not intend to pay for self-referred volume, and that (once the pool balance is limited) can render `claimReward()` reverting with `InsufficientRewardBalance()` for legitimate, unrelated referrers — i.e., theft/freezing of unclaimed yield belonging to genuine referral participants. This matches the "theft or permanent freezing of unclaimed yield" impact class.

### Likelihood Explanation
Fully unprivileged: deploying two EOAs/contracts and calling public `registerCode`/`useCode` requires no special role. The only capital needed is whatever MGP/vlMGP the attacker would stake anyway to earn a legitimate claim; the self-referral markup is pure additional upside with no counterparty risk, and is fully repeatable for every claim cycle.

### Recommendation
Add Sybil-resistant restrictions to the referral linkage, e.g.: disallow `useCode` when `msg.sender` and `codeOwners[_code]` share the same funding source/lock relationship where feasible, require a minimum wait/vesting period plus a per-referrer cap on self-attributable volume, or move to an off-chain/attested referral validation (KYC-style allowlisting) rather than relying solely on address inequality. At minimum, cap total referral rebate payable per referrer as a fraction of independently-verified third-party TVL rather than raw claim amount.

### Proof of Concept
Foundry test outline:
1. Deploy `ReferralStorage`, `MasterMagpie`, `VLMGP`, MGP token; fund `ReferralStorage` with MGP for payouts; set a tier via `setTier`.
2. From address `A`: call `registerCode(codeA)`.
3. From address `B` (different EOA, same attacker key/wallet in test): call `useCode(codeA)`; assert `myReferer[B] == A`.
4. `B` deposits/locks MGP into `vlMGP`/staking pool through normal `MasterMagpie` flow to accrue a real reward.
5. `B` calls the `MasterMagpie` claim path (`_claim`) which triggers `ReferralStorage.trigger(B, totalReward)`.
6. Assert `userInfos[A].rewardAmount` increased by `totalReward * refererPercentage / DENOMINATOR` and `userInfos[B].rewardAmount` increased by the referee share — both attributable to the same attacker.
7. Call `claimReward()` from both `A` and `B`; assert MGP transferred from `ReferralStorage` balance to attacker-controlled addresses, and (with a bounded pool) assert a legitimate third referrer's later `claimReward()` reverts with `InsufficientRewardBalance()`.

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

**File:** rewards/ReferralStorage.sol (L197-206)
```text
    function updateTotalFactor(address _account) external override _onlyVlMGP {
        UserInfo storage userInfo = userInfos[_account];
        if (userInfo.myCode == bytes32(0)) return; // user did not activate referral feature
        
        totalBoostFactor -= userInfo.factor;
        uint256 vlMGPLockedAmoubnt = IVLMGP(vlMGP).getUserTotalLocked(_account);
        userInfo.factor = DSMath.sqrt(vlMGPLockedAmoubnt);

        totalBoostFactor += userInfo.factor;
    }
```

**File:** rewards/MasterMagpie.sol (L576-580)
```text
        uint256 totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount;

        if (totalReward > 0 && referral != address(0)) {
            IReferralStorage(referral).trigger(_user, totalReward);
        }
```
