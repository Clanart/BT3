### Title
Reward tokens beyond `rewardTokens()[0]` become permanently stuck in `DelegateVoteRewardPool` on every `harvestAll`/`claimAllBribes` cycle - ([File: rewards/DelegateVoteRewardPool.sol])

### Summary
`WombatBribeManager.claimAllBribes` only records the first bribe reward token (`IWombatBribe(bribesContract).rewardTokens()[0]`) per pool, yet it calls `IBribeRewardPool(pool.rewarder).getReward(_for, _for)` which (mirroring `DelegateVoteRewardPool._getDelegateReward`) transfers *every* registered reward token the caller earned. When `_for == address(this)` — i.e. `DelegateVoteRewardPool.harvestAll()` triggers `claimAllBribes(address(this))` — any bribe token past index 0 is physically transferred into the `DelegateVoteRewardPool` contract but is never returned in `rewardTokensList`/`earnedRewards`, so `_manageRewards` never queues it via `_queueNewRewardsWithoutTransfer`. Those tokens sit in the contract with no accounting entry (`rewardTokens` array never grows for them, `rewardPerTokenStored`/`historicalRewards` never updated) and are permanently unclaimable. `DelegateVoteRewardPool.harvestAll()` itself has no access-control modifier, so any unprivileged actor can trigger this loss cycle at will.

