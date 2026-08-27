Confirmed: `WombatStaking.vote` (called by `harvestSinglePool`) claims bribes from the Wombat `Voter`/bribe contracts and calls `IBaseRewardPool(rewarder).queueNewRewards(rewardAmount, token)` [1](#0-0) , which flows into `BaseRewardPoolV2._provisionReward`, distributing the newly harvested bribe pro-rata over `totalStaked()` **at the moment of the call**, via a single reward-per-token snapshot update, with no time-weighting or vesting [2](#0-1) .

### Title
Just-in-time vote stake before harvestSinglePool lets an attacker steal a pro-rata share of accrued bribes with zero time exposure - (File: wombat/WombatBribeManager.sol, rewards/BaseRewardPoolV2.sol, wombat/WombatStaking.sol)

### Summary
`WombatBribeManager.vote()` immediately credits a voter's stake in the pool's `BribeRewardPool` via `stakeFor`, checkpointing the caller's `userRewardPerTokenPaid` to the pool's current `rewardPerTokenStored` [3](#0-2) [4](#0-3) . If the attacker calls `vote(pool, +X)` and then immediately `harvestSinglePool([pool])` (or anyone else does, since it's permissionless), the harvested bribe is distributed pro-rata to `totalStaked()` at that instant, which now includes the attacker's freshly added `X`, entitling them to a share of rewards that accrued from bribes deposited by third parties over the prior period, despite zero blocks of real exposure. This lets an attacker with just-purchased/borrowed vlMGP extract meaningful yield that should have accrued only to genuine long-term voters.

### Finding Description
- `vote()` calls `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, delta)` synchronously, updating `_balances`/`totalSupply` and setting `userRewardPerTokenPaid[token][attacker] = rewardPerToken(token)` (pre-harvest value) via the `updateRewards` modifier [3](#0-2) [5](#0-4) .
- `harvestSinglePool(_lps)` is a public, unprivileged function that passes zero-deltas to `wombatStaking.vote(...)`, which still triggers `voter.vote()` on the underlying Wombat `Voter`, harvesting pending bribes for the pool [6](#0-5) .
- `WombatStaking.vote` forwards the harvested bribe tokens to the rewarder via `queueNewRewards`, which calls `_provisionReward`, incrementing `rewardPerTokenStored` by `amount * 10**decimals / totalStaked()` at that single instant [1](#0-0) [2](#0-1) .
- Because the attacker's balance is already included in `totalStaked()` and their checkpoint (`userRewardPerTokenPaid`) is from before this increment, `_earned()` correctly computes a nonzero claim proportional to `X / totalStaked()` for the entire freshly-added `rewardPerTokenStored` delta — i.e., a share of bribes that logically accrued during a period they weren't staked [7](#0-6) .
- `claimBribe([pool])` then calls `getReward`, paying out `earned()` immediately [8](#0-7) [9](#0-8) .
- `unvote(pool)` (or a negative `vote` delta) then withdraws the stake, leaving the attacker with zero risk/duration exposure but a captured share of rewards [10](#0-9) .
- No cooldown, minimum staking duration, deposit/withdraw fee, or streaming/vesting of rewards exists to prevent this snapshot-based dilution; the `updateRewards` modifier and reward-per-token accounting are functioning exactly as coded (standard Synthetix-style pool), which is precisely what enables the attack in a system where large discrete reward injections occur on-demand and stake changes are instantaneous and free.

### Impact Explanation
This is a **theft of unclaimed yield** from all existing pool voters: any bribe amount harvested via `harvestSinglePool` is diluted across `totalStaked()` including the attacker's freshly minted stake, meaning genuine long-term voters permanently receive a smaller share of that specific harvest than they should have. This matches Immunefi's "theft of unclaimed yield" impact class. The magnitude scales with `X / totalStaked()` and the freshly-harvested bribe size; a well-capitalized/large-vlMGP attacker (or a coalition/flash-borrowed vlMGP-holding position, if such exists) can extract a disproportionate share for a single harvest, repeatable every time new bribes accrue.

### Likelihood Explanation
- Requires only holding/locking some vlMGP (a normal, permissionless user action) and does not require any privileged role.
- `harvestSinglePool` is `public` and callable by anyone including the attacker themselves, no cooldown or access control gates it.
- The sequence `vote(+X) -> harvestSinglePool -> claimBribe -> unvote` can be done atomically in one transaction/one block using a single caller, so no real front-running risk (MEV/reorg) is even required — an attacker can self-execute this as a single tx bundle.
- Repeatable every time a pool accumulates a harvestable bribe balance, limited only by the attacker's available vlMGP.
- Practical constraint: `vote`/`unvote` changes are bounded by `getUserVotable`, and voting power (vlMGP) generally requires locking MGP for a period, which raises capital cost but doesn't prevent the JIT extraction technique itself since the position doesn't need to be new — an existing vlMGP holder can reallocate votes into the target pool right before harvesting.

### Recommendation
- Decouple bribe distribution from instantaneous stake snapshots: stream harvested rewards linearly over a fixed period (e.g., a Synthetix-style `rewardRate`/`periodFinish` mechanism) instead of crediting the full harvested amount to `rewardPerTokenStored` in one shot.
- Alternatively/additionally, enforce a minimum vote-lock duration or cooldown before newly added votes become eligible for the *next* harvest's rewards (e.g., snapshot voters at the start of the epoch, not at harvest time).
- Consider a withdrawal/harvest-adjacent fee or requiring `vote` deltas to only take effect at the following harvest checkpoint.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, `WombatStaking`, `BribeRewardPool`, and mock `Voter`/`Bribe` contracts that return a large pending bribe amount for a target pool.
2. Set up "long-term voter" (Alice) who calls `vote(pool, +1000)` and holds for N blocks without harvesting.
3. Have the mock bribe contract accrue a bribe reward (e.g., 1000 WOM) pending for the pool.
4. Attacker (Bob), starting with zero prior position, in a single transaction (or same block):
   a. `vote(pool, +1000)` (matching Alice's stake, doubling `totalStaked()`),
   b. `harvestSinglePool([pool])` — triggers `queueNewRewards(1000 WOM)`, splitting `rewardPerTokenStored` 50/50 between Alice and Bob's balances,
   c. `claimBribe([pool])` — assert Bob receives ~500 WOM despite zero prior blocks staked,
   d. `unvote(pool)` — Bob exits with no vote exposure going forward.
5. Assert: `bobReward > 0` and `bobReward ≈ aliceReward` (both ~500 WOM) even though Alice was staked for N blocks and Bob for 0 net time, proving reward dilution independent of stake duration.
6. Assert Alice's `claimBribe` after the same harvest yields only ~500 WOM instead of the full 1000 WOM she would have received had Bob not front-run the harvest.

### Citations

**File:** wombat/WombatStaking.sol (L403-411)
```text
                        if (caller != address(0) && bribeCallerFee != 0) {
                            callerFeeAmount = (rewardAmount * bribeCallerFee) / DENOMINATOR;
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeManager, callerFeeAmount);
                        }

                        rewardAmount -= protocolFee;
                        rewardAmount -= callerFeeAmount;
                        IERC20(rewardTokens[i][j]).safeApprove(_rewarders[i], rewardAmount);
                        IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, address(rewardTokens[i][j]));
```

**File:** rewards/BaseRewardPoolV2.sol (L107-120)
```text
    modifier updateRewards(address _account, address[] memory _rewards) {
        uint256 length = _rewards.length;
        uint256 userShare = balanceOf(_account);
        
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewards[index];
            // if a reward stopped queuing, no need to recalculate to save gas fee
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;
            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userShare);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
        _;
    }    
```

**File:** rewards/BaseRewardPoolV2.sol (L218-235)
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
            uint256 reward = userRewards[rewardToken][_account]; // updated during updateReward modifier
            if (reward > 0) {
                _sendReward(rewardToken, _account, _receiver, reward);
            }
        }

        return true;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L290-314)
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
    }
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

**File:** wombat/WombatBribeManager.sol (L196-199)
```text
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
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

**File:** wombat/WombatBribeManager.sol (L298-311)
```text
    /// @notice Cast a zero vote to harvest the bribes of selected pools
    /// @notice this  function has a lesser importance than casting votes, hence no rewards will be given to the caller.
    function harvestSinglePool(address[] calldata _lps) public {
        uint256 length = _lps.length;
        int256[] memory votes = new int256[](length);
        address[] memory rewarders = new address[](length);
        for (uint256 i; i < length; i++) {
            address lp = _lps[i];
            Pool storage pool = poolInfos[lp];
            rewarders[i] = pool.rewarder;
            votes[i] = 0;
        }
        wombatStaking.vote(_lps, votes, rewarders, address(0));
    }
```

**File:** wombat/WombatBribeManager.sol (L326-328)
```text
    function claimBribe(address[] calldata lps) public {
        _claimBribeFor(lps, msg.sender);
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
