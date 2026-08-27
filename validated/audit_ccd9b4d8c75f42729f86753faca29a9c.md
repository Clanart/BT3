## Title
Bribe reward sniping via costless instant vote reallocation before `harvestSinglePool` - (File: `wombat/WombatBribeManager.sol`, `rewards/BaseRewardPoolV2.sol`)

## Summary
`harvestSinglePool` harvests bribes that accrued to a Wombat pool based on the *real* votes previously cast to the Wombat `voter` contract (via `castVotes`), but the harvested amount is distributed to the pool's internal `BribeRewardPool` stakers strictly pro‑rata to their *instantaneous* `totalStaked()` at the moment `queueNewRewards` is called. Since `WombatBribeManager.vote()` lets any existing locked‑vlMGP holder move their internal vote allocation between active pools instantly, with no cooldown, fee, or vesting, an attacker can reallocate a large existing vlMGP position into a target pool immediately before triggering `harvestSinglePool`, capture a disproportionate share of that harvested tranche, and reallocate away right after — repeating this for every harvest cycle at near-zero cost.

## Finding Description
`harvestSinglePool` (`wombat/WombatBribeManager.sol:300-311`) calls `wombatStaking.vote(_lps, zeroVotes, rewarders, address(0))`. Because the delta array is all zero, this call does not change the real votes registered on Wombat's `voter` contract — it merely triggers harvesting of whatever bribe has accrued for the pool since the last vote/harvest (`wombat/WombatStaking.sol:363-418`), and forwards it via `IBaseRewardPool(_rewarders[i]).queueNewRewards(...)`.

`queueNewRewards` → `_provisionReward` (`rewards/BaseRewardPoolV2.sol:290-314`) immediately folds the entire harvested amount into `rewardPerTokenStored` using the pool's *current* `totalStaked()`:
```
rewardInfo.rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked();
```
There is no vesting duration or time-weighting — the whole tranche is instantly credited proportional to whoever is staked at that exact block.

Meanwhile, `WombatBribeManager.vote()` (`wombat/WombatBribeManager.sol:182-220`) lets any user reallocate delta amounts of their already-locked vlMGP between active pools at will:
```
if (delta > 0) { ...; IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta)); }
else { ...; IBribeRewardPool(pool.rewarder).withdrawFor(msg.sender, uint256(-delta), false); }
```
The only constraint is `userTotalVotedInVlmgp[msg.sender] <= getUserVotable(msg.sender)` (total locked vlMGP), enforced at `wombat/WombatBribeManager.sol:218-219`. There is no cooldown, minimum holding period, or fee on this reallocation between active pools (`unvote` restrictions only apply to inactive pools).

`BribeRewardPool.stakeFor`/`withdrawFor` (`rewards/BribeRewardPool.sol:57-85`) update `userRewardPerTokenPaid` to the pool's current `rewardPerTokenStored` *before* the harvest injects new rewards, and update balances immediately with no lockup. Consequently, a user who moves a large chunk of their vlMGP into a pool right before `harvestSinglePool` is called captures a share of the just-harvested bribe proportional to their instantaneous stake, then can reallocate away immediately afterward with no penalty — even though that bribe accrued over time from votes cast by other users through `castVotes`.

This bypasses the intended economic invariant that bribe rewards should be split among the users whose real voting power (cast to Wombat) generated them over time, not among whoever happens to hold the largest instantaneous internal vote share at the harvest transaction.

## Impact Explanation
Honest long-term voters who kept their vlMGP allocated to a pool the entire accrual period have their proportional share of harvested bribes diluted every time an attacker performs this stake-snipe-unstake cycle around a `harvestSinglePool` call. This is a repeatable **theft of unclaimed yield** from honest voters: the attacker extracts value that legitimately belongs to holders whose sustained votes on the real Wombat protocol generated the bribe, in favor of a transient staker who contributed no time-weighted voting power. Over N harvest cycles this compounds, matching the Immunefi "theft of unclaimed yield" impact class.

## Likelihood Explanation
- Preconditions: attacker needs to already hold and lock some MGP as vlMGP (`getUserTotalLocked`), which is a real, non-flash-loanable position (locking/unlocking vlMGP has its own schedule), but no time-lock exists on *reallocating* votes among already-active pools via `vote()`.
- `harvestSinglePool` is `public`/permissionless (`wombat/WombatBribeManager.sol:300`), so the attacker can trigger the harvest themselves immediately after concentrating their vote, guaranteeing same-block/near-atomic execution.
- No fee, cooldown, or minimum-stake-duration currently blocks this reallocate→harvest→reallocate pattern, so it is fully repeatable at negligible marginal cost (just gas) across every harvest cycle for any pool the attacker has enough locked vlMGP to dominate momentarily.
- The larger the attacker's locked vlMGP relative to a given pool's total internal vote, the larger their captured share — this scales with capital already committed to the system, not with sustained voting contribution.

