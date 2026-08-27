### Title
Voters can accrue bribe rewards for a pool while diverting the real Wombat vote elsewhere by switching votes right before `castVotes()` executes - ([File: wombat/WombatBribeManager.sol])

### Summary
`WombatBribeManager.vote()` lets any vlMGP holder freely add/remove their vote weight from any pool at any time, immediately staking/withdrawing in that pool's `BribeRewardPool` [1](#0-0) . The actual votes sent to the real Wombat `Voter` (which determines real gauge emissions) are only synced later, whenever someone calls the separate, permissionless `castVotes()` function, using whatever `poolInfos[lp].totalVoteInVlmgp` happens to be at that moment [2](#0-1) . Because bribe-reward accrual (`stakeFor`/`withdrawFor` on `BribeRewardPool`, time-weighted via `updateRewards`) is decoupled from the point-in-time snapshot used by `castVotes()` to compute real votes, a user can earn bribe rewards for Pool A the entire time between two `castVotes()` calls, then flip their vote to Pool B immediately before `castVotes()` executes, so the real Wombat vote/emissions for that period go to Pool B while the bribe payer of Pool A gets nothing for the votes they paid for.

### Finding Description
`vote()` has no epoch gating or cooldown analogous to `onlyNewEpoch` in the reference report; deltas are applied to `poolInfos[_lp].totalVoteInVlmgp` and mirrored into `IBribeRewardPool(pool.rewarder).stakeFor/withdrawFor` immediately [3](#0-2) . `BribeRewardPool.stakeFor`/`withdrawFor` update rewards via the `updateRewards` modifier inherited from `BaseRewardPoolV2`, meaning a user's bribe reward accrual tracks their real-time stake continuously, not a specific epoch snapshot [4](#0-3) .

Separately, `castVotes()` computes the target vote to actually send to Wombat's real `Voter` contract using `pool.totalVoteInVlmgp` and `totalVlMgpInVote` *at the time it is called* [5](#0-4) , and is callable by anyone at any time, with no guarantee it runs synchronously with every `vote()` call.

This produces the exact disconnect described in the reference report: the value that determines who *earns bribes* (continuous stake in `BribeRewardPool`) diverges from the value that determines *real emissions* (snapshot taken at `castVotes()` time). A user can:
1. Call `vote()` to allocate weight to Pool A right after a `castVotes()` execution, immediately starting to accrue Pool A's bribe rewards.
2. Hold that vote (and rewards accrual) for the entire interval until the next `castVotes()` call is about to be triggered.
3. Front-run/precede the next `castVotes()` call with another `vote()` that shifts their weight from Pool A to Pool B.
4. When `castVotes()` executes, the real vote sent to Wombat reflects Pool B, not Pool A — so Pool A's real votes/emissions for that period are lower than what its bribe budget was based on, while the user still walks away with the bribe rewards accrued from Pool A during the whole interval.

### Impact Explanation
Bribing protocols pay bribes into a pool's `BribeRewardPool` expecting that the vlMGP stake behind it produces real votes/emissions from Wombat. This mechanism allows voters to collect bribes for a pool without their vote ultimately contributing (at `castVotes()` time) to that pool's real emissions, since they can switch away right before the cast. This undermines the intended incentive alignment between bribes paid and gauge emissions received, causing bribing protocols to lose value and trust in the system — matching the "governance voting result manipulation" / bribe-emission mismatch impact class from the reference report.

### Likelihood Explanation
`vote()` is a fully permissionless, unprivileged-wallet function with no epoch lock, and `castVotes()` is also callable by any address at any time (it's explicitly a "gas intensive" function anyone can call for a fee) [6](#0-5) . Any vlMGP holder can watch the mempool/monitor `lastCastTime` and time their vote flips accordingly, requiring no special privileges — this is realistically exploitable by an ordinary user.

### Recommendation
Introduce a mechanism to align bribe-reward accrual with the vote snapshot actually used by `castVotes()` — for example, only allow reward accrual to start/stop at `castVotes()` boundaries (snapshot-based accounting), or lock a user's vote allocation for a minimum period covering at least one `castVotes()` cycle before allowing changes, similar to the `onlyNewEpoch` protection recommended in the reference finding.

### Proof of Concept
1. Pool A and Pool B both onboarded via `addPool()`.
2. Right after a `castVotes()` call, user calls `vote([poolA], [+X])`, staking X vlMGP-equivalent weight into Pool A's `BribeRewardPool`, immediately starting bribe accrual per `stakeFor` [7](#0-6) .
3. Bribe rewards accrue to the user in Pool A's rewarder over the entire interval (per `BaseRewardPoolV2`'s time-weighted accounting).
4. Immediately before the next `castVotes()` transaction is mined, user calls `vote([poolA, poolB], [-X, +X])`, moving `poolInfos[poolA].totalVoteInVlmgp` down and `poolInfos[poolB].totalVoteInVlmgp` up [3](#0-2) .
5. `castVotes()` executes and computes `targetVote` for Wombat's real `Voter` from the now-updated `totalVoteInVlmgp`, sending Pool B's (not Pool A's) share of real votes to Wombat [8](#0-7) .
6. User calls `claimBribe([poolA])` / `claimAllBribes()` and receives the full bribe rewards accrued in Pool A's `BribeRewardPool` for the period, despite Pool A's real Wombat vote/emissions for that period reflecting Pool B's diverted weight instead.

### Citations

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

**File:** wombat/WombatBribeManager.sol (L239-276)
```text
    /// @notice cast all pending votes
    /// @notice this  function will be gas intensive, hence a fee is given to the caller
    function castVotes(bool swapForBnb)
        override public
        returns (address[][] memory finalRewardTokens, uint256[][] memory finalFeeAmounts)
    {
        lastCastTime = block.timestamp;
        uint256 length = pools.length;
        address[] memory _pools = new address[](length);
        int256[] memory votes = new int256[](length);
        address[] memory rewarders = new address[](length);

        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[pools[i]];
            _pools[i] = pool.poolAddress;
            rewarders[i] = pool.rewarder;

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

        (address[][] memory rewardTokens, uint256[][] memory feeAmounts) = wombatStaking.vote(
            _pools,
            votes,
            rewarders,
            msg.sender
        );
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
