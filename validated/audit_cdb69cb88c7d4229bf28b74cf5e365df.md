### Title
`_calExpireForfeit` in `mWOMSVBaseRewarder` never applies mWomSV's rewardable-percent discount, allowing unlocking/unlocked holders to claim 100% of bonus rewards with zero forfeiture - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` sets `rewardableAmount = _amount` unconditionally and never consults any lock-percentage function on `mWOMSV`, unlike the sibling `vlMGPBaseRewarder._calExpireForfeit`, which multiplies by `vlMGP.getRewardablePercentWAD(_account)` before computing the forfeit. As a result, `forfeitAmount` computed in `_sendReward` is always `0`, so any mWomSV holder — including one in cooldown or fully unlocked — receives their full accrued bonus reward through `getReward`/`getRewards`, and nothing is ever redistributed to fully-locked holders via `_queueNewRewardsWithoutTransfer`.

### Finding Description
`_calExpireForfeit` in `mWOMSVBaseRewarder.sol` is: [1](#0-0) 

Compare to the analogous, presumably-correct logic in `vlMGPBaseRewarder.sol`, which scales `rewardableAmount` by `vlMGP.getRewardablePercentWAD(_account)`: [2](#0-1) 

Two structural facts corroborate that this is a genuine defect rather than an intentional design difference:
1. `mWOMSV` is declared and typed as `ILocker`, whose interface only exposes `lockFor(uint256,address)` — there is no `getRewardablePercentWAD` in the interface at all: [3](#0-2) [4](#0-3) 
2. `wombat/mWomSV.sol` does implement a `getRewardablePercentWAD` function internally (confirmed via search), meaning the concept of a rewardable percentage for cooldown/unlocked mWomSV exists in the protocol but is simply not wired into the rewarder's forfeiture calculation, and cannot be reached through the `ILocker` interface as currently defined.

Because `rewardableAmount = _amount` always, `forfeitAmount = _amount - rewardableAmount = 0` unconditionally (the dead check `if (rewardableAmount > _amount) revert InvalidRewardableAmount();` can never trigger). In `_sendReward`, this means `toSend == userRewards[_rewardToken][_account]` always, and `_queueNewRewardsWithoutTransfer` is never invoked with any meaningful amount: [5](#0-4) 

Exploit path: an attacker acquires mWomSV, holds it long enough to accrue bonus rewards via `queueNewRewards`/`donateRewards` reward-per-token accrual, then calls `unlock`/`startUnlock` on `mWomSV` (or is already fully unlocked/in cooldown) and calls `MasterMagpie.multiclaim` → `mWOMSVBaseRewarder.getReward`/`getRewards`. No modifier (`onlyMasterMagpie`, `updateReward`, `nonReentrant`) prevents this, since these checks only gate authorization/reentrancy, not the forfeiture math itself. The attacker receives their entire accrued bonus reward, when the intended design (mirrored by `vlMGPBaseRewarder`) should discount it by their unlocked/cooldown share and redirect the forfeited portion to fully-locked stakers.

### Impact Explanation
This causes theft/misappropriation of unclaimed protocol-distributed yield: fully-locked mWomSV holders are the intended beneficiaries of forfeited rewards from cooling-down/unlocked holders (as implemented correctly in `vlMGPBaseRewarder`), but here that forfeiture mechanism is completely inert. Any unlocking/unlocked holder captures 100% of the reward pool's bonus token yield attributable to their balance, directly at the expense of long-term lockers who should have received the redistributed forfeit share. This matches Immunefi's "theft of unclaimed yield" impact class.

### Likelihood Explanation
Preconditions are trivial and require no special privilege: any address that holds mWomSV (obtainable by staking/locking WOM through normal, permissionless flows), lets rewards accrue, and then unlocks/cools down before claiming will trigger the bug every time. No capital beyond normal staking is needed, and the exploit is fully repeatable for every reward distribution cycle — this is a deterministic contract-logic bug, not a timing race.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder`: retrieve the account's rewardable percentage from `mWomSV` (e.g., `mWomSV.getRewardablePercentWAD(_account)`) and compute `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before deriving `forfeitAmount`. This requires either changing the `mWOMSV` state variable's type from `ILocker` to a locker interface that exposes `getRewardablePercentWAD` (e.g., an `IMWomSV`/`ILockerV2` interface matching `wombat/mWomSV.sol`'s actual implementation), or extending `ILocker` with that method.

### Proof of Concept
Foundry test plan:
1. Deploy `mWomSV`, `mWOMSVBaseRewarder`, `MasterMagpie` (or mocks satisfying `IMasterMagpie.stakingInfo`), and a mock reward token.
2. Have `attacker` lock WOM to mint mWomSV, then have `rewardManager` call `queueNewRewards` to accrue bonus reward per token.
3. Call `earned(attacker, rewardToken)` and record `earnedBefore`.
4. Have `attacker` call `unlock`/`startUnlock` on `mWomSV` to enter cooldown/fully-unlocked state, and independently query `mWomSV.getRewardablePercentWAD(attacker)`, asserting it is `< 1e18`.
5. Call `mWOMSVBaseRewarder.calExpireForfeit(attacker, rewardToken)` and assert `forfeitAmount == 0` despite `getRewardablePercentWAD < 1e18` — demonstrating the bug directly.
6. Call `getReward(attacker, attacker)` via `MasterMagpie.multiclaim` (or directly if test bypasses `onlyMasterMagpie`), and assert the amount transferred to `attacker` equals the full `earnedBefore` amount rather than `earnedBefore * rewardablePercentWAD / 1e18`.
7. Assert no forfeited amount was queued via `_queueNewRewardsWithoutTransfer` (e.g., check `ForfeitRewardAdded` event was not emitted, or `rewards[rewardToken].rewardPerTokenStored` unchanged post-claim aside from the attacker's own accrual), confirming fully-locked holders received zero redistribution that they should have received under the vlMGP-equivalent logic.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L22-22)
```text
    ILocker public mWOMSV; 
```

**File:** rewards/mWOMSVBaseRewarder.sol (L362-376)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver) internal {
        uint256 forfeitAmount = _calExpireForfeit(_account, userRewards[_rewardToken][_account]);
        uint256 toSend = userRewards[_rewardToken][_account] - forfeitAmount;


        userRewards[_rewardToken][_account] = 0;
            
        if (toSend > 0) {
            IERC20(_rewardToken).safeTransfer(_receiver, toSend);
            emit RewardPaid(_account, _receiver, toSend, _rewardToken);
        }

        if(forfeitAmount > 0)
            _queueNewRewardsWithoutTransfer(forfeitAmount, _rewardToken);
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L385-398)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardableAmount = _amount;
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

**File:** interfaces/ILocker.sol (L1-6)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface ILocker {
    function lockFor(uint256 _amount, address _for) external;
}
```
