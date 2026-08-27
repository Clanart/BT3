### Title
Sybil self-referral in ReferralStorage allows an attacker to mint unearned referral commissions from the shared MGP reward pool - ([File: rewards/ReferralStorage.sol])

### Summary
`useCode`/`registerCode` place no restriction on who may register or use a code, and `trigger` computes referrer and referee bonuses purely from the referee's harvested amount and the referrer's tier/boost factor, with no check that the referrer ever staked or contributed value. An attacker controlling two wallets (A and B) can make B register a code, have A use it, then stake and claim in `MasterMagpie`, causing `MasterMagpie._multiClaim` to call `IReferralStorage(referral).trigger(A, totalReward)` and credit B's `rewardAmount` purely as a sybil referrer, entirely funded from `ReferralStorage`'s MGP balance rather than from any value B contributed.

### Finding Description
`useCode` only checks that the code exists, isn't the caller's own code, and that the caller has no existing referrer [1](#0-0) . `registerCode` lets any address register any unused code with no eligibility check, defaulting to tier 1 [2](#0-1) . Neither function verifies that the referrer (B) and referee (A) are distinct real economic actors, so a single attacker fully controls both roles.

`MasterMagpie._multiClaim` invokes `trigger` automatically at the end of every claim for any user with a referrer set, passing the user's own accrued `totalReward`: [3](#0-2) 

`trigger` then computes `refererAmount` and `refereeAmount` as extra percentages of `_amount` (based on the referrer's tier and boosted factor) and credits both to `rewardAmount` fields, which are later paid out via `claimReward`'s `MGP.safeTransfer`: [4](#0-3) [5](#0-4) 

Critically, `refererAmount` and `refereeAmount` are *additive* on top of the `totalReward` that A already receives directly from `MasterMagpie` (via `_sendMGP`/`_sendVlMGPFor`/`_sendMGPForVlMGPPool`) before `trigger` is even called [6](#0-5) . So this is not a redistribution of A's own reward but new claimable MGP created in `ReferralStorage`'s ledger, backed by whatever MGP balance sits in the `ReferralStorage` contract (funded out-of-band by the protocol/treasury, not tied per-referral to any deposit from B). No modifier, pause, or reentrancy guard prevents this since `trigger` is only gated by `_onlyMasterMagpie`, and `MasterMagpie` calls it unconditionally whenever a referred user claims — there is no check that the referrer B ever staked, deposited, or otherwise contributed capital.

### Impact Explanation
An attacker with two wallets can self-refer and receive protocol-funded MGP "referral rewards" proportional to their own stake's harvested amount, without any second party being genuinely referred. This drains `ReferralStorage`'s finite MGP balance (theft/misallocation of the referral reward pool — unclaimed yield intended for legitimate referrers) and effectively lets the attacker earn more total MGP than their stake/accrual entitles them to, since B's `rewardAmount` growth has zero correlation with any stake/deposit in `MasterMagpie.userInfo`. This matches the "theft of unclaimed yield" impact class.

### Likelihood Explanation
This requires no special privileges — only two ordinary EOAs, one `registerCode` call, one `useCode` call, and then normal staking/claiming through `MasterMagpie` (`deposit` + `multiclaim`). It is trivially repeatable for every claim cycle and scales with the attacker's own stake size and boosted factor, making it fully feasible and low-cost.

### Recommendation
Add sybil-resistance to the referral system, e.g.: require a minimum stake/lock or KYC-independent economic bond for a code to be eligible as `codeOwners`; disallow crediting `refererAmount` unless the referrer address differs from the referee and has no common funding/control heuristics feasible on-chain (hard in general); more practically, cap or scale referral rewards to be funded strictly from a per-referee-earned pool rather than newly credited balances, or require the referrer to have an active stake/lock in `MasterMagpie`/`vlMGP` above a threshold before `trigger` credits any `refererAmount`.

### Proof of Concept
Hardhat test outline:
1. Deploy `MasterMagpie`, `ReferralStorage`, `vlMGP`, fund `ReferralStorage` with MGP as the protocol would.
2. Wallet B calls `registerCode(codeB)`.
3. Wallet A calls `useCode(codeB)`.
4. Wallet A calls `MasterMagpie.deposit` into a pool, advance time, call `MasterMagpie.multiclaim`.
5. Assert `MasterMagpie.userInfo[stakingToken][B].amount == 0` (B never staked).
6. Assert `ReferralStorage.userInfos(B).rewardAmount > 0` and grows proportional to A's `totalReward`, confirming B accrues claimable MGP with zero stake — call `ReferralStorage.claimReward()` from B and assert MGP is transferred to B despite B holding no stake.

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

**File:** rewards/MasterMagpie.sol (L564-581)
```text
        if (vlMGPPoolAmount > 0) {
            _sendMGPForVlMGPPool(_user, _receiver, vlMGPPoolAmount);
        }

        if (mWOmPoolAmount > 0) {
            _sendMGP(_user, _receiver, mWOmPoolAmount);
        }

        if (defaultPoolAmount > 0) {
            _sendVlMGPFor(_user, _receiver, defaultPoolAmount);
        }

        uint256 totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount;

        if (totalReward > 0 && referral != address(0)) {
            IReferralStorage(referral).trigger(_user, totalReward);
        }
    }
```
