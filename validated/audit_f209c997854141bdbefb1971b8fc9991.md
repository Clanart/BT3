Based on the code review, this is a valid finding.

### Title
Referral trigger() mints unfunded reward liabilities on top of referee claims, leading to protocol insolvency - (File: rewards/ReferralStorage.sol)

### Summary
`trigger()` in `rewards/ReferralStorage.sol` computes `refererAmount` and `refereeAmount` as percentages of the referee's `totalReward` from `MasterMagpie._multiClaim` and credits both amounts additively to `userInfos[...].rewardAmount`, without any accompanying transfer of MGP tokens into `ReferralStorage` to back these new liabilities. [1](#0-0)  Since `claimReward()` pays out of `MGP.safeTransfer` from the contract's own balance, every triggered referral creates additional claimable liability without a matching increase in the token balance held by the contract. [2](#0-1) 

### Finding Description
`MasterMagpie._multiClaim` computes the user's total newly harvested MGP (`totalReward`), pays it out to the user/receiver via `_sendMGP`/`_sendVlMGPFor`/`_sendMGPForVlMGPPool`, and then unconditionally calls `IReferralStorage(referral).trigger(_user, totalReward)` if the user has a referrer. [3](#0-2) 

Inside `trigger()`, `refererAmount` and `refereeAmount` are both derived as fractions of `_amount` (the referee's claimed reward) and are additively credited to `refererInfo.rewardAmount` and `refereeInfo.rewardAmount` — i.e., on top of what the referee already received from `MasterMagpie`, not carved out of it. [4](#0-3) 

There is no code path in the reviewed portion of `ReferralStorage.sol` or `MasterMagpie.sol` that transfers additional MGP into `ReferralStorage` proportional to `refererAmount + refereeAmount` at the time `trigger()` runs; the referee's MGP is sent directly to the referee/receiver by `MasterMagpie`, not to `ReferralStorage`. This means `userInfos[account].rewardAmount` (the sum of all outstanding referral claims) can grow independently of `MGP.balanceOf(address(this))` for `ReferralStorage`, which is exactly the invariant break described in the question.

However, I was unable to fully verify within the available tool budget how/whether `ReferralStorage` is pre-funded (e.g., via an owner-only funding mechanism, a fixed allocation, or a `mgpPerSec`-style emission unrelated to referee claims) — the constructor only sets `MGP`, `vlMGP`, `masterMagpie`, `BoostPoint`, `sharePercent` and does not show a funding source. [5](#0-4)  If, as the visible code suggests, there is no such funding leg tied to referral triggers, then this is a legitimate accounting bug: repeated referred claims will make cumulative `rewardAmount` liabilities exceed the contract's actual MGP balance, so eventually `claimReward()` calls will revert due to insufficient balance for some users (`InsufficientRewardBalance` is only checked against `rewardAmount == 0`, not against `MGP.balanceOf`) — resulting in some referrers/referees being unable to claim what the contract itself recorded as owed to them, i.e., insolvency of the referral reward pool.

### Impact Explanation
Every claim by a referred user creates additional recorded liability (`refererAmount` + `refereeAmount`) in `ReferralStorage` without a corresponding increase in the contract's MGP balance. As claim volume grows (trivially triggerable by any unprivileged account that stakes MGP/vlMGP, registers a referral code, refers itself via other addresses it controls, and repeatedly claims), the sum of `rewardAmount` balances across all users can exceed `MGP.balanceOf(address(this))`. This causes some legitimate claimants to be unable to withdraw their recorded rewards (`claimReward()` will attempt a `safeTransfer` that fails once the balance is exhausted), which is a protocol insolvency / fund-freezing condition for the referral reward pool specifically.

### Likelihood Explanation
The precondition (attacker splits one lock across multiple addresses, each registering a referral code and referring the others, then repeatedly calling `multiclaimFor`) requires only owning MGP/vlMGP and calling permissionless functions (`registerCode`, `useCode`, `multiclaimFor`) — no privileged role is needed. The exploit is repeatable indefinitely as long as the attacker keeps generating small legitimate claims to trigger referral accrual, since `trigger()` has no cap tied to actual contract balance.

### Recommendation
- Fund referral bonuses out of the referee's already-computed `totalReward` (i.e., reduce what is sent to the referee/receiver by `refererAmount + refereeAmount` and route that reduced portion into `ReferralStorage`) rather than adding referral amounts on top of a full, separately-paid claim.
- Alternatively, require `MasterMagpie` to transfer `refererAmount + refereeAmount` worth of MGP into `ReferralStorage` at the time `trigger()` is called, so `rewardAmount` liabilities are always backed 1:1 by an actual balance increase.
- Add an invariant check/guard in `claimReward()` (or in `trigger()`) that the total outstanding `rewardAmount` liabilities never exceed `MGP.balanceOf(address(this))`.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `Mgp`, `MasterMagpie`, `vlMGP` (or mocks), and `ReferralStorage`, wired together as in production (`masterMagpie` set correctly in `ReferralStorage`).
2. Fund `ReferralStorage` with zero MGP balance beyond whatever the referee's claim naturally routes through `MasterMagpie`.
3. Have attacker-controlled address A stake/lock MGP, `registerCode(codeA)`.
4. Have attacker-controlled address B `useCode(codeA)`, stake/lock MGP, and call `MasterMagpie.multiclaimFor` (or equivalent public claim entrypoint) to trigger reward harvesting, which internally calls `ReferralStorage.trigger(B, totalReward)`.
5. Repeat step 4 across many attacker-controlled referee addresses referring back to A.
6. Assert: sum of `userInfos[x].rewardAmount` for all `x` (A and all referees) exceeds `MGP.balanceOf(address(ReferralStorage))`.
7. Assert: at least one account's `claimReward()` call reverts due to insufficient MGP balance in `ReferralStorage`, despite `rewardAmount > 0`, demonstrating funds recorded as owed cannot actually be withdrawn.

### Citations

**File:** rewards/ReferralStorage.sol (L79-92)
```text
    function __ReferralStorage_init(
        address _vlMGP,
        address _masterMagpie,
        uint256 _boostPoint,
        uint256 _sharePercent
    ) public initializer {
        __Ownable_init();
        vlMGP = _vlMGP;
        MGP = IVLMGP(_vlMGP).MGP();
        masterMagpie = _masterMagpie;
        BoostPoint = _boostPoint;
        if(_sharePercent > DENOMINATOR) revert InvalidPercent();
        sharePercent = _sharePercent;
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

**File:** rewards/MasterMagpie.sol (L576-581)
```text
        uint256 totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount;

        if (totalReward > 0 && referral != address(0)) {
            IReferralStorage(referral).trigger(_user, totalReward);
        }
    }
```
