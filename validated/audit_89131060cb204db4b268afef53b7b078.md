### Title
Vote-share hijack via `totalVlMgpInVote` denominator shrinkage in `castVotes` - ([File: wombat/WombatBribeManager.sol])

### Summary
`castVotes` computes each pool's target share of the real Wombat voting power (`totalVotes()`) as `pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote`. Because `totalVlMgpInVote` and `pool.totalVoteInVlmgp` are mutated symmetrically by `vote()`/`unvote()` while `totalVotes()` is an independent, externally-fixed value (`veWom.balanceOf(wombatStaking)`), any sequence that shrinks `totalVlMgpInVote` to near the size of an attacker's dust vote causes that attacker's pool to receive a disproportionate (up to ~100%) share of the real veWOM votes cast to Wombat.

### Finding Description
In `castVotes` ( [1](#0-0) ), for each pool the target real-vote allocation is:
```
targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote
```
`totalVotes()` returns `veWom.balanceOf(address(wombatStaking))` — the actual, real voting power Wombat gave the protocol, unrelated to how much vlMGP is internally registered as "voted" ( [2](#0-1) ).

`totalVlMgpInVote` and `pool.totalVoteInVlmgp` are both updated by `vote()`/`unvote()` in lock-step per-user delta ( [3](#0-2) , [4](#0-3) ). Neither `vote()` nor `unvote()` is restricted to any privileged role, and `castVotes` itself is a plain `public` function callable by anyone.

Exploit flow:
1. Victim calls `vote()` for Pool B with a large amount `V`. State: `poolB.totalVoteInVlmgp = V`, `totalVlMgpInVote = V`.
2. Attacker calls `vote()` for Pool A with a dust amount `d` (as little as the smallest unit of locked vlMGP). State: `poolA.totalVoteInVlmgp = d`, `totalVlMgpInVote = V + d`.
3. Victim calls `unvote(PoolB)`. This subtracts the victim's full `V` from both `poolB.totalVoteInVlmgp` and the global `totalVlMgpInVote` in the same statement ( [5](#0-4) ). State becomes: `poolB.totalVoteInVlmgp = 0`, `totalVlMgpInVote = d`.
4. Anyone calls `castVotes(false)`. For Pool A: `targetVote = d * totalVotes() / d = totalVotes()` — i.e., 100% of the real veWOM voting power is directed to Pool A, even though the attacker only ever locked a dust amount of vlMGP.

The root cause is that the proportion is computed against the *current* internal bookkeeping total (`totalVlMgpInVote`), which can be driven arbitrarily low relative to the fixed `totalVotes()` denominator by any user unvoting, while `getUserVotable`/`NotEnoughVote` checks in `vote()` only bound an individual user's own vote against their own locked balance ( [6](#0-5) ) — they do nothing to protect the integrity of `totalVlMgpInVote` as a stable denominator for other users' pool shares. No modifier, pause, or reentrancy guard addresses this state-dependent rounding/skew, and the exploit requires only ordinary calls to `vote`/`unvote`/`castVotes` from unprivileged EOAs.

### Impact Explanation
This is a governance voting result manipulation of the real Wombat gauge: `wombatStaking.vote()` is invoked with `votes[i]` values derived from the skewed `targetVote`, sending Wombat's real veWOM allocation overwhelmingly to a pool that an unprivileged attacker effectively controls with negligible locked vlMGP ( [7](#0-6) ). This lets an attacker redirect bribe emissions/gauge weight disproportionate to their real stake, diverting bribe rewards intended for legitimate voters and distorting the Wombat gauge outcome — matching the "governance voting result manipulation" impact class.

### Likelihood Explanation
Feasibility is high and capital requirement is low: the attacker needs only a dust amount of locked vlMGP. The only timing requirement is that the attacker's `vote()` happens to remain registered at the moment `totalVlMgpInVote` collapses (e.g., right after a large voter's `unvote()`), which can be achieved by front-running/back-running in the same block or simply monitoring mempool activity — no privileged role, oracle, or upgrade path is needed, and it is repeatable every casting cycle as long as some large voter unvotes.

### Recommendation
Decouple the per-pool allocation ratio from `totalVlMgpInVote`, or compute proportions using a value that cannot be manipulated to near-zero relative to a single dust voter, e.g.:
- Track proportions using accumulated/locked balances that require a minimum locking period before being eligible to affect the denominator, or
- Cap the maximum share of `totalVotes()` any single pool/voter can receive within one `castVotes` call in proportion to their vlMGP relative to `IVLMGP(vlMGP).totalLocked()` (a value not directly manipulable via unvote timing) rather than `totalVlMgpInVote`, or
- Snapshot `totalVlMgpInVote` and per-pool votes at a fixed point (e.g., epoch boundary) so within-block/same-epoch `unvote()` cannot alter the denominator used for that `castVotes` call.

### Proof of Concept
Foundry fork test plan:
1. Deploy/fork with `WombatBribeManager`, `WombatStaking`, `vlMGP`, and two active pools A and B.
2. Have `victim` lock a large amount of MGP, then `vote([B], [V])`.
3. Have `attacker` lock a dust amount of MGP (smallest unit), then `vote([A], [1])`.
4. Have `victim` call `unvote(B)`.
5. Have `attacker` (or anyone) call `castVotes(false)`.
6. Assert that the `votes` array passed to `wombatStaking.vote()` for Pool A equals (approximately) `totalVotes()` (the entire real veWOM balance of `wombatStaking`), while Pool A's real vlMGP backing (`attacker`'s locked amount) is a negligible fraction of `IVLMGP(vlMGP).totalLocked()` — demonstrating a disproportionate allocation of real Wombat gauge votes relative to attacker's actual locked stake.

### Citations

**File:** wombat/WombatBribeManager.sol (L155-157)
```text
    function totalVotes() public view returns (uint256) {
        return veWom.balanceOf(address(wombatStaking));
    }
```

**File:** wombat/WombatBribeManager.sol (L196-216)
```text
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
```

**File:** wombat/WombatBribeManager.sol (L218-219)
```text
        if (userTotalVotedInVlmgp[msg.sender] > getUserVotable(msg.sender))
            revert NotEnoughVote();
```

**File:** wombat/WombatBribeManager.sol (L223-237)
```text
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

**File:** wombat/WombatBribeManager.sol (L256-262)
```text
            uint256 currentVote = getVoteForLp(pool.poolAddress);
            uint256 targetVoteInLMGP = pool.totalVoteInVlmgp;
            uint256 targetVote = 0;

            if (totalVlMgpInVote != 0) {
                targetVote = targetVoteInLMGP * totalVotes() / totalVlMgpInVote;
            }
```

**File:** wombat/WombatBribeManager.sol (L271-276)
```text
        (address[][] memory rewardTokens, uint256[][] memory feeAmounts) = wombatStaking.vote(
            _pools,
            votes,
            rewarders,
            msg.sender
        );
```
