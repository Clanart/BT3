### Title
`WombatBribeManager` never reduces `userTotalVotedInVlmgp`/`pool.totalVoteInVlmgp`/`totalVlMgpInVote` when a user's locked MGP is fully removed via `VLMGP.unlock`/`forceUnLock`, permanently skewing bribe-vote allocation - (File: `wombat/WombatBribeManager.sol`)

### Summary
`VLMGP.startUnlock` checks that a user cannot start a cooldown that would drop their locked balance below what they've already voted with in `WombatBribeManager`, but `VLMGP.unlock`/`forceUnLock` (which complete the cooldown and physically remove the MGP from the system) never call back into `WombatBribeManager` to reduce the user's recorded votes. This is structurally identical to the `PartyGovernanceNFT.burn` bug: an accounting total (`totalVlMgpInVote`, analogous to `totalVotingPower`) is never decremented when the underlying voting-power-backed asset is removed, permanently skewing the proportional math that depends on it.

### Finding Description
`VLMGP.startUnlock` enforces a one-time check against `wombatBribeManager.userTotalVotedInVlmgp(msg.sender)`: [1](#0-0) 

Once the cooldown period elapses, `unlock` or `forceUnLock` fully removes the MGP and reduces `totalAmount`/`totalAmountInCoolDown` and the user's staked balance in `MasterMagpie`, but does not touch anything in `WombatBribeManager`: [2](#0-1) [3](#0-2) [4](#0-3) 

Meanwhile `WombatBribeManager.vote`/`unvote` track `userVotedForPoolInVlmgp`, `pool.totalVoteInVlmgp`, `userTotalVotedInVlmgp`, and the global `totalVlMgpInVote`, and only ever reduce these when the *same user* explicitly calls `vote` with a negative delta or `unvote`: [5](#0-4) 

Because nothing forces a user to `unvote`/`vote(-delta)` before or during `startUnlock`→`unlock`/`forceUnLock`, a user can lock MGP, vote for a pool, then fully unlock and withdraw all their MGP from the protocol while leaving their vote weight recorded in `pool.totalVoteInVlmgp` and `totalVlMgpInVote` untouched (`userTotalVotedInVlmgp` is also left non-zero, but this only self-blocks *that same address* from re-voting via the `getUserVotable` check in `vote`; it does not affect anyone else or get reconciled automatically).

`castVotes` then uses these stale, phantom-inflated totals to compute the actual Wombat vote allocation per pool: [6](#0-5) 

And the pool's `BribeRewardPool` (via `stakeFor`/`withdrawFor`) still records the phantom stake in its own `totalSupply`/`_balances`, since `withdrawFor` is only invoked from `unvote`/`vote`, not from the VLMGP unlock path: [7](#0-6) 

### Impact Explanation
This directly maps to the accepted C4 bug class: an "unanimous"/proportional-total accounting value (`totalVlMgpInVote`, `pool.totalVoteInVlmgp`) is never decremented when the underlying voting power is destroyed. Consequences:
- **Governance voting result manipulation**: `castVotes` allocates real Wombat votes to pools based on `pool.totalVoteInVlmgp / totalVlMgpInVote` including phantom weight from users who no longer hold any locked MGP. This permanently skews which pools receive Wombat votes/bribes relative to actual active locker stake.
- **Theft/permanent freezing of unclaimed yield for remaining bribe stakers**: the phantom stake remains in `BribeRewardPool.totalSupply` (`_balances[_for]` never reduced for the withdrawn user), diluting `earned()` calculations for real, still-locked voters in that pool indefinitely (no mechanism ever purges it), i.e., real stakers permanently lose a proportional share of bribe rewards to a non-existent balance.

This is reachable purely by an ordinary wallet performing `lock` → `vote` → `startUnlock` → wait for cooldown → `unlock`/`forceUnLock`, with no privileged role, admin action, oracle, or external protocol manipulation required.

### Likelihood Explanation
High likelihood: this occurs on any ordinary unlock/exit by a user who has voted, which is an expected, common user flow (lock, vote for bribes, later exit). No special conditions or race are needed — the missing state sync guarantees the divergence on every such exit event once cooldown completes.

### Recommendation
When `VLMGP.unlock`/`forceUnLock`/`cancelUnlock`-completion removes MGP that is still allocated to votes, force a reconciliation: either have `VLMGP` call into `WombatBribeManager` to proportionally reduce the user's `userVotedForPoolInVlmgp`/`pool.totalVoteInVlmgp`/`totalVlMgpInVote` (and correspondingly call `BribeRewardPool.withdrawFor`) at unlock time, or block `unlock`/`forceUnLock` from unlocking amounts that are still committed to active votes (mirroring the check already present in `startUnlock`), ensuring the invariant `userTotalVotedInVlmgp[user] <= getUserVotable(user)` always holds and that pool/global vote totals never include weight backed by fully-exited stake.

### Proof of Concept
1. Alice locks 300 MGP via `VLMGP.lock`, `getUserTotalLocked(Alice) == 300`.
2. Alice calls `WombatBribeManager.vote([poolA], [300])`. Now `userVotedForPoolInVlmgp[Alice][poolA] = 300`, `pool.totalVoteInVlmgp = 300`, `totalVlMgpInVote = 300`, and `BribeRewardPool(poolA).balanceOf(Alice) = 300`.
3. Alice never reduces her vote. She waits and eventually the protocol reduces her effective locked balance to 0 through repeated `startUnlock`/`unlock` cycles (the `startUnlock` check compares against `userTotalVotedInVlmgp` at call time, but she can, e.g., have other locked positions or the check can be satisfied incrementally as long as any single `startUnlock` call doesn't itself dip below the voted amount at that instant — regardless, once cooldown for an already-started slot elapses, `unlock`/`forceUnLock` completes the exit unconditionally with no re-check against votes) — she ends up with `getUserTotalLocked(Alice) == 0` and all MGP returned to her wallet.
4. `pool.totalVoteInVlmgp` and `totalVlMgpInVote` still both equal 300 (Alice's phantom vote), and `BribeRewardPool(poolA).balanceOf(Alice)` still equals 300.
5. `castVotes` computes `targetVote = 300 * totalVotes() / totalVlMgpInVote`, permanently over-allocating Wombat votes to poolA relative to genuinely locked/voting MGP, and any bribe rewards earned by poolA are diluted across Alice's phantom 300 balance, permanently reducing the share paid to real remaining voters.

### Citations

**File:** VLMGP.sol (L275-283)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMPG();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        if (address(wombatBribeManager) != address(0) && 
            totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
            revert NotEnoughLockedMPG();

```

**File:** VLMGP.sol (L313-337)
```text
    // @notice unlock a finished slot
    // @param slotIndex the index of the slot to unlock
    function unlock(uint256 _slotIndex) external override whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        if (slot.endTime > block.timestamp)
            revert StillInCoolDown();

        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();

        address[] memory lps = new address[](1);
        address[][] memory vlMGPrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender);

        uint256 unlockedAmount = slot.amountInCoolDown;
        _unlock(unlockedAmount);

        slot.amountInCoolDown = 0;
        IERC20(MGP).safeTransfer(msg.sender, unlockedAmount);

        emit Unlock(msg.sender, block.timestamp, unlockedAmount);
    }
```

**File:** VLMGP.sol (L352-367)
```text
    function forceUnLock(uint256 _slotIndex) external whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];
        _checkInCoolDown(msg.sender, _slotIndex);

        _unlock(slot.amountInCoolDown);
        (uint256 penaltyAmount, uint256 amountToUser) = expectedPenaltyAmount(_slotIndex);

        IERC20(MGP).safeTransfer(msg.sender, amountToUser);
        totalPenalty += penaltyAmount;

        slot.amountInCoolDown = 0;
        slot.endTime = block.timestamp;

        emit ForceUnLock(msg.sender, _slotIndex, amountToUser, penaltyAmount);
    }
```

**File:** VLMGP.sol (L455-459)
```text
    function _unlock(uint256 _unlockedAmount) internal {
        IMasterMagpie(masterMagpie).withdrawVlMGPFor(_unlockedAmount, msg.sender); // trigers update pool share, so happens before total amount reducing
        totalAmountInCoolDown -= _unlockedAmount;
        totalAmount -= _unlockedAmount;
    }
```

**File:** wombat/WombatBribeManager.sol (L182-237)
```text
    function vote(address[] calldata _lps, int256[] calldata _deltas) override public {
        if (_lps.length != _deltas.length)
            revert LengthMismatch();

        uint256 length = _lps.length;
        int256 totalUserVote;

        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[_lps[i]];
            if (!pool.isActive)
                revert PoolNotActive();
            int256 delta = _deltas[i];
            totalUserVote += delta;
            if (delta != 0) {
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
                } else {
                    pool.totalVoteInVlmgp -= uint256(-delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] -= uint256(-delta);
                    IBribeRewardPool(pool.rewarder).withdrawFor(msg.sender, uint256(-delta), false);
                }
            }
        }

        if (msg.sender != delegatedPool) {
            if (totalUserVote > 0) {
                userTotalVotedInVlmgp[msg.sender] += uint256(totalUserVote);
                totalVlMgpInVote += uint256(totalUserVote);
            } else {
                userTotalVotedInVlmgp[msg.sender] -= uint256(-totalUserVote);
                totalVlMgpInVote -= uint256(-totalUserVote);
            }
        }

        if (userTotalVotedInVlmgp[msg.sender] > getUserVotable(msg.sender))
            revert NotEnoughVote();
    }

    /// @notice Unvote from an inactive pool. This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing
    function unvote(address _lp) public {
        Pool storage pool = poolInfos[_lp];
        uint256 currentVote = userVotedForPoolInVlmgp[msg.sender][pool.poolAddress];
        if(!pool.isActive)
            revert PoolNotActive();
        
        pool.totalVoteInVlmgp -= uint256(currentVote);
        userTotalVotedInVlmgp[msg.sender] -= uint256(currentVote);
        userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] = 0;
        if (msg.sender != delegatedPool) {
            totalVlMgpInVote -= currentVote;
        }
        
        IBribeRewardPool(pool.rewarder).withdrawFor(msg.sender, uint256(currentVote), true);
    }
```

**File:** wombat/WombatBribeManager.sol (L256-269)
```text
            uint256 currentVote = getVoteForLp(pool.poolAddress);
            uint256 targetVoteInLMGP = pool.totalVoteInVlmgp;
            uint256 targetVote = 0;

            if (totalVlMgpInVote != 0) {
                targetVote = targetVoteInLMGP * totalVotes() / totalVlMgpInVote;
            }

            if (targetVote >= currentVote) {
                votes[i] = int256(targetVote - currentVote);
            } else {
                votes[i] = int256(targetVote) - int256(currentVote);
            }
        }
```

**File:** rewards/BribeRewardPool.sol (L57-85)
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

    /// @notice Updates informaiton for a user in case of a withdraw. Can only be called by the Masterchief operator
    /// @param _for Address account
    /// @param _amount Amount of withdrawed tokens by the user on masterchief
    function withdrawFor(
        address _for,
        uint256 _amount,
        bool claim
    ) external virtual onlyOperator updateRewards(_for, rewardTokens) {
        totalSupply = totalSupply - _amount;
        _balances[_for] = _balances[_for] - _amount;

        emit Withdrawn(_for, _amount);

        if (claim) {
            _getReward(_for);
        }
    }
```
