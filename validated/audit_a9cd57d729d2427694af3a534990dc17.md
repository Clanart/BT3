### Title
Locked MGP voting weight is not re-synced when moved into cooldown via `startUnlock`, allowing voters to retain full governance weight while withdrawing underlying stake - ([File: rewards/VLMGP.sol])

### Summary
`VLMGP.startUnlock` only blocks a user from starting a cooldown if the resulting locked balance would drop **below** their currently recorded `userTotalVotedInVlmgp`, but it never reduces or re-syncs the user's vote weight in `WombatBribeManager` when MGP is moved into cooldown. An attacker can vote with nearly their entire locked balance, then immediately move all remaining "unvoted" MGP into cooldown, keeping `totalLockAfterStartUnlock` exactly equal to (or one wei above) their recorded vote weight, thereby retaining full bribe/governance voting power while the underlying MGP is already in the withdrawal pipeline.

### Finding Description
`startUnlock` (rewards/VLMGP.sol, `VLMGP.sol:275-282`) enforces:
```solidity
uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
if (address(wombatBribeManager) != address(0) &&
    totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
    revert NotEnoughLockedMPG();
``` [1](#0-0) 

This is a floor check only — it prevents `totalLockAfterStartUnlock` from falling **below** `userTotalVotedInVlmgp`, but it does nothing to reduce `userTotalVotedInVlmgp`, `userVotedForPoolInVlmgp`, or `pool.totalVoteInVlmgp` in `WombatBribeManager` (`wombat/WombatBribeManager.sol:181-220`) when the cooldown starts. Those values are only updated inside `vote()`/`unvote()`, which the attacker does not need to call. [2](#0-1) 

Exploit flow:
1. Attacker locks MGP in `VLMGP` and calls `WombatBribeManager.vote(lps, deltas)` with `totalUserVote` close to `getUserVotable(msg.sender)` (bounded by locked amount), setting `userTotalVotedInVlmgp[attacker]` to nearly all of their locked MGP.
2. Attacker calls `startUnlock(_amountToCoolDown)` with `_amountToCoolDown = getUserTotalLocked(attacker) - userTotalVotedInVlmgp[attacker]` (i.e., the maximum amount that keeps `totalLockAfterStartUnlock == userTotalVotedInVlmgp[attacker]`, satisfying the `>=` check).
3. This succeeds: nearly all of the attacker's MGP moves into a cooldown slot (`userUnlockings`), while `pool.totalVoteInVlmgp`/`userVotedForPoolInVlmgp` in `WombatBribeManager` are untouched — the attacker's full vote weight (and their `IBribeRewardPool` stake via `stakeFor`) remains intact.
4. Attacker can repeat this pattern across multiple slots up to `maxSlot`, or simply wait out cooldown and withdraw, all while their vote continues to count and their bribe-pool receipt-token stake (from `stakeFor` in `vote()`) continues to earn/influence rewards, even though the underlying MGP is economically committed to exiting rather than "at stake."

Existing checks that fail to prevent this: the `>=` floor check in `startUnlock` only stops the lock from going *below* the vote amount — it does not force a resync/reduction of the vote when a cooldown is started, which is the actual gap.

### Impact Explanation
This causes governance/bribe voting-result manipulation: an attacker's recorded voting weight in `WombatBribeManager` (`totalVoteInVlmgp`, `userVotedForPoolInVlmgp`) no longer reflects MGP genuinely locked and at risk/slashable — nearly all of it can be simultaneously queued for exit via cooldown. This distorts the intended 1:1 relationship between "locked and committed" MGP and voting power, letting voters exert influence over bribe/reward pool allocation without the corresponding capital lockup, and to keep earning bribe-pool receipt token yield during the cooldown period. This matches the "governance voting result manipulation" impact class specified in scope.

### Likelihood Explanation
- No privileged role required — any address holding/locking MGP can execute `vote()` then `startUnlock()`.
- Requires only owning MGP tokens and locking them, a normal user action.
- Fully repeatable: the attacker can do this on every voting/cooldown cycle, and across all their unlock slots (bounded by `maxSlot`).
- No flash loan or exotic setup needed — straightforward two-transaction sequence (`vote` then `startUnlock`).

### Recommendation
When `startUnlock` reduces `getUserTotalLocked`, force the user's outstanding votes down to match, e.g., by calling into `WombatBribeManager` to proportionally `unvote`/reduce `userTotalVotedInVlmgp`, `userVotedForPoolInVlmgp`, and `pool.totalVoteInVlmgp` (and corresponding `withdrawFor` calls on `IBribeRewardPool`) whenever locked balance available for voting drops, rather than merely reverting if the new locked balance falls below the already-recorded vote. Alternatively, require users to fully `unvote` before/as part of `startUnlock` for the portion being moved to cooldown.

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `WombatBribeManager`, and MGP mock; set up an active pool with a `BribeRewardPool` rewarder and wire `wombatBribeManager` into `VLMGP`.
2. Attacker locks `1000e18` MGP via `VLMGP.lock`.
3. Attacker calls `WombatBribeManager.vote([pool], [999e18])` — asserts `userTotalVotedInVlmgp[attacker] == 999e18` and `pool.totalVoteInVlmgp == 999e18`.
4. Attacker calls `VLMGP.startUnlock(1e18)` (bringing `totalLockAfterStartUnlock` to `999e18`, exactly equal to voted amount) — should succeed without reverting.
5. Assert: `getUserTotalLocked(attacker) == 999e18` (nearly all locked MGP now effectively only 999e18 non-cooldown) while `getUserAmountInCoolDown(attacker) == 1e18`.
6. Repeat by calling `vote` to reduce vote slightly is not needed — instead assert directly that `userTotalVotedInVlmgp[attacker]` remains `999e18` unchanged after `startUnlock`, proving votes were not resynced/reduced automatically.
7. Optionally, repeat the `startUnlock` call with the max allowed amount each time `userTotalVotedInVlmgp` still permits, demonstrating the attacker can iteratively push nearly all locked MGP into cooldown while retaining `999e18` of voting weight and the corresponding `IBribeRewardPool` stake balance (checked via `rewarder.balanceOf(attacker)` remaining `999e18` throughout).

### Citations

**File:** VLMGP.sol (L275-282)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMPG();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        if (address(wombatBribeManager) != address(0) && 
            totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
            revert NotEnoughLockedMPG();
```

**File:** wombat/WombatBribeManager.sol (L182-220)
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
```
