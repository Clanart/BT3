### Title
Flash-lock reward sniping in `vlMGPBaseRewarder` lets an unprivileged attacker steal a disproportionate share of forfeited MGP from a same-block whale claim - ([File: rewards/vlMGPBaseRewarder.sol])

### Summary
`vlMGPBaseRewarder` uses a single-step cumulative `rewardPerTokenStored` index (no streaming/vesting of rewards). Because `VLMGP._lock` → `MasterMagpie._deposit` snapshots a user's `userRewardPerTokenPaid` via `_harvestBaseRewarder` **before** increasing that user's staked balance, an attacker can lock MGP immediately before a whale's forfeiture-triggering `claim()` and immediately after harvest a pro-rata share of the whale's entire forfeited MGP, despite having zero time-weighted accrual.

### Finding Description
`vlMGPBaseRewarder.queueMGP` is called from `MasterMagpie._sendMGPForVlMGPPool` whenever a user claims MGP that is subject to `_calExpireForfeit`. When `forfeitAmount > 0`, it calls `_queueNewRewardsWithoutTransfer`, which does:
```solidity
rewardInfo.rewardPerTokenStored += (forfeitAmount * 1e18) / totalStaked();
``` [1](#0-0) 
where `totalStaked()` is `IERC20(vlMGP).totalSupply()`, i.e. `VLMGP.totalAmount` across **all** users, not a snapshot taken at the start of a reward period. [2](#0-1) 

Any unprivileged user can call `VLMGP.lock`/`lockFor`, which calls `MasterMagpie.depositVlMGPFor` → `_deposit(vlmgp, _for, _amount, true)`:
```solidity
_harvestBaseRewarder(_stakingToken, _account);   // snapshots userRewardPerTokenPaid at OLD rewardPerTokenStored
user.amount = user.amount + _amount;             // balance increases AFTER the snapshot
``` [3](#0-2) 
and `VLMGP._lock` increments `totalAmount` (used as `totalStaked()`) right after the deposit call: [4](#0-3) 

Exploit sequence within one block:
1. Attacker calls `lock`/`lockFor(amount, attacker)`. This sets `userRewardPerTokenPaid[MGP][attacker] = current rewardPerTokenStored` (unchanged so far) and increases `attacker`'s `balanceOf` (via `MasterMagpie.stakingInfo`) and `VLMGP.totalAmount` (i.e. `totalStaked()`).
2. A whale's pending `claim()` executes right after, hitting `queueMGP`, which computes a large `forfeitAmount` from `_calExpireForfeit(whale, amount)` (based on the whale's own unlock schedule, unrelated to the attacker) and bumps `rewardPerTokenStored` using the **now-inflated** `totalStaked()` that already includes the attacker's fresh stake. [5](#0-4) 
3. Attacker calls `claim()`/`multiclaimFor` on `MasterMagpie`, which invokes `vlMGPBaseRewarder.getReward(attacker, receiver)`. The `updateReward` modifier computes:
```solidity
_earned = (balanceOf(attacker) * (rewardPerToken(MGP) - userRewardPerTokenPaid[MGP][attacker])) / 1e18
``` [6](#0-5) 
Since the attacker's snapshot predates the whale's forfeiture, and their balance is now included in the post-bump index, they receive a full pro-rata share of the entire forfeited MGP for a stake held for effectively zero time.

Existing protections (`onlyMasterMagpie`, `nonReentrant`, `whenNotPaused`, reward-index snapshotting) do not stop this because they only guard single-call reentrancy/authorization, not same-block ordering/MEV between two independent user transactions. The `_calExpireForfeit`'s 0.1% dust-ignore threshold does not prevent this since a whale's forfeiture can be far larger than 0.1%.

### Impact Explanation
This is theft of unclaimed yield: MGP that should accrue only to existing/long-term lockers who bore reward-index dilution over time is instead partially diverted to an attacker who added stake moments before the reward was queued and can withdraw the reward (not the locked principal) immediately via `getReward`. This matches the Immunefi "theft of unclaimed yield" impact class. The magnitude scales with the size of the whale's forfeited MGP and the ratio of attacker's flash-locked amount to `totalStaked()`.

### Likelihood Explanation
- No privileged role required — any EOA can call `lock`/`lockFor` and `claim`/`multiclaimFor`.
- Requires mempool visibility of a pending whale `claim()` transaction that will trigger a non-trivial forfeiture, and same-block/back-run inclusion (standard MEV technique, e.g. via a searcher bundle or block builder).
- Capital requirement: attacker only needs enough MGP to lock (bought freely on the open market); funds remain locked but the harvested reward can be claimed immediately without unlocking principal.
- Repeatable any time a large forfeiture event is about to occur, making this systematically exploitable, not a one-off edge case.

### Recommendation
Do not let a stake added in the current transaction/block immediately participate in a reward drop from the same block. Options:
- Snapshot `totalStaked()`/rewardable balances at the start of a reward-distribution epoch, or introduce a minimum holding period (warm-up) before a locker's balance counts toward `earned()`.
- Convert the forfeiture distribution to a streamed/rate-based model (e.g., Synthetix-style `rewardRate` over a `rewardsDuration`) instead of an instantaneous `rewardPerTokenStored` bump, so a single-block stake cannot capture a full lump-sum reward.
- Alternatively, exclude newly locked balances added within the same block/epoch as a `queueMGP` forfeiture from the `rewardPerTokenStored` calculation (e.g., checkpoint balances prior to reward queuing).

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, `VLMGP`, `vlMGPBaseRewarder`, `MGP` token; register the vlMGP pool and rewarder in `MasterMagpie`.
2. Set up a "whale" user who locks a large MGP amount and lets a chunk unlock (or is otherwise in a state where `getRewardablePercentWAD` yields a large forfeiture) so that a pending `claim()` will trigger a large `forfeitAmount` in `queueMGP`.
3. In the same `vm.roll`/block:
   - Tx A: attacker calls `VLMGP.lockFor(amount, attacker)` (or `lock`) with a modest amount of freshly acquired MGP.
   - Tx B: whale calls `MasterMagpie.claim()` (or `multiclaimFor`), triggering `_sendMGPForVlMGPPool` → `queueMGP`, producing a large `forfeitAmount` that bumps `rewardPerTokenStored`.
   - Tx C: attacker calls `MasterMagpie.claim()`/`multiclaimFor` → `vlMGPBaseRewarder.getReward(attacker, attacker)`.
4. Assert: `earned(attacker, MGP)` (or the MGP actually transferred to attacker in Tx C) is greater than zero and disproportionate to a time-weighted pro-rata share (e.g., compare against `attacker_amount * elapsed_holding_time` — which is ~0 — versus the amount actually received, which equals `attacker_amount / totalStaked() * forfeitAmount`).
5. Compare against a control run where attacker's lock happens one block **after** the whale's forfeiture — attacker's `earned()` should then be ~0, confirming that block-ordering alone (not any legitimate accrual) produced the reward in the exploit scenario.

### Citations

**File:** rewards/vlMGPBaseRewarder.sol (L137-139)
```text
    function totalStaked() public override view returns (uint256) {
        return IERC20(address(vlMGP)).totalSupply();
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

**File:** rewards/MasterMagpie.sol (L482-498)
```text
    function _deposit(address _stakingToken, address _account, uint256 _amount, bool _isVlmgp) internal {
        updatePool(_stakingToken);

        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][_account];

        if (user.amount > 0) {
            _harvestMGP(_stakingToken, _account);
        }
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount + _amount;
        if (!_isVlmgp) {
            user.available = user.available + _amount;
            IERC20(pool.stakingToken).safeTransferFrom(address(msg.sender), address(this), _amount);
        }
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;
```

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
