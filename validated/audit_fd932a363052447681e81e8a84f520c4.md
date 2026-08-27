## Title
Hardcoded `rewardTokens()[0]` in `claimAllBribes` causes reward accounting mismatch, permanently freezing yield in `DelegateVoteRewardPool` - (File: wombat/WombatBribeManager.sol)

### Summary
`claimAllBribes(_for)` only tracks and reports the first bribe reward token (`IWombatBribe(bribesContract).rewardTokens()[0]`) per pool, but calls `IBribeRewardPool(pool.rewarder).getReward(_for, _for)`, which (per `BaseRewardPoolV2.getReward`) loops over *all* registered `rewardTokens` in the rewarder and transfers every earned token to `_for`. When a pool's rewarder has been queued with 2+ bribe tokens (which happens naturally via `WombatStaking.vote()`, which iterates `IWombatBribe(bribesContract).rewardTokens()` fully and calls `queueNewRewards` for each token), the extra tokens are physically transferred to `_for` but never appear in the returned `rewardTokens`/`earnedRewards` arrays.

### Finding Description
In `wombat/WombatBribeManager.sol`: [1](#0-0) 

```
for (uint256 i; i < length; i++) {
    Pool storage pool = poolInfos[pools[i]];
    address lp = pool.poolAddress;
    address bribesContract = address(voter.infos(lp).bribe);
    if (bribesContract != address(0)) {
        rewardTokens[i] = address(IWombatBribe(bribesContract).rewardTokens()[0]);
        if (userVotedForPoolInVlmgp[_for][lp] > 0) {
            earnedRewards[i] = IBribeRewardPool(pool.rewarder).earned(_for, rewardTokens[i]);
            if (earnedRewards[i] > 0) {
                IBribeRewardPool(pool.rewarder).getReward(_for, _for);
            }
        }
    }
}
```

`rewardTokens[i]` and `earnedRewards[i]` only reflect token index `0`. But `pool.rewarder.getReward(_for, _for)` internally iterates the rewarder's full `rewardTokens` array (`BaseRewardPoolV2.getReward`, lines 218-235) and transfers *every* earned token to `_for`. The rewarder accumulates multiple tokens because `WombatStaking.vote()` queues rewards for all tokens returned by `IWombatBribe(bribesContract).rewardTokens()` (lines 378-417 of `wombat/WombatStaking.sol`), not just index 0.

When `_for` is `delegatedPool` (the `DelegateVoteRewardPool` contract), the flow is:
`DelegateVoteRewardPool.harvestAll()` → `IWombatBribeManager(operator).claimAllBribes(address(this))` → `_manageRewards(rewardTokensList, earnedRewards)`. [2](#0-1) [3](#0-2) 

`_manageRewards` only distributes the tokens/amounts present in `rewardTokensList`/`earnedRewards` (calling `_queueNewRewardsWithoutTransfer` per entry). Any second/third bribe token physically transferred into `DelegateVoteRewardPool` by `getReward` is not included in this list, so it is never registered as a reward token in the pool (`isRewardToken` stays false, `rewardPerTokenStored` is never updated for it), leaving the tokens stuck in the contract's balance with no accounting path for stakers to claim them (short of a manual admin rescue, which is out of scope for an unprivileged actor and not guaranteed to exist).

Existing checks do not prevent this: `harvestAll()` has no access control and is callable by anyone, `claimAllBribes` has no validation that a bribe has only one reward token, and there is no invariant check comparing transferred balances against the returned arrays.

### Impact Explanation
This is a **theft/permanent freezing of unclaimed yield** issue (Immunefi: "Theft of unclaimed yield" / "Permanent freezing of funds"). Any pool with a bribe contract configured with 2+ reward tokens and votes routed through `delegatedPool` will have its non-index-0 reward tokens transferred into `DelegateVoteRewardPool` but never entered into its reward-per-token accounting, permanently stranding that value for depositors of `DelegateVoteRewardPool` (no stated mechanism recovers or redistributes it). This applies to any caller of `claimAllBribes`/`harvestAll`/`getReward` generally when a rewarder holds multiple tokens, not only the delegated-pool case, but the delegated pool aggregation path in `_manageRewards` is where the funds become structurally untracked and frozen.

### Likelihood Explanation
- Requires only that a Wombat bribe contract for a voted pool has ≥2 reward tokens — a normal, permissionless-to-observe protocol configuration, not an admin misconfiguration of this repo.
- No special privileges needed: any user can call `harvestAll()`/`castVotes()`/`claimAllBribes` since these are public/external with no access restriction.
- Fully repeatable each time bribes accrue for such a pool; happens automatically without any crafted attacker input — it's a data-loss bug rather than something needing an active "attack," but it is unprivileged-triggerable and results in real fund freezing each harvest cycle.

### Recommendation
In `claimAllBribes`, iterate over **all** `IWombatBribe(bribesContract).rewardTokens()` for each pool (mirroring what `WombatStaking.vote()` and `BaseRewardPoolV2.getReward` already do), building `rewardTokens`/`earnedRewards` arrays sized to the total token count across all pools, and query `earned(_for, token)` for each token index before calling `getReward`. Alternatively, replace the manual `earned`+`getReward` pattern with `IBribeRewardPool(pool.rewarder).getRewardsAndReturnAmounts(...)`-style helper that returns the exact tokens/amounts actually transferred, ensuring the returned arrays conserve with the real transfer so `_manageRewards` never drops tokens.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, a Wombat pool with a bribe contract configured with 2 reward tokens (`rewardTokens()` returns `[tokenA, tokenB]`), and a `BribeRewardPool` as `pool.rewarder`.
2. Register `delegatedPool` as a `DelegateVoteRewardPool`, add the pool to `poolInfos`/`pools`, and have `delegatedPool` vote for it via `_updateVote()`.
3. Call `castVotes()` so `WombatStaking.vote()` queues both `tokenA` and `tokenB` into `pool.rewarder` (confirm via `pool.rewarder.rewardTokens()` now has length 2).
4. Call `DelegateVoteRewardPool.harvestAll()` (→ `claimAllBribes(delegatedPool)`).
5. Assert: `earnedRewards` returned/used in `_manageRewards` only accounts for `tokenA`; capture `tokenB.balanceOf(delegatedPool)` before/after and show it increased by the earned `tokenB` amount from `pool.rewarder.earned(delegatedPool, tokenB)`, yet `isRewardToken[tokenB]` in `DelegateVoteRewardPool` remains `false` and no `rewardPerTokenStored` update occurred for `tokenB` — proving the received `tokenB` balance is permanently unaccounted/frozen relative to the conservation invariant (accounted rewards should equal transferred rewards). [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** wombat/WombatBribeManager.sol (L339-375)
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

        uint256 delegatePoolRewardsLength = delegatePoolRewardTokens.length;
        for (uint256 i = length; i < length + delegatePoolRewardsLength; i++) {
            rewardTokens[i] = delegatePoolRewardTokens[i - length];
            earnedRewards[i] = delegatePoolRewardAmounts[i - length];
        }
    }
```

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

**File:** rewards/DelegateVoteRewardPool.sol (L152-173)
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

**File:** wombat/WombatStaking.sol (L378-417)
```text
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
```
