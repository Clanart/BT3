### Title
Sybil mutual-referral bypasses `Circled` self-referral guard in `ReferralStorage.useCode`, letting collusive addresses capture full referral bonus on their own claims - (File: rewards/ReferralStorage.sol)

### Summary
`ReferralStorage.useCode` only rejects the case where `codeOwners[_code] == msg.sender` (literal self-referral), but does nothing to prevent two attacker-controlled addresses from registering codes and referring each other. This lets a single actor controlling two EOAs collect both the "referer" and "referee" share of the referral bonus on every `MasterMagpie` claim, without any independent, externally-sourced referred volume.

### Finding Description
`useCode` performs a single check to stop self-dealing: [1](#0-0) 

This only blocks `A.useCode(codeA)` where `A` is both the code owner and the caller. It does not stop:
```
A.registerCode(codeA);         // codeOwners[codeA] = A
B.registerCode(codeB);         // codeOwners[codeB] = B
A.useCode(codeB);              // myReferer[A] = B  (codeOwners[codeB]=B != A -> passes Circled check)
B.useCode(codeA);              // myReferer[B] = A  (codeOwners[codeA]=A != B -> passes Circled check)
```
There is also no check anywhere preventing an address from simultaneously being a code owner (referer) and a referee of the person referring it - the `HasReferee` error is declared but never used in the contract, so the guard rail implied by its name doesn't actually exist: [2](#0-1) 

Once the mutual loop is formed, every time `MasterMagpie` finishes a claim for a colluding address it calls `trigger`, which splits a percentage of the claim amount between referer and referee: [3](#0-2) [4](#0-3) 

When A claims: referee=A, referer=myReferer[A]=B → B gets `refererAmount`, A gets `refereeAmount`.
When B claims: referee=B, referer=myReferer[B]=A → A gets `refererAmount`, B gets `refereeAmount`.

Summed across the pair, for every claim made by either colluding address, the attacker captures `(basic + boosted)` percentage of the claimed amount split between the two accounts they control - exactly the total bonus that the `Circled` check was meant to prevent a single self-dealing address from claiming, but achievable via a two-address Sybil loop with zero real, independent referred stake or volume.

### Impact Explanation
This is theft of unclaimed yield from the referral reward pool (funded by MGP held in `ReferralStorage`, distributed via `claimReward`). An attacker with two addresses and minimal MGP/stake can inflate their aggregate referral reward beyond what a single non-colluding referrer/referee pair could earn for the same amount of legitimate staked/claimed volume, draining the referral incentive pool without providing the real user-acquisition value the mechanism is meant to reward.

### Likelihood Explanation
Very low capital and complexity: registering two codes and calling `useCode` in each direction costs only gas plus whatever minimal stake is needed to trigger `MasterMagpie` claims. The exploit is fully repeatable for every subsequent claim by either address, with no additional preconditions beyond controlling two EOAs.

### Recommendation
When processing `useCode`, check the reverse relationship as well - reject if `myReferer[codeOwners[_code]] == msg.sender` (i.e., the code owner is already referred by the caller), and more generally detect/prevent referral cycles among any set of addresses (or restrict eligibility so that a registered code owner cannot itself be a referee of one of its own referees, transitively).

### Proof of Concept
Foundry test plan:
1. Deploy `ReferralStorage`, `MasterMagpie`, `vlMGP`/`MGP` per existing test harness; set a nonzero `tier[1].rewardPercentage` and `sharePercent`.
2. `A.registerCode(codeA)`, `B.registerCode(codeB)`.
3. `A.useCode(codeB)` and `B.useCode(codeA)` - assert both succeed (not reverted by `Circled`).
4. Have A and B each stake the same minimal amount in `MasterMagpie` and accrue MGP rewards.
5. A calls `multiClaim`/claim path that triggers `IReferralStorage(referral).trigger(A, amount)`; then B claims similarly.
6. Assert `userInfos[A].rewardAmount + userInfos[B].rewardAmount` equals `(basic + boosted) * (amountA + amountB) / DENOMINATOR` - i.e., the full referral bonus percentage is captured by the colluding pair, which is strictly greater than what either address could earn alone under the `Circled` restriction (0, since self-referral is blocked) or what an honest one-directional referral would generate (only one side of the split per claim, not both).
7. Compare against a baseline scenario where A refers an independent, non-colluding user C who has no way to also refer back to A - baseline reward for equivalent staked volume is lower, demonstrating the inflated capture via mutual referral.

### Citations

**File:** rewards/ReferralStorage.sol (L66-76)
```text
    error OnlyMasterMagpie();
    error OnlyVlMGP();
    error InvalidCode();
    error CodeOccupied();
    error HasReferral();
    error InvalidPercentage();
    error Circled();
    error InvalidPercent();
    error HasReferee();
    error InsufficientRewardBalance();

```

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

**File:** rewards/MasterMagpie.sol (L576-580)
```text
        uint256 totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount;

        if (totalReward > 0 && referral != address(0)) {
            IReferralStorage(referral).trigger(_user, totalReward);
        }
```