## Recommendation
Decouple bribe distribution from instantaneous stake snapshots: either (a) vest/stream each `queueNewRewards` tranche over a fixed duration (Synthetix-style `rewardRate`/`periodFinish`) so momentary stakers cannot capture a full tranche instantly, or (b) impose a minimum holding/cooldown period on vlMGP vote reallocation (`vote`) before a user's stake becomes eligible for a newly queued reward, or (c) checkpoint/weight rewards by time-integrated stake instead of point-in-time `totalStaked()`.

## Proof of Concept
Foundry test outline:
1. Deploy `WombatBribeManager`, `WombatStaking`, `BribeRewardPool`, and mock `voter`/`veWom`/bribe contracts that accrue a fixed bribe amount per `vote()` call regardless of delta.
2. Set up two vlMGP holders: `honestVoter` locks `1000 vlMGP` and allocates it to `poolX` at t0 and never touches it; `attacker` locks `1000 vlMGP` but keeps it allocated to `poolY` (inactive in the snipe) until just before each harvest.
3. Loop N times:
   - `attacker.vote([poolY, poolX], [-1000, +1000])` to move all vlMGP into `poolX` right before harvest.
   - Call `bribeManager.harvestSinglePool([poolX])`, which queues a fixed bribe `R` into `poolX`'s `BribeRewardPool`.
   - `attacker.vote([poolX, poolY], [-1000, +1000])` immediately after, moving vote back out.
4. After N cycles, call `claimBribe` for both `honestVoter` and `attacker`.
5. Assert `attacker`'s claimed bribe ≈ `N * R * (1000/(1000+honestVoterStakeAtHarvest))` — i.e., attacker captures close to 50%+ of every tranche despite contributing zero time-weighted voting power to `poolX`, while `honestVoter`, who held the vote in `poolX` continuously, receives far less than the proportional share their sustained contribution should earn. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** wombat/WombatStaking.sol (L363-418)
```text
    function vote(
        address[] calldata _lpVote,
        int256[] calldata _deltas,
        address[] calldata _rewarders,
        address caller
    ) external returns (IERC20[][] memory rewardTokens, uint256[][] memory callerFeeAmounts) {
        if(msg.sender != bribeManager)
            revert OnlyBribeMamager();
            
        if (_lpVote.length != _rewarders.length || _lpVote.length != _deltas.length)
            revert LengthMismatch();
        uint256[][] memory rewardAmounts = voter.vote(_lpVote, _deltas);
        rewardTokens = new IERC20[][](rewardAmounts.length);
        callerFeeAmounts = new uint256[][](rewardAmounts.length);

        for (uint256 i; i < rewardAmounts.length; i++) {

            address bribesContract = address(voter.infos(_lpVote[i]).bribe);

            if (bribesContract != address(0)) {
                rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens();
                callerFeeAmounts[i] = new uint256[](rewardAmounts[i].length);

                for (uint256 j; j < rewardAmounts[i].length; j++) {
                    uint256 rewardAmount = rewardAmounts[i][j];
                    uint256 callerFeeAmount = 0;

                    if (rewardAmount > 0) {
                        // if reward token is bnb, wrap it first
                        if (address(rewardTokens[i][j]) == address(0)) {
                            Address.sendValue(payable(wbnb), rewardAmount);
                            rewardTokens[i][j] = IERC20(wbnb);
                        }

                        uint256 protocolFee = (rewardAmount * bribeProtocolFee) / DENOMINATOR;

                        if (protocolFee > 0) {
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeFeeCollector, protocolFee);
                        }

                        if (caller != address(0) && bribeCallerFee != 0) {
                            callerFeeAmount = (rewardAmount * bribeCallerFee) / DENOMINATOR;
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeManager, callerFeeAmount);
                        }

                        rewardAmount -= protocolFee;
                        rewardAmount -= callerFeeAmount;
                        IERC20(rewardTokens[i][j]).safeApprove(_rewarders[i], rewardAmount);
                        IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, address(rewardTokens[i][j]));
                    }

                    callerFeeAmounts[i][j] = callerFeeAmount;
                }
            }
        }
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
