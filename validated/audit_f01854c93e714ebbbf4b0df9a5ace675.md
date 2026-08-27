Confirmed: `_lock` in `VLMGP.sol` calls `IReferralStorage(referralStorage).updateTotalFactor(_for)` on every lock, which sets `userInfo.factor` for the code owner (attacker) and adds it to `totalBoostFactor`. [1](#0-0) 

The critical gap is confirmed in `forceSetCodeOwner`, which only clears `myCode` but never touches `factor`/`totalBoostFactor`, and never migrates the `myReferer` mapping of existing referees to the new owner.

### Title
Stale boost factor and orphaned referee links in `forceSetCodeOwner` allow ex-code-owner to keep siphoning boosted referral yield - (rewards/ReferralStorage.sol)

### Summary
`ReferralStorage.forceSetCodeOwner` reassigns `codeOwners[_code]` and clears/sets `myCode` for the previous and new owner, but never zeroes the previous owner's `factor`/`totalBoostFactor` contribution and never updates the `myReferer` mapping of referees who already used the code. As a result, the original attacker continues to receive both base-tier and boosted referral rewards from `trigger()` calls for pre-existing referees indefinitely after ownership is reassigned, permanently diverting yield that should accrue to the new code owner/referral base.

### Finding Description
An attacker calls `registerCode(_code)` [2](#0-1)  and then locks MGP in `VLMGP`, which triggers `_lock` → `updateTotalFactor(attacker)`, setting `userInfo.factor = sqrt(lockedAmount)` and adding it to `totalBoostFactor` [1](#0-0) [3](#0-2) . Meanwhile other users call `useCode(_code)`, which permanently sets `myReferer[user] = codeOwners[_code]` (the attacker) at that point in time [4](#0-3) .

When the owner later calls `forceSetCodeOwner(_code, victim)` to reassign the code, it updates `codeOwners[_code]` and the `myCode` field of both accounts, but does not touch `userInfos[previousOwner].factor`, `totalBoostFactor`, or the `myReferer` mapping of any referee who already called `useCode`: [5](#0-4) .

Consequently:
1. `totalBoostFactor` still includes the attacker's stale factor, and `userInfos[attacker].factor` is never reset, so `_calBoosted(attacker)` still returns a nonzero value [6](#0-5) .
2. Every existing referee's `myReferer[referee]` mapping still points to the attacker's address (it was fixed at `useCode` time and is never migrated). When `masterMagpie` calls `trigger(_referee, _amount)` for those referees, `_referer = myReferer[_referee]` resolves to the attacker, so `refererInfo.rewardAmount` (including the boosted percentage) continues to accrue to the attacker's `UserInfo`, not the victim's [7](#0-6) .

No modifier, reentrancy guard, or accounting check prevents this because the state is simply never migrated/cleared on reassignment; the attacker performs no privileged action after the owner's routine `forceSetCodeOwner` call.

### Impact Explanation
This is a theft of unclaimed yield: after a legitimate `forceSetCodeOwner` reassignment (e.g., resolving a dispute or handing a code to its rightful owner), the intended new owner (victim) never actually receives referral/boost rewards from the referees who signed up before the reassignment, and the denominator (`totalBoostFactor`) inflation dilutes the boosted share of every other legitimate code owner. The attacker keeps collecting `refererAmount` (base tier % + boosted %) via `trigger()` indefinitely, with no way for the contract owner to stop it short of also manually adjusting `factor`/`totalBoostFactor`/`myReferer`, none of which is exposed. This matches "theft or permanent freezing of unclaimed yield" from the intended new code owner/referral base.

### Likelihood Explanation
Fully feasible with no special privileges: the attacker only needs to call `registerCode` and `lock` (both public/unprivileged), and get some users to `useCode` their referral code (normal product usage). The only external trigger required is the owner calling `forceSetCodeOwner`, which is a routine, intended admin action (not malicious admin behavior) — the bug is that this legitimate call fails to fully migrate state. Once triggered, the leak is permanent and repeatable for every referee already linked to the attacker, and requires no further action from the attacker.

### Recommendation
In `forceSetCodeOwner`, also: (1) subtract `userInfos[previousOwner].factor` from `totalBoostFactor` and zero out `userInfos[previousOwner].factor` (mirroring the accounting done in `updateTotalFactor`), and (2) migrate/re-point all existing referees' `myReferer` entries (or `myReferees[previousOwner]`) to `_newAccount`, or alternatively resolve the referer dynamically via `codeOwners[userInfos[referee].codeIUsed]` at `trigger()` time instead of caching it in `myReferer` at `useCode()` time.

### Proof of Concept
Foundry test outline:
1. Deploy `ReferralStorage`, `VLMGP`, `MGP` token; wire `vlMGP` and `masterMagpie` addresses.
2. Attacker calls `registerCode(codeA)`, acquires MGP, approves, and calls `VLMGP.lock(amount)` → asserts `userInfos[attacker].factor > 0` and `totalBoostFactor == userInfos[attacker].factor`.
3. A separate `referee` account calls `useCode(codeA)` → assert `myReferer[referee] == attacker`.
4. Owner calls `forceSetCodeOwner(codeA, victim)` → assert `codeOwners[codeA] == victim`, `userInfos[attacker].myCode == 0`, `userInfos[victim].myCode == codeA`.
5. Assert (bug confirmation): `userInfos[attacker].factor` is still `> 0` and `totalBoostFactor` is unchanged (still includes attacker's stale factor); `myReferer[referee]` is still `attacker`, not `victim`.
6. Simulate `masterMagpie` calling `trigger(referee, amount)` → assert `userInfos[attacker].rewardAmount` increases (including boosted portion) while `userInfos[victim].rewardAmount` remains `0`, proving the victim never receives the reassigned code's referral/boost yield.

### Citations

**File:** VLMGP.sol (L461-470)
```text
    function _lock(
        address spender,
        address _for,
        uint256 _amount
    ) internal {
        MGP.safeTransferFrom(spender, address(this), _amount);
        IMasterMagpie(masterMagpie).depositVlMGPFor(_amount, _for);
        totalAmount += _amount; // trigers update pool share, so happens after toal amount increase
        if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(_for);
    }
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

**File:** rewards/ReferralStorage.sol (L243-246)
```text
    function _calBoosted(address _account) private view returns(uint256) {
        if (totalBoostFactor == 0) return 0;
        return BoostPoint * userInfos[_account].factor / totalBoostFactor;
    }
```
