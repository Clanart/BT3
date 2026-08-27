### Title
`calExpireForfeit`/`_calExpireForfeit` always returns 0, permanently bypassing reward forfeiture and diverting yield owed to the forfeit pool - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
The internal `_calExpireForfeit` function in `mWOMSVBaseRewarder.sol` sets `rewardableAmount = _amount` and then computes `forfeitAmount = _amount - rewardableAmount`, which is algebraically `0` for every input, independent of any lock/cooldown/unlock state. Both the public view `calExpireForfeit` and the internal reward-distribution path `_sendReward` rely on this broken calculation, so forfeiture never occurs and 100% of `userRewards` is always sent to the claimer instead of the intended partial amount being routed to `_queueNewRewardsWithoutTransfer` for redistribution to remaining stakers.

### Finding Description
`calExpireForfeit` is a thin wrapper that calls `_calExpireForfeit(_account, earned(_account, _rewardToken))`: [1](#0-0) 

The core logic is: [2](#0-1) 

`rewardableAmount` is initialized equal to `_amount` and never modified by any external call (no reference to lock/cooldown state, no call into `mWOMSV`/`ILocker`, no reference to `_account`'s unlocking schedule). Consequently `forfeitAmount = _amount - rewardableAmount` is always exactly `0`, and the subsequent `if (forfeitAmount < (_amount / 1000))` branch always triggers as well (0 < anything), reinforcing `forfeitAmount = 0`. This makes the function a dead/no-op forfeiture check — the invariant "forfeiture reflects lock/expiry state" is unreachable for any input, as the question hypothesizes.

The same broken helper is used in the actual payout path: [3](#0-2) 

Since `forfeitAmount` is always `0`, `toSend` always equals the full `userRewards[_rewardToken][_account]`, and `_queueNewRewardsWithoutTransfer` (which would redistribute forfeited yield back into `rewardPerTokenStored` for other stakers) is never invoked with a non-zero amount.

No modifier, `nonReentrant` guard, or reward-index update prevents this — the flaw is in the arithmetic itself, not access control. Any unprivileged holder of `mWomSV` calling the standard `getReward`/`getRewards` path via MasterMagpie's multiclaim reaches this code and always receives the unforfeited full amount.

### Impact Explanation
This matches the "theft or permanent freezing of unclaimed yield" Immunefi impact class: any yield that the protocol design intends to forfeit and redistribute to remaining `mWomSV` stakers (via `_queueNewRewardsWithoutTransfer` → `rewardPerTokenStored`) is instead permanently retained by the claiming account. Every claimant — attacker or otherwise — captures 100% of `userRewards` regardless of whatever expiry/cooldown condition should have triggered a partial clawback, and the forfeit pool that would have benefited other stakers never receives those funds. This is a systemic, unconditional loss of the intended redistribution mechanism, not a one-off griefing vector.

### Likelihood Explanation
Likelihood is maximal/deterministic: no special preconditions, capital, or timing manipulation are required. The bug is a pure arithmetic identity (`_amount - _amount == 0`) reached on every single call to `calExpireForfeit` or every reward claim through `getReward`/`getRewards`, for every account and every state combination, exactly as the question's fuzzing proof idea describes. It requires no exploitation technique — it is a deterministic logic defect that will be observed on the very first invocation.

### Recommendation
Rewrite `_calExpireForfeit` so `rewardableAmount` is derived from actual account-specific lock/unlock/expiry state (e.g., by querying `ILocker(mWOMSV)` or the account's unlocking schedule to determine what fraction of `_amount` is "expired"/forfeitable), analogous to the intended design pattern used elsewhere (e.g. `vlMGPBaseRewarder.sol`), rather than trivially assigning `rewardableAmount = _amount`.

### Proof of Concept
Foundry test plan:
1. Deploy `mWOMSVBaseRewarder` with a mock `IMasterMagpie` and mock `ILocker`.
2. Queue rewards via `queueNewRewards` so `earned(account, token) > 0`.
3. Fuzz arbitrary mock lock/cooldown/unlock states for `account` (these are irrelevant since `_calExpireForfeit` never reads them).
4. Assert `calExpireForfeit(account, token) == 0` for all fuzzed states — this holds unconditionally because `forfeitAmount = _amount - _amount` algebraically.
5. Call `getReward`/`getRewards` through the `masterMagpie` role and assert `RewardPaid` always emits the full `userRewards` amount and `ForfeitRewardAdded` is never emitted with a non-zero value, confirming the forfeit pool never receives redistributed yield.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L188-190)
```text
    function calExpireForfeit(address _account, address _rewardToken) public view returns(uint256) {
        return _calExpireForfeit(_account, earned(_account, _rewardToken));
    }
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
