### Title
Attacker can externally inflate `mWomSV` locked balance via `mWomSV.lockFor()` to fraudulently jump `ArbWomUp3.getUserTier()`/`calDoubledCounted()` and drain the MGP reward pool disproportionate to WOM actually routed through the contract - ([File: wombat/ArbWomUp3.sol])

### Summary
`ArbWomUp3.getUserTier()` and `calDoubledCounted()` compute reward tiers purely from `mWomSV.getUserTotalLocked(_account)`, a state variable that any unprivileged holder of `mWom` can freely manipulate by calling `mWomSV.lockFor(_amount, _for)` directly, bypassing `ArbWomUp3` entirely. Because `getRewardAmount()`'s non-lock branch (`_mode != 2`) multiplies the *full* `_amount` deposited by `rewardMultiplier[getUserTier(_account)]` with no bracket/accumulator bound (unlike the `_lock` branch, which is bracket-bound via `calDoubledCounted`), an attacker can cheaply reach a top tier via `mWomSV.lockFor` and then repeatedly call `incentiveDeposit` with `_mode == 1` (or 3) and large `_amount` to extract MGP at the top-tier multiplier for amounts that were never bracket-tracked, draining the unclaimed MGP reward pool.

### Finding Description
`mWomSV.lockFor()` is a fully public, unrestricted function: [1](#0-0) 
Any address holding `mWom` tokens can call it to credit an arbitrary `_for` address's locked balance in `mWomSV`, completely independent of `ArbWomUp3`.

`ArbWomUp3.getUserTier()` and `calDoubledCounted()` both read this externally-controllable value directly: [2](#0-1) 

`getRewardAmount()` has two branches. When `_lock == false` (i.e., `_mode != 2` in `incentiveDeposit`, e.g. stake mode `_mode == 1`), the reward is computed as a flat multiplication of the *entire* deposited `_amount` by the tier multiplier, with **no bracket ladder and no subtraction of previously rewarded amounts** (unlike the `_lock == true` branch, which uses `calDoubledCounted` to avoid double counting): [3](#0-2) 

Exploit flow:
1. Attacker acquires enough `mWom`/`WOM` to reach the top `rewardTier` threshold (a one-time, comparatively small deposit — since only the threshold amount needs to be locked once, not repeated per claim).
2. Attacker calls `mWomSV.lockFor(thresholdAmount, attacker)` directly, bypassing `ArbWomUp3`'s `_deposit` accounting entirely. This inflates `mWomSV.getUserTotalLocked(attacker)`.
3. Attacker calls `ArbWomUp3.incentiveDeposit(_amount, _convertRatio, false, 1)` (stake mode) with an arbitrarily large `_amount` of WOM. `_lock` evaluates false, so `getRewardAmount` computes `mgpReward = _amount * rewardMultiplier[getUserTier(attacker)] / DENOMINATOR`, where `getUserTier(attacker)` now returns the top tier due to the externally-inflated `mWomSV` balance.
4. Because this branch has no per-account cumulative tracking (no equivalent of `userWOMDeposited` used by `ArbWomUp2`), the attacker can repeat step 3 with additional large `_amount` values, each time getting the top-tier multiplier applied to the full new `_amount`, capped only by `IERC20(mgp).balanceOf(address(this))`.

Existing checks (`whenNotPaused`, `nonReentrant`, `_checkAmount`) do not prevent this because the flaw is a logic/accounting flaw, not a reentrancy or access-control issue — `mWomSV.lockFor` is intentionally public (it's meant to be called by other contracts like `ArbWomUp3` itself), and nothing ties tier eligibility to `ArbWomUp3`-routed volume.

### Impact Explanation
This allows theft of the unclaimed MGP reward pool held by `ArbWomUp3` (via `vlMGP.lockFor(rewardToSend, msg.sender)`), disproportionate to actual WOM volume converted through the contract. This matches the "theft of unclaimed yield / reward pool" impact class — the attacker extracts far more MGP than warranted by their true, contract-tracked deposit tier, and can repeat the extraction until the MGP balance is drained, since the `_lock == false` reward path is not bracket-bound or accumulator-bound per account.

### Likelihood Explanation
This requires only:
- Acquiring/locking enough `mWom` once to cross a favorable tier threshold via the fully public `mWomSV.lockFor()`.
- Calling `incentiveDeposit` with `_mode == 1` (or the `else` mode 3) and a large `_amount`, repeatable indefinitely as long as MGP balance remains.

No privileged role, oracle manipulation, or governance action is needed — purely unprivileged EOA/contract calls to two public functions. The only capital requirement is the one-time tier threshold lock, which is far smaller than the drainable reward, making this highly feasible and repeatable.

### Recommendation
- Do not use `mWomSV.getUserTotalLocked()` (an externally-manipulable global state) as the sole basis for `ArbWomUp3`'s tier/reward calculations. Instead, track deposits/lock amounts routed specifically through `ArbWomUp3` (similar to `userWOMDeposited` in `ArbWomUp`/`ArbWomUp2`) and use that internal accounting for `getUserTier`, `calDoubledCounted`, and the non-lock reward branch.
- Apply the same bracket-ladder + `calDoubledCounted`-style accumulator logic to the `_lock == false` reward branch so a single large deposit cannot draw the top-tier multiplier on its full amount without correspondingly having crossed each intermediate bracket.
- Consider restricting `mWomSV.lockFor`'s `_for` parameter usage or explicitly documenting/asserting that reward-eligible balances must originate from `ArbWomUp3`-mediated locks only (e.g., an internal counter incremented in `ArbWomUp3._deposit` rather than reading global `mWomSV` state).

### Proof of Concept
Foundry test plan:
1. Deploy `mWomSV`, `ArbWomUp3`, mock `mWom`/`WOM`/`mgp`/`vlMGP`/`smartWomConvert`, set `rewardTier` and `rewardMultiplier` arrays (e.g., tier0=0, tier1=1000e18 with multiplier0=100, multiplier1=5000).
2. Fund `ArbWomUp3` with a large MGP balance to simulate the reward pool.
3. Attacker mints/obtains `1000e18` `mWom`, approves `mWomSV`, calls `mWomSV.lockFor(1000e18, attacker)` directly (bypassing `ArbWomUp3`).
4. Assert `ArbWomUp3.getUserTier(attacker) == 1` (top tier) even though `attacker` has deposited `0` WOM through `ArbWomUp3`.
5. Attacker calls `ArbWomUp3.incentiveDeposit(largeAmount, 0, false, 1)` with `largeAmount` WOM (mode 1, stake, non-lock branch).
6. Assert `rewardToSend == largeAmount * rewardMultiplier[1] / DENOMINATOR`, and that this value is far greater than `largeAmount * rewardMultiplier[trueTier=0] / DENOMINATOR` (the reward a normal user with the same actual ArbWomUp3-routed WOM would receive).
7. Repeat step 5 multiple times to show the MGP pool balance decreasing each call with no cap tied to attacker's true cumulative ArbWomUp3 deposits, until `IERC20(mgp).balanceOf(address(ArbWomUp3))` is drained.

### Citations

**File:** wombat/mWomSV.sol (L232-240)
```text
    // @notice lock mWom in the contract
    // @param _amount the amount of mWom to lock
    // @param _for the address to lcock for
    // @dev the tokens will be taken from msg.sender
    function lockFor(uint256 _amount, address _for) override external whenNotPaused nonReentrant {
        _lock(msg.sender, _for, _amount);

        emit NewLock(_for, block.timestamp, _amount);
    }
```

**File:** wombat/ArbWomUp3.sol (L107-129)
```text
    function getRewardAmount(uint256 _amountToConvert, address _account, bool _lock) external view returns (uint256) {
        uint256 mgpReward = 0;

        if (!_lock) {
            mgpReward = _amountToConvert * rewardMultiplier[getUserTier(_account)] / DENOMINATOR;
        } else {
            uint256 accumulated = _amountToConvert + mWomSV.getUserTotalLocked(_account);
            uint256 rewardAmount = 0;
            uint256 i = 1;

            while (i < rewardTier.length && accumulated > rewardTier[i]) {
                rewardAmount +=
                    (rewardTier[i] - rewardTier[i - 1]) *
                    rewardMultiplier[i - 1];
                i++;
            }
            rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1];
            mgpReward = (rewardAmount / DENOMINATOR) - calDoubledCounted(_account);
        }

        uint256 mgpleft = IERC20(mgp).balanceOf(address(this));
        return mgpReward > mgpleft ? mgpleft : mgpReward;
    }
```

**File:** wombat/ArbWomUp3.sol (L131-155)
```text
    function calDoubledCounted(address _account) public view returns (uint256) {
        uint256 accuIn1 = mWomSV.getUserTotalLocked(_account);
        uint256 rewardAmount = 0;
        uint256 i = 1;
        while (i < rewardTier.length && accuIn1 > rewardTier[i]) {
            rewardAmount +=
                (rewardTier[i] - rewardTier[i - 1]) *
                rewardMultiplier[i - 1];
            i++;
        }

        rewardAmount += (accuIn1 - rewardTier[i - 1]) * rewardMultiplier[i - 1];
        return rewardAmount / DENOMINATOR;
    }    

    function getUserTier(address _account) public view returns (uint256) {
        uint256 userMWOMSVBal = mWomSV.getUserTotalLocked(_account);
        for (uint256 i = tierLength - 1; i >= 1; i--) {
            if (userMWOMSVBal >= rewardTier[i]) {
                return i;
            }
        }

        return 0;
    }
```
