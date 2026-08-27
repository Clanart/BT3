### Title
Blacklisted/reverting reward token forces `MasterMagpie._multiClaim()` and `WombatBribeManager.claimAllBribes()` to revert, permanently freezing rewards across all unrelated pools - (File: rewards/MasterMagpie.sol, wombat/WombatBribeManager.sol)

### Summary
`MasterMagpie._multiClaim()` and `WombatBribeManager.claimAllBribes()`/`_claimBribeFor()` iterate across multiple staking pools/bribe pools in a single transaction and perform direct `safeTransfer` payouts to the receiving address for each reward token via `BaseRewardPool.getReward()`/`BaseRewardPoolV2._sendReward()` and `IBribeRewardPool.getReward()`. If any single reward token used in any one of the many pools is a blacklist-capable token (e.g. USDC) and the receiving address is blacklisted for that one token, the `safeTransfer` reverts, and because there is no isolation (no try/catch, no claims-later pattern), the revert propagates and reverts the entire batched transaction — blocking the user from claiming ALL of their MGP and reward tokens across every other pool as well, not just the one associated with the offending token.

### Finding Description
`_multiClaim` loops through `_stakingTokens` and for each one calls `_claimBaseRewarder`, which calls `rewarder.getReward(_account, _receiver)` [1](#0-0) [2](#0-1) .

`BaseRewardPool.getReward()` itself loops over ALL registered `rewardTokens` for the pool and does an unconditional `safeTransfer` to `_receiver` for each non-zero reward, with no isolation between tokens: [3](#0-2) 

The V2 rewarder (`BaseRewardPoolV2`) has the same pattern via `_sendReward` doing a direct `safeTransfer`: [4](#0-3) 

Similarly, `WombatBribeManager.claimAllBribes()` loops across every pool the user voted for and calls `IBribeRewardPool(pool.rewarder).getReward(_for, _for)` directly, again with no isolation: [5](#0-4)  and `_claimBribeFor` similarly loops calling `getReward` for each lp: [6](#0-5) 

Because both `_multiClaim` and `claimAllBribes`/`_claimBribeFor` process multiple, independent pools and reward tokens in one atomic call and use push-style (`safeTransfer`) distribution rather than a pull/claim-bookkeeping pattern, a single blacklist-capable reward token (e.g. USDC used as a bribe/reward token in one pool) reverting for one recipient blocks the claim of every other unrelated, non-blacklisted reward token/pool bundled in the same call.

### Impact Explanation
Once any of a user's staking pools or voted bribe pools contains a blacklist-enabled reward token (e.g. USDC) and that user's receiving address becomes blacklisted (whether by external circumstance or via a remapped/attacker-chosen receiver, since `_multiClaim`/`claimAllBribes` accept an arbitrary `_receiver`/`_for`), the user's legitimate, unrelated MGP rewards and bribe rewards across all other pools become permanently unclaimable in the standard claim path, since the batched transaction always reverts. This is a permanent freezing-of-funds condition for the affected user's already-earned/unclaimed yield, matching the accepted bug class in the source report (frozen rewards due to blacklist-capable token in a batched push-transfer flow).

### Likelihood Explanation
Likelihood is moderate: it requires (1) at least one reward/bribe token in the pool set to be a blacklist-capable stablecoin such as USDC (plausible given Wombat/Magpie ecosystems commonly use USDC-pegged bribe/reward tokens), and (2) the receiving address to end up blacklisted. No privileged role is required — any ordinary user calling the standard multi-pool claim/harvest functions with an arbitrary `_receiver` can trigger or be affected by this condition, and no admin action is needed to reproduce the DOS once the precondition token/blacklist state exists.

### Recommendation
Isolate per-token/per-pool transfer failures so that a single reverting `safeTransfer` cannot block unrelated claims:
- Wrap each `safeTransfer` in `BaseRewardPool.getReward()`, `BaseRewardPoolV2._sendReward()`, and `WombatBribeManager`'s bribe-claim loops in a low-level call with try/catch (or use OpenZeppelin's `Address.functionCall` pattern) and skip/queue failed transfers instead of reverting.
- Adopt a claims/accounting pattern (as recommended in the source report) where a failed transfer credits `claimable[token][user] += amount`, allowing the user to retry the transfer separately without blocking harvesting of other tokens/pools in the same transaction.

### Proof of Concept
1. Magpie team adds a pool whose `BaseRewardPool`/`BaseRewardPoolV2` includes USDC (or another blacklist-capable token) as a reward token, or `WombatBribeManager` has a pool with a USDC bribe reward via `IWombatBribe`.
2. A user stakes in this pool as well as several other independent pools (e.g. MGP pool, vlMGP pool, other LP pools) that do not use blacklist-capable tokens.
3. The user's designated receiver address (which can differ from `msg.sender` since `_receiver` is passed explicitly to `_multiClaim`/`claimAllBribes`) becomes blacklisted by the USDC issuer (Circle) for any reason.
4. The user (or anyone) calls the multi-pool claim function that internally invokes `_multiClaim` (looping all staking tokens) — the loop reaches the pool with the blacklisted USDC reward and `IERC20(rewardToken).safeTransfer(_receiver, reward)` in `BaseRewardPool.getReward()` reverts [7](#0-6) .
5. The revert bubbles up through `_claimBaseRewarder` and `_multiClaim`, reverting the entire transaction — the user cannot claim their MGP or other non-blacklisted reward tokens from any of the other pools bundled in the same call, indefinitely, until the blacklist token is removed from that pool or the receiver is unblacklisted.
6. The same scenario applies to `WombatBribeManager.claimAllBribes()`/`claimBribeFor()`, where one blacklisted bribe reward token in one voted pool blocks bribe claims from every other pool the user has voted on: [8](#0-7)

### Citations

**File:** rewards/MasterMagpie.sol (L536-562)
```text
    function _multiClaim(address[] calldata _stakingTokens, address _user, address _receiver, address[][] memory _rewardTokens) internal nonReentrant {
        uint256 length = _stakingTokens.length;
        if (length != _rewardTokens.length) revert LengthMismatch();

        uint256 vlMGPPoolAmount;
        uint256 mWOmPoolAmount;
        uint256 defaultPoolAmount;

        for (uint256 i = 0; i < length; ++i) {
            address _stakingToken = _stakingTokens[i];
            UserInfo storage user = userInfo[_stakingToken][_user];
            
            updatePool(_stakingToken);
            uint256 claimableMgp = _calNewMGP(_stakingToken, _user) + unClaimedMgp[_stakingToken][_user];

            if (_stakingToken == address(vlmgp)) {
                vlMGPPoolAmount += claimableMgp;
            } else if (MPGRewardPool[_stakingToken]) {
                mWOmPoolAmount += claimableMgp;
            } else {
                defaultPoolAmount += claimableMgp;
            }

            unClaimedMgp[_stakingToken][_user] = 0;
            user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
            _claimBaseRewarder(_stakingToken, _user, _receiver, _rewardTokens[i]);
        }
```

**File:** rewards/MasterMagpie.sol (L618-628)
```text
    /// @notice Harvest reward token in BaseRewarder for an account. NOTE: Baserewarder use user staking token balance as source to
    /// calculate reward token amount
    function _claimBaseRewarder(address _stakingToken, address _account, address _receiver, address[] memory _rewardTokens) internal {
        IBaseRewardPool rewarder = IBaseRewardPool(tokenToPoolInfo[_stakingToken].rewarder);
        if (address(rewarder) != address(0)) {
            if (_rewardTokens.length > 0)
                rewarder.getRewards(_account, _receiver, _rewardTokens);
            else
                // if not specifiying any reward token, just claim them all
                rewarder.getReward(_account, _receiver);
        }
```

**File:** rewards/BaseRewardPool.sol (L219-240)
```text
    /// @notice Calculates and sends reward to user. Only callable by masterMagpie
    /// @param _account Address account
    function getReward(address _account, address _receiver)
        override
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
                userRewards[rewardToken][_account] = 0;
                IERC20(rewardToken).safeTransfer(_receiver, reward);
                emit RewardPaid(_account, _receiver, reward, rewardToken);
            }
        }

        return true;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L323-327)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver, uint256 _amount) internal {
        userRewards[_rewardToken][_account] = 0;
        IERC20(_rewardToken).safeTransfer(_receiver, _amount);
        emit RewardPaid(_account, _receiver, _amount, _rewardToken);
    }
```

**File:** wombat/WombatBribeManager.sol (L339-368)
```text
    function claimAllBribes(address _for)
        override public
        returns (address[] memory rewardTokens, uint256[] memory earnedRewards)
    {
        address[] memory delegatePoolRewardTokens;
        uint256[] memory delegatePoolRewardAmounts;
        if (userVotedForPoolInVlmgp[_for][delegatedPool] > 0) {
            (delegatePoolRewardTokens, delegatePoolRewardAmounts) = IDelegateVoteRewardPool(delegatedPool)
                .getReward(_for);
        }

        uint256 length = pools.length;
        rewardTokens = new address[](length + delegatePoolRewardTokens.length);
        earnedRewards = new uint256[](length + delegatePoolRewardTokens.length);

        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[pools[i]];
            address lp = pool.poolAddress;
            address bribesContract = address(voter.infos(lp).bribe);
            if (bribesContract != address(0)) {
                rewardTokens[i] = address(IWombatBribe(bribesContract).rewardTokens()[0]);
                // skip the which pool not in voting to save gas
                if (userVotedForPoolInVlmgp[_for][lp] > 0) {
                    earnedRewards[i] = IBribeRewardPool(pool.rewarder).earned(_for, rewardTokens[i]);
                    if (earnedRewards[i] > 0) {
                        IBribeRewardPool(pool.rewarder).getReward(_for, _for);
                    }
                }
            }
        }
```

**File:** wombat/WombatBribeManager.sol (L399-406)
```text
    /// @notice Harvests user rewards for each pool
    /// @notice If bribes weren't harvested, this might be lower than actual current value
    function _claimBribeFor(address[] calldata lps, address _for) internal {
        uint256 length = lps.length;
        for (uint256 i; i < length; i++) {
            IBribeRewardPool(poolInfos[lps[i]].rewarder).getReward(_for, _for);
        }
    }    
```