### Finding Description
- `DelegateVoteRewardPool.harvestAll()` (no modifier, callable by anyone) calls `IWombatBribeManager(operator).claimAllBribes(address(this))`: [1](#0-0) 
- Inside `WombatBribeManager.claimAllBribes`, for each active pool it truncates the reported reward token to index `[0]` of the underlying bribe contract, but the actual token transfer is done by `IBribeRewardPool(pool.rewarder).getReward(_for, _for)`, which is not limited to token 0: [2](#0-1) 
- The underlying rewarder's `getReward` (same pattern as `DelegateVoteRewardPool._getDelegateReward`) iterates over its full `rewardTokens` array and transfers all tokens the account (`address(this)`, i.e., the `DelegateVoteRewardPool`) has earned: [3](#0-2) 
- Back in `harvestAll`, only the truncated `rewardTokensList`/`earnedRewards` (token index 0 per pool) are passed into `_manageRewards`, which is the only path that calls `_queueNewRewardsWithoutTransfer` to register a token into `rewardTokens` and update `rewardPerTokenStored`: [4](#0-3) 
- Consequently, any bribe token beyond index 0 that was physically transferred into `DelegateVoteRewardPool` is left with `isRewardToken[token] == false` and no `rewardInfo` entry, so `_getDelegateReward`/`getReward(address _for)` can never distribute it — the balance is permanently stranded in the contract.
- No existing modifier prevents this: `claimAllBribes` and `harvestAll` are both unauthenticated public/external functions, and there is no reconciliation or sweep mechanism for tokens received outside `_queueNewRewardsWithoutTransfer`.

Note: the specific invariant proposed in the question ("`_balances[account]` must stay reconciled with `totalSupply`") does not apply here — `_balances`/`totalSupply` in `DelegateVoteRewardPool` are stake/vote accounting variables mutated only by `stakeFor`/`withdrawFor`, not by reward-token flows. `getReward(address _for)` never touches `_balances` or `totalSupply`. The real, verifiable invariant break is in the reward-token bookkeeping (`rewards[token].rewardPerTokenStored`/`historicalRewards`), not the stake ledger.

### Impact Explanation
Every bribe pool that pays out more than one reward token causes those extra tokens to be silently locked inside `DelegateVoteRewardPool` whenever `harvestAll` (or `castVotes` → `harvestAll`, or a direct `claimAllBribes(delegatedPool)` call) executes. These tokens are never queued into the reward index, so no staker can ever claim them via `getReward`/`_getDelegateReward`, and there is no admin/recovery function shown in this contract to sweep them out. This matches Immunefi's "Permanent freezing of unclaimed yield" (High) impact — the loss is proportional to bribe reward diversity and accrues on every harvest cycle indefinitely.

### Likelihood Explanation
`harvestAll()` has no access control, and `claimAllBribes` is a fully public function on `WombatBribeManager`, so this triggers automatically as part of normal, expected keeper operation (`castVotes` → `harvestAll`) with no attacker input needed — it is not something an unprivileged attacker needs to specially construct via `getReward(address _for)` timing games. It occurs deterministically any time an underlying Wombat bribe pool distributes more than one reward token, which is a realistic and common on-chain condition, making the freezing highly likely and repeatable rather than a rare edge case.

### Recommendation
In `WombatBribeManager.claimAllBribes`, retrieve and forward *all* reward tokens/amounts returned by `IBribeRewardPool(pool.rewarder).getReward(_for, _for)` (e.g., have the rewarder's `getReward` return the full token/amount arrays it actually transferred, as `_getDelegateReward` already does) instead of hardcoding `rewardTokens()[0]`. Ensure `DelegateVoteRewardPool._manageRewards` receives and queues every token that was actually transferred into the contract during `harvestAll`, so no bribe token can arrive without being routed into the reward index.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `WombatBribeManager`, a mock `IWombatVoter`/bribe contract that reports 2+ `rewardTokens()` for a pool, a mock `pool.rewarder` (`BribeRewardPool`) that pays out both tokens on `getReward`, and `DelegateVoteRewardPool` as `delegatedPool`.
2. Have a user vote for the pool via `WombatBribeManager.vote`, then simulate the underlying pool rewarder accruing both reward tokens for `address(DelegateVoteRewardPool)` (simulating that `DelegateVoteRewardPool` itself had votes routed through `castVotes`/`_forwardRewards` earning bribes).
3. Call `DelegateVoteRewardPool.harvestAll()` from an arbitrary unprivileged address.
4. Assert: `IERC20(secondRewardToken).balanceOf(address(delegateVoteRewardPool))` increased by the harvested amount, but `delegateVoteRewardPool.isRewardToken(secondRewardToken)` is `false` and `rewards(secondRewardToken).historicalRewards == 0`.
5. Assert that calling `getReward(account)` for any staker never distributes the second token (earned always 0), confirming the tokens are permanently unclaimable.

### Citations

**File:** rewards/DelegateVoteRewardPool.sol (L97-103)
```text
    function harvestAll() external {
        (
            address[] memory rewardTokensList,
            uint256[] memory earnedRewards
        ) = IWombatBribeManager(operator).claimAllBribes(address(this));
        _manageRewards(rewardTokensList, earnedRewards);
    }
```

**File:** rewards/DelegateVoteRewardPool.sol (L107-130)
```text
    function _getDelegateReward(
        address _account
    )
        internal
        returns (
            address[] memory rewardTokensList,
            uint256[] memory earnedRewards
        )
    {
        uint256 length = rewardTokens.length;
        rewardTokensList = new address[](length);
        earnedRewards = new uint256[](length);
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            rewardTokensList[index] = rewardToken;
            uint256 reward = earned(_account, rewardToken);
            earnedRewards[index] = reward;
            if (reward > 0) {
                userRewards[rewardToken][_account] = 0;
                IERC20(rewardToken).safeTransfer(_account, reward);
                emit RewardPaid(_account, _account, reward, rewardToken);
            }
        }
    }
```

**File:** rewards/DelegateVoteRewardPool.sol (L152-203)
```text
    function _manageRewards(
        address[] memory rewardTokensList,
        uint256[] memory earnedRewards
    ) internal {
        uint256 length = rewardTokensList.length;
        for (uint256 index = 0; index < length; ++index) {
            uint256 fees = (protocolFee * earnedRewards[index]) / DENOMINATOR;
            if (fees > 0 && feeCollector != address(0)) {
                earnedRewards[index] = earnedRewards[index] - fees;
                IERC20(rewardTokensList[index]).safeTransfer(
                    feeCollector,
                    fees
                );
            }
            if (earnedRewards[index] > 0) {
                _queueNewRewardsWithoutTransfer(
                    earnedRewards[index],
                    rewardTokensList[index]
                );
            }
        }
    }

    /// @notice Sends new rewards to be distributed to the users staking.
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function _queueNewRewardsWithoutTransfer(
        uint256 _amountReward,
        address _rewardToken
    ) internal {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;
        if (totalSupply == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10 ** this.stakingDecimals()) /
                totalSupply;
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }
```

**File:** wombat/WombatBribeManager.sol (L354-368)
```text
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
