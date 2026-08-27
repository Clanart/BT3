### Title
`registerCode` in `ReferralStorage` can be front-run to steal ownership of a referral code and its future reward stream - ([File: rewards/ReferralStorage.sol])

### Summary
`ReferralStorage.registerCode()` lets any unprivileged wallet permanently claim a `bytes32` referral code on a strict first-come-first-served basis, with no whitelist, commit-reveal, or signature check tying the code to the caller who intended to use it. Exactly like the `createProject` front-running class, an attacker watching the mempool can see a pending `registerCode(_code)` transaction and resubmit it with a higher gas price to claim the code first, permanently diverting all future referral rewards tied to that code to themselves.

### Finding Description
`registerCode` performs a simple availability check and then unconditionally assigns `msg.sender` as the owner of the code: [1](#0-0) 

Because `_code` is a caller-supplied parameter visible in the mempool before inclusion, any address can observe a legitimate user's (e.g., an influencer's well-known/vanity code) pending `registerCode` call and front-run it with the identical `_code` value and higher gas, causing `codeOwners[_code] = msg.sender` to be set to the attacker instead of the intended owner. The original registrant's transaction then reverts with `CodeOccupied()` [2](#0-1) , permanently losing that identifier since there is no re-assignment path available to a normal user (only `onlyOwner` can call `forceSetCodeOwner`) [3](#0-2) .

Once the attacker owns the code, every referee who later calls `useCode(_code)` becomes permanently bound to the attacker as their referrer via `myReferer[msg.sender] = codeOwners[_code]` (referral bindings cannot be changed once set, per the `HasReferral()` check) [4](#0-3) . From that point on, `trigger()` (called by `MasterMagpie` whenever a referee claims MGP rewards) routes the referrer share of `_amount` to the attacker's `userInfos[_referer].rewardAmount` instead of the legitimate operator: [5](#0-4) . The attacker can then withdraw these accrued rewards directly via `claimReward()` [6](#0-5) .

### Impact Explanation
This results in theft of unclaimed referral yield: all MGP reward share that should have accrued to the legitimate code owner (a percentage of every referee's claimed reward, boosted further by `totalBoostFactor`/`vlMGP` lock size) is instead redirected to and withdrawable by the attacker for as long as the code remains bound to referees, which can be indefinite. This satisfies the "theft or permanent freezing of unclaimed yield" impact bar, entirely through an ordinary wallet transaction with no privileged role required.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to monitor the mempool for a `registerCode` call using a specific, presumably valuable/recognizable code (e.g. tied to a marketing campaign) and win a gas-price race, which is a well-understood and cheap MEV/front-running technique requiring no special access.

### Recommendation
Bind the code to the intended owner using a commit-reveal scheme, or require the caller to sign the code claim off-chain (validated via `ECDSA.recover`) so a front-runner cannot simply resubmit the same calldata. Alternatively, allow `registerCode` to only be called once per whitelisted/verified address by the protocol owner, or make codes derived deterministically from `msg.sender` (e.g., a hash of the address) so they cannot be squatted by third parties.

### Proof of Concept
1. Alice (a well-known referrer) broadcasts `registerCode("ALICE001")`.
2. Bob observes this pending transaction in the mempool and sends `registerCode("ALICE001")` with a higher gas price.
3. Bob's transaction mines first: `codeOwners["ALICE001"] = Bob` [7](#0-6) .
4. Alice's original transaction reverts with `CodeOccupied()`.
5. Users who trust Alice's advertised code call `useCode("ALICE001")`, binding themselves to Bob as referrer.
6. When these referees claim rewards via `MasterMagpie`, `trigger()` credits the referrer share to Bob's `userInfos[Bob].rewardAmount`, which Bob withdraws via `claimReward()`.

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

**File:** rewards/ReferralStorage.sol (L208-217)
```text
    function forceSetCodeOwner(bytes32 _code, address _newAccount) external override onlyOwner {
        if (_code == bytes32(0)) revert InvalidCode();

        address previousOwner = codeOwners[_code];
        codeOwners[_code] = _newAccount;

        userInfos[previousOwner].myCode = bytes32(0); // Clear the code for previous owner
        userInfos[_newAccount].myCode = _code; // Update the code for new owner
        emit ForceSetCodeOwner(_code, _newAccount);
    }
```
