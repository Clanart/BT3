### Title
Instant reward-index dilution lets a front-running locker capture forfeited MGP without time-weighted contribution - (File: rewards/vlMGPBaseRewarder.sol)

### Summary
`queueMGP` computes a forfeit amount for a specific forfeiting user and immediately redistributes it to *all* current `totalStaked()` holders via `_queueNewRewardsWithoutTransfer`, which bumps `rewardPerTokenStored` in a single discrete step. Because this reward-per-token accounting has no time-weighting or vesting, any account that increases its vlMGP balance in the same block (or just before) the forfeit is queued captures a full pro-rata share of that forfeited MGP, exactly like classic "flash-stake-to-front-run-reward" issues in Synthetix-style pools.

### Finding Description
`queueMGP` (rewards/vlMGPBaseRewarder.sol:274-289) is called by an authorized `rewardManager`/`MasterMagpie` (via `_sendMGPForVlMGPPool`, rewards/MasterMagpie.sol:638-644) whenever a user harvests MGP. It computes:

```
uint256 forfeitAmount = _calExpireForfeit(_account, _amount);
...
if (forfeitAmount > 0)
    _queueNewRewardsWithoutTransfer(forfeitAmount, address(vlMGP.MGP()));
``` [1](#0-0) 

`_calExpireForfeit` uses `vlMGP.getRewardablePercentWAD(_account)` to determine how much of user A's harvest is forfeited (e.g., because A didn't maintain a long enough lock/relock cadence): [2](#0-1) 

`_queueNewRewardsWithoutTransfer` then distributes this forfeit as an instantaneous bump to `rewardPerTokenStored`, scaled by whatever `totalStaked()` (i.e. `vlMGP.totalSupply()`) is *at that exact block*:

```
rewardInfo.rewardPerTokenStored =
    rewardInfo.rewardPerTokenStored +
    (_amountReward * 10**vlMGPDecimal) / totalStaked();
``` [3](#0-2) 

Reward accrual per-user is a simple checkpoint difference with no per-second streaming or minimum holding-period requirement:
```
function _earned(...) internal view returns (uint256) {
    return ((_userVlmgpShare *
            (rewardPerToken(_rewardToken) -
                userRewardPerTokenPaid[_rewardToken][_account])) /
            10**vlMGPDecimal) + userRewards[_rewardToken][_account];
}
``` [4](#0-3) 

and the `updateRewards`/`updateReward` modifiers simply snapshot `balanceOf(_account)` at call time with no time-weighting: [5](#0-4) 

`balanceOf` reads the staked vlMGP amount live from `MasterMagpie.stakingInfo`: [6](#0-5) 

**Exploit flow:** Attacker B observes/predicts that a `queueMGP` call (triggered when user A harvests MGP and forfeits a portion) is about to land. In the same block, B front-runs by locking a large amount of MGP into vlMGP (increasing `totalStaked()` and B's own balance). When `queueMGP` executes, the forfeit amount computed for A is divided across the now-inflated `totalStaked()` that includes B's freshly-added balance. B subsequently calls `getReward`/`getRewards`, whose `updateRewards` modifier computes `_earned` using B's current `balanceOf`, crediting B with `B_balance/totalStaked() * forfeitAmount` even though B contributed zero locked time before the forfeit event occurred.

None of the existing guards (`onlyManager`, `nonReentrant`, `updateRewards`) address this because the vulnerability is not reentrancy or an access-control bypass — it stems from the reward math itself lacking time-weighting, and front-running is available to any unprivileged locker.

### Impact Explanation
This is a "theft or unfair capture of unclaimed yield" impact: value forfeited from long-term lockers (intended to reward genuinely committed vlMGP holders) can be siphoned by an attacker who only needs to hold a balance for a single block/transaction ordering window. This dilutes/steals real economic value (forfeited MGP) away from legitimate long-term lockers who should be the beneficiaries of the recycling mechanism, in favor of an opportunistic front-runner.

### Likelihood Explanation
- No privileged role required; the attacker only needs capital to lock into vlMGP and the ability to observe/front-run a pending `queueMGP` transaction (mempool visibility, same-block ordering, or predictable harvest timing).
- The attack is repeatable every time a `queueMGP` call with nonzero `forfeitAmount` occurs, so it scales with protocol usage.
- The main mitigating factor (not fully verifiable from the available code) is whatever minimum lock duration `VLMGP.sol`'s `lockFor`/`lock` imposes before an attacker can withdraw the capital used to inflate `totalStaked()` — I could not fully confirm within tool-call limits whether newly locked (non-cooldown) MGP is counted at full weight immediately in `totalStaked()`/`balanceOf`, though the codebase's own comment ("lock weighting ... amount of MGP still in lock + amount of MGP in cool down / 2") indicates freshly-locked (non-cooldown) MGP counts at full weight instantly, supporting the exploit's feasibility.

### Recommendation
Redesign the forfeit-recycling reward mechanism to avoid instantaneous, un-time-weighted distribution:
- Stream forfeited rewards over a fixed duration (e.g., Synthetix-style `rewardRate`/`periodFinish`) instead of instantly bumping `rewardPerTokenStored`, so that only balances held throughout the distribution window earn proportionally.
- Alternatively, snapshot eligible balances (e.g., balances as of the start of the current reward epoch) rather than using the live/spot `totalStaked()` at the moment `queueMGP` is called.
- Consider a minimum holding-period requirement before a balance becomes eligible for reward-per-token accrual.

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `MasterMagpie`, `vlMGPBaseRewarder`, `MGP` token; set up reward manager permissions.
2. Have user A lock MGP long enough to accrue a harvestable MGP balance, then let A's `getRewardablePercentWAD` degrade (e.g., via time passing without relock) so a subsequent harvest will trigger a nonzero `forfeitAmount`.
3. Record `totalStaked()` = `T0` (excluding attacker) before the harvest transaction.
4. In the same block as the manager's `queueMGP(amount, A, receiver)` call (use `vm.startPrank`/multicall or same-block ordering in Foundry), have attacker B call MasterMagpie's deposit path to `lockFor` a large amount `X` of MGP into vlMGP, so `totalStaked()` becomes `T0 + X`.
5. Execute `queueMGP`; assert `forfeitAmount > 0` was queued via `_queueNewRewardsWithoutTransfer`.
6. Call `vlMGPBaseRewarder.getReward(B, B)` (via MasterMagpie) and assert `userRewards[MGP][B]` / received amount ≈ `X / (T0 + X) * forfeitAmount > 0`, despite B having held the position for 0 blocks/time prior to the forfeit event.
7. Assert this captured amount is disproportionate to B's time-weighted contribution (0 elapsed time) relative to A's or other pre-existing long-term stakers' contributions, confirming reward capture without commensurate lock duration.

### Citations

**File:** rewards/vlMGPBaseRewarder.sol (L100-118)
```text
    modifier updateRewards(address _account, address[] memory _rewards) {
        uint256 length = _rewards.length;
        uint256 userVlMGPAmount = balanceOf(_account);
        
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewards[index];
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;

            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userVlMGPAmount);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
        _;
    }

    modifier updateReward(address _account) {
        _updateFor(_account);
        _;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L145-148)
```text
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
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

**File:** rewards/vlMGPBaseRewarder.sol (L331-347)
```text
    function _queueNewRewardsWithoutTransfer(uint256 _amountReward, address _rewardToken) internal
    {
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards = rewardInfo.historicalRewards + _amountReward;
        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**vlMGPDecimal) / totalStaked();
        }
        emit ForfeitRewardAdded(_amountReward, _rewardToken);
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L379-384)
```text
    function _earned(address _account, address _rewardToken, uint256 _userVlmgpShare) internal view returns (uint256) {
        return ((_userVlmgpShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**vlMGPDecimal) + userRewards[_rewardToken][_account];
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
