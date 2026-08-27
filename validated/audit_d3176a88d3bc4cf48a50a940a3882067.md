### Title
Cost-free `cancelUnlock` erases cooldown history, letting users zero out `_calExpireForfeit` on historically-accrued vlMGP rewards - (File: VLMGP.sol / rewards/vlMGPBaseRewarder.sol)

### Summary
`VLMGP.cancelUnlock` zeroes a cooldown slot's `amountInCoolDown` without first settling/harvesting rewards at the forfeiture rate that applied while the tokens were in cooldown, unlike `unlock()` which calls `multiclaimFor` before mutating the slot. Because `vlMGPBaseRewarder._calExpireForfeit` only looks at the *current* snapshot of `VLMGP.getRewardablePercentWAD(_account)` at claim time, and `getUserTotalLocked`/`getUserAmountInCoolDown` are derived purely from live slot state, a user can call `cancelUnlock` on all active cooldown slots immediately before `MasterMagpie.multiclaimFor` and receive 100% of `unClaimedMgp`/accrued MGP with zero forfeiture, regardless of how long that MGP accrued while nominally in cooldown.

### Finding Description
- `VLMGP.getRewardablePercentWAD` (VLMGP.sol:193-218) computes the rewardable percentage from live state: `fullyInLock = getUserTotalLocked(_user)` and `inCoolDown = getUserAmountInCoolDown(_user)`, both read directly from `userUnlockings[_user]` at call time. [1](#0-0) 
- `getUserTotalLocked` is `_amountInMasterMagpie - getUserAmountInCoolDown(_user)`, where `_amountInMasterMagpie` (the staked `user.amount` in `MasterMagpie`) is unaffected by entering/canceling cooldown — only `unlock()`/`forceUnLock()` call `withdrawVlMGPFor` to actually reduce it. [2](#0-1) 
- `cancelUnlock` simply does `totalAmountInCoolDown -= slot.amountInCoolDown; slot.amountInCoolDown = 0;` with **no** call to `multiclaimFor`/harvest beforehand, unlike `unlock()` (VLMGP.sol:325-328) which force-claims before finalizing the slot. [3](#0-2) 
- Because the slot is wiped instantly and for free (no penalty, unlike `forceUnLock`), immediately after `cancelUnlock` the account's `inCoolDown` becomes 0 and `fullyInLock` becomes 100% of `_amountInMasterMagpie`, so `getRewardablePercentWAD` returns `1e18` (100%) regardless of how long or how much of the underlying MGP was accrued while previously in cooldown.
- `vlMGPBaseRewarder._calExpireForfeit` and `queueMGP`/`_sendReward` apply this live 100% rate to the entire pending amount (`unClaimedMgp` plus newly accrued MGP calculated in `MasterMagpie._multiClaim`), producing `forfeitAmount = 0`. [4](#0-3) [5](#0-4) 
- `MasterMagpie._multiClaim` (reached via the externally-callable `multiclaimFor`) computes `claimableMgp` from the user's total staked amount and calls `_sendMGPForVlMGPPool` → `IvlmgpPBaseRewarder(vlMGPRewarder).queueMGP(...)`, so the entire historical unclaimed balance is paid out using the manipulated 100% rate. [6](#0-5) [7](#0-6) 

No modifier (`onlyManager`, `nonReentrant`, pausable checks) prevents this because the attacker never needs manager privileges — they call their own `cancelUnlock` and then their own `multiclaimFor`, both public/external functions available to any staked account. The forfeit accounting is fundamentally stateless/point-in-time rather than accruing forfeiture proportionally as time passes in cooldown, so it can always be reset to zero cost by exiting cooldown before the claim executes.

### Impact Explanation
The forfeited MGP is not lost by the protocol treasury directly, but is meant to be redistributed to all other vlMGP stakers via `_queueNewRewardsWithoutTransfer(forfeitAmount, MGP)` in `vlMGPBaseRewarder` (queueMGP/`_sendReward`). By zeroing forfeiture at will, any staker can divert MGP that should have flowed to the honest, continuously-locked stakers' reward pool back to themselves in full. This is a **theft of unclaimed yield from other users** (the honest vlMGP holder pool), matching the "theft ... of unclaimed yield" Immunefi impact class, repeatable indefinitely by any account that uses cooldown slots. [8](#0-7) 

### Likelihood Explanation
- No special privileges, capital, or flash loans are required — only an existing vlMGP position with an open cooldown slot.
- `cancelUnlock` is `whenNotPaused` only, no cooldown restriction on repeated calls, and no penalty is applied (unlike `forceUnLock`). [3](#0-2) 
- The exploit is trivially repeatable each time the user wants to claim while any cooldown slot exists, and works whether executed atomically in one transaction (via a helper contract) or as two sequential transactions in the same block/near-block, since nothing forces settlement between `startUnlock`/time passing and `cancelUnlock`.
- The only friction is the `< 0.1%` ignore threshold in `_calExpireForfeit`, easily exceeded for any meaningfully-sized position/time-in-cooldown.

### Recommendation
Settle/crystallize the forfeiture at the moment the cooldown state changes rather than relying on a live snapshot at claim time:
- Make `cancelUnlock` call `IMasterMagpie(masterMagpie).multiclaimFor(...)` (as `unlock()`/`startUnlock()` already do) **before** zeroing `slot.amountInCoolDown`, so any pending `unClaimedMgp` is settled and forfeited at the correct in-cooldown rate prior to the slot being wiped.
- Alternatively, redesign `getRewardablePercentWAD`/`_calExpireForfeit` to track forfeiture cumulatively per unit of MGP accrued over time (e.g., checkpoint-based accrual weighted by cooldown status at each accrual interval) instead of applying a single live-state percentage to the entire historical unclaimed balance.

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, `VLMGP`, `vlMGPBaseRewarder`, MGP token; configure pools as in existing test setup.
2. User locks MGP via `VLMGP.lock`, then calls `startUnlock(amount)` to place all/most tokens into a cooldown slot (this internally claims pending rewards to reset `unClaimedMgp` to 0 at t0).
3. Advance time substantially (e.g., 50% of `coolDownInSecs`) so MGP continues to accrue in `MasterMagpie` against the user's unchanged `user.amount`, while `getRewardablePercentWAD` would (absent manipulation) attribute a `still in cool down` weighting of `amountInCoolDown * 1e18 / userTotalVlmgp` (i.e., partial/likely near-zero rewardable percent since `fullyInLock` is small relative to `inCoolDown`).
4. **Baseline run:** call `MasterMagpie.multiclaimFor` directly without canceling — record `forfeitAmount` emitted in `MGPHarvested` and MGP actually transferred to receiver; assert `forfeitAmount > 0`.
5. **Exploit run (fresh state):** repeat steps 2-3, then in the same transaction (or immediately before) call `VLMGP.cancelUnlock(slotIndex)` for the slot, then call `MasterMagpie.multiclaimFor` for the same staking token.
6. Assert: `forfeitAmount == 0` (or below the 0.1% ignore threshold) and the user receives the full pending MGP, in contrast to the baseline's nonzero forfeiture — demonstrating the forfeiture mechanism was fully bypassed at zero cost via `cancelUnlock`.

### Citations

**File:** VLMGP.sol (L125-144)
```text
    function getUserTotalLocked(address _user) override public view returns (uint256 _lockAmount) {
        // needs fixing
        (uint256 _amountInMasterMagpie, ) = IMasterMagpie(masterMagpie).stakingInfo(address(this), _user);
        _lockAmount = _amountInMasterMagpie - getUserAmountInCoolDown(_user);
    }

    function getUserAmountInCoolDown(address _user)
        override
        public
        view
        returns (uint256)
    {
        uint256 length = getUserUnlockSlotLength(_user);
        uint256 totalCoolDownAmount = 0;
        for (uint256 i; i < length; i++) {
            totalCoolDownAmount += userUnlockings[_user][i].amountInCoolDown;
        }

        return totalCoolDownAmount;
    }    
```

**File:** VLMGP.sol (L193-218)
```text
    function getRewardablePercentWAD(address _user) override public view returns(uint256 percent) {
        uint256 fullyInLock = getUserTotalLocked(_user);
        uint256 inCoolDown = getUserAmountInCoolDown(_user);
        uint256 userTotalVlmgp = fullyInLock + inCoolDown;
        if (userTotalVlmgp == 0)
            return 0;
        percent = fullyInLock * 1e18 / userTotalVlmgp;

        uint256 timeNow = block.timestamp;
        UserUnlocking[] storage userUnlocking = userUnlockings[_user];

        for (uint256 i; i < userUnlocking.length; i++) {
            if (userUnlocking[i].amountInCoolDown > 0) {
                if (block.timestamp > userUnlocking[i].endTime) {// fully unlocked 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 * (userUnlocking[i].endTime - userUnlocking[i].startTime)
                        / userTotalVlmgp / (timeNow - userUnlocking[i].startTime);
                }
                else {// still in cool down 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 / userTotalVlmgp;
                }

            }
        }

        return percent;
    }
```

**File:** VLMGP.sol (L339-349)
```text
    function cancelUnlock(uint256 _slotIndex) external override whenNotPaused {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        _checkInCoolDown(msg.sender, _slotIndex);

        totalAmountInCoolDown -= slot.amountInCoolDown; // reduce amount to cool down accordingly
        slot.amountInCoolDown = 0; // not in cool down anymore

        emit ReLock(msg.sender, _slotIndex, slot.amountInCoolDown);
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L274-289)
```text
    function queueMGP(uint256 _amount, address _account, address _receiver) override external onlyManager nonReentrant returns (bool) {
        IERC20(vlMGP.MGP()).safeTransferFrom(msg.sender, address(this), _amount);
        
        uint256 forfeitAmount = _calExpireForfeit(_account, _amount);
        uint256 rewardableAmount = _amount - forfeitAmount;
        
        if (forfeitAmount > 0)
            _queueNewRewardsWithoutTransfer(forfeitAmount, address(vlMGP.MGP()));

        if (rewardableAmount > 0) {
            IERC20(vlMGP.MGP()).safeTransfer(_receiver, rewardableAmount);
            emit MGPHarvested(_account, rewardableAmount, forfeitAmount);
        }

        return true;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L386-400)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
        uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
        if (rewardableAmount > _amount)
            revert InvalidRewardableAmount();

        uint256 forfeitAmount = _amount - rewardableAmount;
        
        if (forfeitAmount < (_amount / 1000)) {  // if forfeitAmount is smaller than 0.1% ignore to save gas fee
            forfeitAmount = 0;
            rewardableAmount = _amount;
        }

        return forfeitAmount;
    }
```

**File:** rewards/MasterMagpie.sol (L536-566)
```text
    function _multiClaim(address[] calldata _stakingTokens, address _user, address _receiver, address[][] memory _rewardTokens) internal nonReentrant {
        uint256 length = _stakingTokens.length;
        if (length != _rewardTokens.length) revert LengthMismatch();

        uint256 vlMGPPoolAmount;
        uint256 mWOmPoolAmount;
        uint256 defaultPoolAmount;

        for (uint256 i = 0; i < length; ++i) {
            address _stakingToken = _stakingTokens[i];
            UserInfo storage user = userInfo[_stakingToken][_user];
            
            updatePool(_stakingToken);
            uint256 claimableMgp = _calNewMGP(_stakingToken, _user) + unClaimedMgp[_stakingToken][_user];

            if (_stakingToken == address(vlmgp)) {
                vlMGPPoolAmount += claimableMgp;
            } else if (MPGRewardPool[_stakingToken]) {
                mWOmPoolAmount += claimableMgp;
            } else {
                defaultPoolAmount += claimableMgp;
            }

            unClaimedMgp[_stakingToken][_user] = 0;
            user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
            _claimBaseRewarder(_stakingToken, _user, _receiver, _rewardTokens[i]);
        }

        if (vlMGPPoolAmount > 0) {
            _sendMGPForVlMGPPool(_user, _receiver, vlMGPPoolAmount);
        }
```

**File:** rewards/MasterMagpie.sol (L638-644)
```text
    function _sendMGPForVlMGPPool(address _account, address _receiver, uint256 _amount) internal {
        address vlMGPRewarder = tokenToPoolInfo[address(vlmgp)].rewarder;
        IERC20(mgp).safeApprove(vlMGPRewarder, _amount);
        IvlmgpPBaseRewarder(vlMGPRewarder).queueMGP(_amount, _account, _receiver);

        emit HarvestMGP(_account, _receiver, _amount, false);
    }
```
