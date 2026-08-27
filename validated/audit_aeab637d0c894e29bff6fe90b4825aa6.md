### Title
Atomic `voteAndCast` allows unprivileged attackers to snipe freshly-harvested bribe rewards with zero epoch-long exposure - (File: `wombat/WombatBribeManager.sol`, `rewards/BribeRewardPool.sol`)

### Summary
`WombatBribeManager.voteAndCast` calls `vote()` (which updates `BribeRewardPool.stakeFor` and thus the attacker's reward checkpoint) and then immediately `castVotes()` (which harvests Wombat bribes and calls `queueNewRewards`/`_provisionReward` on the same rewarder) in one transaction. Because `lock()` in `VLMGP.sol` grants usable `vlMGP` instantly with no cooldown, an attacker can buy MGP, lock it, and vote-and-cast in a single transaction to receive a full pro-rata share of a bribe payout that was earned by other voters over the entire preceding epoch.

### Finding Description
- `VLMGP.lock`/`_lock` deposits MGP and immediately updates `masterMagpie` stake with no delay [1](#0-0) , so `getUserVotable` (`= getUserTotalLocked`) is satisfied the instant MGP is locked [2](#0-1) .
- `voteAndCast` runs `vote(_lps, _deltas)` then `castVotes(swapForBnb)` atomically [3](#0-2) .
- `vote()` calls `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta))` for each positive delta [4](#0-3) .
- `BribeRewardPool.stakeFor` is guarded by `updateRewards(_for, rewardTokens)`, which snapshots the account's reward checkpoint using the **pre-stake** balance/index, then only afterward increases `totalSupply`/`_balances` [5](#0-4) .
- Immediately after, `castVotes()` invokes `wombatStaking.vote(...)`, which harvests bribes and (via `_forwardRewards`) calls `queueNewRewards` → `_provisionReward` on the rewarder, dividing the harvested reward by the **current** `totalStaked()`/`totalSupply` — which now already includes the attacker's freshly staked delta [6](#0-5) .
- Since the attacker's `userRewardPerTokenPaid` checkpoint was captured at the pre-distribution index, `_earned` computes their share as `balance * (newIndex - oldIndex)`, entitling them to a full proportional cut of the newly harvested bribe despite holding the position for zero time during the epoch that generated it [7](#0-6) .
- No cooldown, minimum holding period, or snapshot-before-vote mechanism exists in `vote`, `castVotes`, or `lock` to prevent this same-block/same-tx entry-then-harvest sequence.

### Impact Explanation
This causes direct dilution/theft of unclaimed yield: honest voters who held their vote position for the full epoch have their share of the harvested bribe diluted by a newcomer who contributed zero time-weighted vote exposure, extracting value that should have accrued only to genuine long-term voters. This matches the "theft of unclaimed yield" impact class.

### Likelihood Explanation
Feasibility is high: the attacker needs only capital (buy/borrow MGP), since `lock()` provides usable `vlMGP` instantly with no time lock, and `voteAndCast` is a public, unprivileged, single-transaction entry point. The attack is repeatable every time a `castVotes`/`voteAndCast` harvest is imminent (predictable since `lastCastTime` and pending bribe accrual are public state), and requires no admin or governance role.

### Recommendation
Decouple reward eligibility from same-block stake changes: snapshot voter balances/checkpoints prior to any pending harvest (e.g., require a minimum holding duration before a new vote counts toward the next `queueNewRewards` distribution), or process `queueNewRewards` using the pre-vote `totalVoteInVlmgp`/stake snapshot rather than the post-`vote()` state. Alternatively, prevent `stakeFor` triggered within the same transaction as `castVotes` from being included in the immediately following reward distribution (e.g., a checkpoint delay/epoch-boundary enforcement similar to a `startUnlock`-style cooldown applied symmetrically to new votes).

### Proof of Concept
Foundry test outline:
1. Deploy `VLMGP`, `MasterMagpie`, `WombatBribeManager`, `BribeRewardPool`, mock `wombatStaking`/`voter` that simulate a pending bribe balance accrued over a full epoch.
2. Voter A locks MGP and votes for pool `lp1` at epoch start; time-warp forward one full epoch so a bribe amount `B` accumulates on the Wombat side attributable to A's vote.
3. Immediately before the scheduled `castVotes`, have Attacker (freshly funded EOA) call `MGP.approve` + `VLMGP.lock(amount)` then `voteAndCast([lp1], [amount], false)` in the same transaction (or same block).
4. Mock `wombatStaking.vote` to harvest bribe `B` and call `rewarder.queueNewRewards(B, token)`.
5. Assert: `BribeRewardPool.earned(attacker, token) > 0` and roughly proportional to `attackerVote / (attackerVote + voterA.vote)`, despite attacker having zero prior time-weighted exposure; compare against `earned(voterA, token)` to show A's expected full share `B` was diluted by the attacker's late entry.

### Citations

**File:** VLMGP.sol (L102-104)
```text
        masterMagpie = _masterMagpie;
        MGP = IERC20(_mgp);
        coolDownInSecs = _coolDownInSecs;
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

**File:** wombat/WombatBribeManager.sol (L196-199)
```text
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
```

**File:** wombat/WombatBribeManager.sol (L315-322)
```text
    function voteAndCast(
        address[] calldata _lps,
        int256[] calldata _deltas,
        bool swapForBnb
    ) external returns (address[][] memory finalRewardTokens, uint256[][] memory finalFeeAmounts) {
        vote(_lps, _deltas);
        (finalRewardTokens, finalFeeAmounts) = castVotes(swapForBnb);
    }
```

**File:** rewards/BribeRewardPool.sol (L57-67)
```text
    function stakeFor(address _for, uint256 _amount)
        external
        virtual
        onlyOperator
        updateRewards(_for, rewardTokens)
    {
        totalSupply = totalSupply + _amount;
        _balances[_for] = _balances[_for] + _amount;

        emit Staked(_for, _amount);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L290-313)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;

        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingTokenDecimals) /
                totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
```

**File:** rewards/BaseRewardPoolV2.sol (L316-321)
```text
    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```
