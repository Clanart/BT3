### Title
Reward Sniping via `vote()` immediately followed by `castVotes()` - allows an attacker to steal a share of an entire epoch's bribes with a same-block stake ([File: wombat/WombatBribeManager.sol])

### Summary
`castVotes()` in `wombat/WombatBribeManager.sol` is public, callable by anyone at any time, and triggers `wombatStaking.vote()` which harvests Wombat bribes and pushes them into each `BribeRewardPool` via `queueNewRewards()`/`_provisionReward()`. Because `_provisionReward()` in `rewards/BaseRewardPoolV2.sol` distributes the entire harvested bribe amount instantly and pro-rata over the pool's *current* `totalStaked()`, and `vote()` has no cooldown/minimum holding period, an attacker can call `vote()` to add stake and then immediately trigger `castVotes()` (e.g. atomically via `voteAndCast()`) to capture a share of bribes that accrued over the whole epoch, diluting long-term voters.

### Finding Description
- `vote(address[] _lps, int256[] _deltas)` [1](#0-0)  lets any address adjust its stake in a `BribeRewardPool` via `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta))` with no minimum holding period and no time-lock before the position counts toward reward distribution.
- `castVotes(bool swapForBnb)` [2](#0-1)  is `public`, has no access control, and can be called by anyone at any time; it calls `wombatStaking.vote(...)` which harvests Wombat bribes and (based on the staking contract's flow) results in `queueNewRewards` being called on each pool's `BribeRewardPool`.
- `queueNewRewards` → `_provisionReward` in `rewards/BaseRewardPoolV2.sol` [3](#0-2)  increments `rewardPerTokenStored` by `_amountReward * 10**decimals / totalStaked()` **instantly**, using whatever `totalStaked()` is at that exact block — there is no time-weighted/streamed distribution (e.g., no `rewardRate`/`periodFinish` mechanism).
- Because rewards are attributed strictly by current stake share at the moment of provisioning (not by how long a staker held their position during the accrual period), a staker who joined one block before `castVotes()` is treated identically to a staker who held the position for the entire epoch.
- `voteAndCast()` [4](#0-3)  lets an attacker perform `vote()` immediately followed by `castVotes()` in a single atomic transaction, guaranteeing their freshly added stake is present when `_provisionReward` executes.
- After the harvest, the attacker's `earned()` (computed by `_earned` in `rewards/BaseRewardPoolV2.sol`, lines 316-321) reflects this share, and they can withdraw/claim it via `unvote()` (or `vote()` with a negative delta) plus `claimBribeFor`/`getReward`, extracting real reward tokens that would otherwise have gone to voters who held their position the entire epoch.
- No existing modifier or check (no `nonReentrant` issue here, no minimum-stake-duration check, no per-epoch snapshotting) prevents this: `stakeFor`/`withdrawFor` in `rewards/BribeRewardPool.sol` [5](#0-4)  only guard against `onlyOperator` (the bribe manager itself, which any address can drive by calling public `vote`/`castVotes`).

Note: the specific framing in the question — that `earnedRewards` reported by `claimAllBribes` becomes inconsistent with the tokens actually transferred by `getReward` — does not hold in this code: `earned()`/`_earned()` and `getReward()`/`_getReward()` both read the same `rewardPerTokenStored`/`userRewardPerTokenPaid`/`userRewards` state, so the reported and transferred amounts stay reconciled. The real, exploitable defect is reward-share dilution from unvested reward distribution, not an accounting desync between the two functions.

### Impact Explanation
An attacker with capital to acquire/lock even a modest amount of vlMGP voting power can, in a single transaction (`voteAndCast`), capture a pro-rata share of an entire epoch's harvested bribes for a pool without having contributed to that pool's vote weight during the accrual period. This directly steals value from honest long-term voters who bore the opportunity cost of committing votes for the whole epoch, satisfying "Direct theft of user funds."

### Likelihood Explanation
This requires no special privileges — any EOA holding vlMGP (or able to acquire it) can call `vote()` and `voteAndCast()`/`castVotes()` at will, since these functions have no access control and no cooldown. It is repeatable every epoch/every time bribes are pending, and capital requirements scale only with the desired share of stolen rewards, not with any barrier in the code (no minimum stake duration is enforced anywhere in `WombatBribeManager.sol` or `BribeRewardPool.sol`).

### Recommendation
Introduce a minimum holding/vesting period between `vote()` (stake increase) and eligibility for rewards harvested by a subsequent `castVotes()` — e.g., snapshot voter weights at the start of an epoch and only allow newly added stake to participate in the *next* harvest cycle, or convert `_provisionReward` to a time-weighted streaming distribution (rewardRate over a fixed duration, à la Synthetix `StakingRewards`) instead of an instantaneous lump-sum `rewardPerTokenStored` bump. Additionally consider restricting who can trigger `castVotes()`/timing it on a fixed schedule to reduce griefing/sniping windows.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `WombatBribeManager`, a mock `WombatStaking`, a mock `IWombatVoter`, and a `BribeRewardPool` for a test pool; add the pool via `addPool`.
2. Victim locks vlMGP and calls `vote()` at epoch start, staking into the pool's `BribeRewardPool`.
3. Simulate epoch-long bribe accrual on the Wombat side (mock bribe contract accumulates a fixed reward amount to be harvested).
4. Immediately before triggering harvest, attacker acquires vlMGP and calls `voteAndCast(lps, deltas, false)` in one transaction: this both stakes attacker's delta into the pool and triggers `castVotes()`, which harvests the bribe and calls `queueNewRewards`/`_provisionReward` on the `BribeRewardPool`, splitting the harvested amount by current `totalStaked()` (victim + attacker).
5. Assert: `earned(attacker, rewardToken) > 0` despite attacker having staked in the same transaction/block as the harvest — i.e., the attacker captures `attackerStake / (attackerStake + victimStake)` of the full epoch's bribe.
6. Assert: `earned(victim, rewardToken)` is strictly less than it would have been had the attacker not sniped (i.e., less than the full harvested amount), demonstrating dilution/theft of victim's expected rewards.
7. Attacker calls `unvote()`/`vote()` with negative delta and `claimBribeFor`/`getReward` to withdraw the sniped tokens, confirming actual token transfer matches the diluted `earned()` value.

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

**File:** wombat/WombatBribeManager.sol (L241-296)
```text
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

        // comment outs for now since chainlink fails sometimes
        // if (swapForBnb) {
        //     finalFeeAmounts = new uint256[][](1);
        //     finalFeeAmounts[0] = new uint256[](1);
        //     finalFeeAmounts[0][0] = _swapFeesForBnb(rewardTokens, feeAmounts);
        //     finalRewardTokens = new address[][](1);
        //     finalRewardTokens[0] = new address[](1);
        //     finalRewardTokens[0][0] = address(0);
        // } else {
            _forwardRewards(rewardTokens, feeAmounts);
            finalRewardTokens = rewardTokens;
            finalFeeAmounts = feeAmounts;
        // }

        // send rewards to the delegate pool
        if (delegatedPool != address(0)) IDelegateVoteRewardPool(delegatedPool).harvestAll();

        emit VoteCasted(msg.sender, lastCastTime);
    }
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
