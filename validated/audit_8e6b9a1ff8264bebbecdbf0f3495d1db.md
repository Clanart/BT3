## Title
Unfair distribution of rewards via instantaneous `rewardPerTokenStored` update in `donateRewards`/`queueNewRewards` - (File: `rewards/BaseRewardPoolV2.sol`)

### Summary
`BaseRewardPoolV2` (the base contract used by `BribeRewardPool`, which backs `WombatBribeManager` gauge voting) distributes rewards by immediately baking a newly deposited amount into `rewardPerTokenStored`, in proportion to whatever `totalStaked()` happens to be at that exact moment. This is the same bug class as the reported THENA `VoterV3._notifyRewardAmount` issue: rewards are allocated based on a transient, manipulable snapshot of "votes" (stake), instead of being distributed proportionally to genuine, sustained participation over an epoch.

### Finding Description
`_provisionReward` (called by both the permissionless `donateRewards` and the manager-only `queueNewRewards`) computes: [1](#0-0) 

If `totalStaked()` is zero when reward tokens are provisioned, the amount is stashed in `queuedRewards` and only converted into `rewardPerTokenStored` the next time provisioning happens while `totalStaked() > 0`. Crucially, that conversion divides the *entire* `queuedRewards + _amountReward` by whatever `totalStaked()` is at that single instant — not by any time-weighted average of votes over the relevant period.

`BribeRewardPool.stakeFor`/`withdrawFor` (called from `WombatBribeManager.vote()`/`unvote()`) let a user instantly change their staked "vote" balance for a pool, and `donateRewards` is fully permissionless (no access control beyond the reward token being registered): [2](#0-1) [3](#0-2) 

`WombatBribeManager.vote`/`unvote` allow a wallet to freely and immediately move vote weight into and out of a pool's `BribeRewardPool`, with no epoch lock, cooldown, or minimum holding period: [4](#0-3) 

Because `_provisionReward` bakes rewards in at the instant `totalStaked()` is sampled (rather than at the end of a fixed voting epoch, and without any time-weighting), an attacker can:
1. Front-run/target a pool where no one is currently voting (`totalStaked() == 0`) so a previous bribe deposit sits fully in `queuedRewards`, or a pool where votes are currently thin.
2. Call `vote()` to stake a large vlMGP vote weight into that pool's `BribeRewardPool` in the same transaction/block.
3. Call `donateRewards` (or trigger `queueNewRewards` via `castVotes`) with a trivial amount, which flushes the entire `queuedRewards` balance and divides it by `totalStaked()` — now dominated by the attacker's freshly-added stake.
4. Call `unvote()` immediately afterward to withdraw the vote weight, while the `rewardPerTokenStored` increase (and thus the reward entitlement) remains permanently credited to the attacker via `userRewardPerTokenPaid`/`userRewards` snapshotting in `_updateFor`/`updateRewards`.

This mirrors exactly the reported bug class: reward index updates use "the current number of votes" instead of a finalized, epoch-end distribution, letting someone who did not meaningfully/durably participate capture (or dilute) a disproportionate share of the rewards intended for genuine, continuous voters.

### Impact Explanation
Legitimate voters who maintain their vote/stake for the full bribe/voting cycle have their fair share of `queuedRewards` (or newly injected `donateRewards`) diluted or entirely captured by a transient staker who enters and exits within the same transaction or block. This constitutes theft/misdirection of unclaimed yield that rightfully belongs to durable voters, satisfying the "theft or permanent freezing of unclaimed yield" impact bar.

### Likelihood Explanation
`donateRewards` is unauthenticated and callable by any wallet holding the reward token, and `vote()`/`unvote()` in `WombatBribeManager` have no cooldown or epoch lock preventing same-block stake-in/stake-out. The only precondition is that a pool has low or zero current `totalStaked()` (i.e., few active voters) at the moment reward provisioning is triggered — a state that is easily observable on-chain and can be engineered by simply picking an under-voted pool, making this practically exploitable by any ordinary wallet.

### Recommendation
Distribute rewards using a time-weighted mechanism (e.g., a fixed reward-rate-over-duration model like Synthetix's `StakingRewards`, or accrue rewards only against balances that were staked for the full accounting window) rather than instantaneously converting an entire reward deposit into `rewardPerTokenStored` based on the stake balance at a single block. At minimum, enforce a minimum staking duration or epoch-based snapshotting before a voter's stake counts toward reward-per-token calculations for newly injected rewards.

### Proof of Concept
1. Pool `P` currently has `totalStaked() == 0` in its `BribeRewardPool`, with `queuedRewards = X` (from an earlier `queueNewRewards` call while no one voted), per `_provisionReward`: [5](#0-4) .
2. Attacker calls `WombatBribeManager.vote([P], [largeDelta])`, which calls `BribeRewardPool.stakeFor(attacker, largeDelta)`, making `totalStaked() = largeDelta`: [6](#0-5)  and [7](#0-6) .
3. Attacker calls `donateRewards(1, rewardToken)` (or anyone triggers `queueNewRewards`), flushing `queuedRewards = X` into `rewardPerTokenStored` divided by `totalStaked() = largeDelta` (dominated by attacker's own stake): [8](#0-7) .
4. Attacker calls `WombatBribeManager.unvote(P)`, immediately withdrawing the vote weight via `withdrawFor`, while `userRewards`/`userRewardPerTokenPaid` already captured the attacker's share of `X`: [9](#0-8)  and [10](#0-9) .
5. Attacker later calls `claimBribe([P])` to withdraw the captured reward share, having contributed negligible durable vote weight to pool `P`.

**Note on verification:** I could not fully confirm the absence of any epoch/cooldown guard in `WombatBribeManager` beyond what `grep_search` matched (`lastCastTime`, no lock found on `vote`/`unvote`); a Devin session with full file access would be needed to double check there isn't an implicit lock elsewhere (e.g., in `VLMGP.sol` or `MasterMagpie.sol`) that would prevent same-block vote/unvote cycles.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L252-260)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
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

**File:** wombat/WombatBribeManager.sol (L180-237)
```text
    /// @notice Vote on pools. Need to compute the delta prior to casting this.
    /// @param _deltas delta amount in vlMGP
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
