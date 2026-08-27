### Title
First-depositor front-running of `queueNewRewards`/`donateRewards` lets an attacker capture 100% of rewards queued while `totalStaked()==0` - (File: rewards/vlMGPBaseRewarder.sol, rewards/mWOMSVBaseRewarder.sol)

### Summary
`_provisionReward` (inlined in `queueNewRewards`/`donateRewards`) accumulates reward tokens into `rewardInfo.queuedRewards` whenever `totalStaked()==0`, and flushes the *entire* backlog into `rewardPerTokenStored` divided by whatever `totalStaked()` happens to be at the moment the next reward call executes. An unprivileged attacker can watch the mempool for a `queueNewRewards`/`donateRewards` call (or simply act right after genesis/a full-unlock event when `totalStaked()==0`), front-run it with a dust `lock()`/deposit that atomically becomes the pool's stake, and thereby claim the whole queued reward pool with a near-zero-cost position.

### Finding Description
`vlMGPBaseRewarder._provisionReward`-equivalent logic in `queueNewRewards` (and the shared `_provisionReward` in `mWOMSVBaseRewarder.sol` / `BaseRewardPoolV2.sol`) does: [1](#0-0) 
```
if (totalStaked() == 0) {
    rewardInfo.queuedRewards += _amountReward;
} else {
    if (rewardInfo.queuedRewards > 0) {
        _amountReward += rewardInfo.queuedRewards;
        rewardInfo.queuedRewards = 0;
    }
    rewardInfo.rewardPerTokenStored += (_amountReward * 10**vlMGPDecimal) / totalStaked();
}
```
`rewardPerTokenStored` is a single global cumulative index (`rewardPerToken()` returns it directly, no time-weighting/checkpointing per staker interval): [2](#0-1) . A user's earned amount is `_earned = userShare * (rewardPerToken - userRewardPerTokenPaid) + userRewards`, so whoever holds the staked balance *at the instant* `rewardPerTokenStored` is bumped captures the corresponding share of the flush, regardless of how long they were staked before that moment: [3](#0-2) .

Becoming a staker in this rewarder is cheap and atomic: `VLMGP.lock()` transfers MGP from the caller and, in the same transaction, calls `masterMagpie.depositVlMGPFor`, which increases `totalStaked()` (`vlMGP.totalSupply()`) immediately: [4](#0-3) , [5](#0-4) . There is no minimum lock size and no cooldown to *become* a staker (cooldown only applies to *unlocking*), so an attacker can lock 1 wei of MGP.

Exploit flow:
1. `totalStaked()==0` (pool genesis, or after all lockers fully unlock).
2. A manager/anyone calls `queueNewRewards`/`donateRewards` with reward `R`; while pending in the mempool, the attacker front-runs with `lock(1)` (or any dust amount), making `totalStaked()==1` before the reward tx executes.
3. When the reward tx lands, it takes the `else` branch, computing `rewardInfo.rewardPerTokenStored += (R * 10**decimals) / 1`, i.e. the whole reward `R` (including any previously accrued `queuedRewards`) is now attributable entirely to the attacker's 1-wei share.
4. Attacker calls `getReward`/`getRewards` (or `masterMagpie.multiclaimFor`) to withdraw the full reward `R`, without ever needing to pass through the unlock cooldown, since claiming rewards only requires `balanceOf(_account) > 0`, not withdrawal: [6](#0-5) .

Existing checks that fail to prevent this: `onlyManager` on `queueNewRewards` only restricts *who can fund* rewards, not who can be staked when the funding lands; `nonReentrant`/`whenNotPaused` don't address MEV ordering; there is no per-block/vesting mechanism, no minimum stake duration requirement, and no snapshot of `totalStaked()` prior to the triggering deposit.

### Impact Explanation
This is a theft of unclaimed yield: reward tokens intended to be distributed to the actual/returning lockers of `vlMGP`/`mWomSV` are instead captured almost entirely by an attacker who commits a negligible, momentary stake. Matches the Immunefi impact class "theft of unclaimed yield" and, in the genesis/seed-reward case, can be a full drain of the initial reward provisioning for that token.

### Likelihood Explanation
Preconditions are realistic and do not require any admin misbehavior: `totalStaked()==0` naturally occurs at contract genesis before the first locker, and can recur if all lockers fully exit simultaneously. Capital required is minimal (1 wei of MGP plus gas), and front-running a public mempool transaction is standard MEV behavior available to any unprivileged actor. The attack is repeatable any time the `totalStaked()==0` condition is met and a reward-provisioning transaction is observable pre-confirmation.

### Recommendation
Do not allow `totalStaked()` to be manipulated in the same block/transaction window as a queued-reward flush without a minimum bonding period; e.g., require a lock-up duration before a staker's balance counts toward reward distribution ("warm-up" period), or checkpoint/distribute the queued rewards pro-rata over a vesting window rather than instantaneously into `rewardPerTokenStored` at the next stake event. Alternatively, restrict `_provisionReward`'s flush to only be triggered by, or measured against, `totalStaked()` snapshotted before the manager's transaction was broadcast (e.g., via a two-step commit-reveal or a minimum elapsed time since last stake change).

### Proof of Concept
Hardhat test plan:
1. Deploy `MasterMagpie`, `VLMGP`, `vlMGPBaseRewarder`, register a reward token, add `vlMGPBaseRewarder` pool with 0 total staked.
2. Have `rewardManager` call `queueNewRewards(R, rewardToken)` — assert `rewards[rewardToken].queuedRewards == R` and `rewardPerTokenStored == 0` (since `totalStaked()==0`).
3. Simulate attacker front-run: attacker calls `MGP.approve` + `VLMGP.lock(1)` in a transaction ordered *before* a second `queueNewRewards(R2, rewardToken)` call (or construct both txs in the same block with attacker's tx given priority).
4. After the second `queueNewRewards` call executes, assert `rewardPerTokenStored == (R + R2) * 10**18 / 1`.
5. Call `vlMGPBaseRewarder.earned(attacker, rewardToken)` and assert it equals `R + R2` (100% of both queued and new reward), despite attacker holding a 1-wei stake for effectively zero real economic time.
6. Have attacker call `getReward`/`multiclaimFor` and assert they receive the full `R + R2` reward tokens, confirming capture of yield that should have accrued to genuine future lockers.

### Citations

**File:** rewards/vlMGPBaseRewarder.sol (L159-166)
```text
    function rewardPerToken(address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return rewards[_rewardToken].rewardPerTokenStored;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L232-246)
```text
    function getReward(address _account, address _receiver)
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }

        return true;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L314-324)
```text
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

**File:** VLMGP.sol (L254-258)
```text
    function lock(uint256 _amount) override external whenNotPaused nonReentrant {
        _lock(msg.sender, msg.sender, _amount);

        emit NewLock(msg.sender, block.timestamp, _amount);
    }
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
