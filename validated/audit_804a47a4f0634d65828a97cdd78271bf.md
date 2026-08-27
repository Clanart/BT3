### Title
`getReward` permanently reverts for all users once any single queued reward token reverts on transfer, freezing unclaimed yield for every other token - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder.getReward(address _account, address _receiver)` iterates the full, append-only `rewardTokens` array and calls `_sendReward` -> `IERC20.safeTransfer` for every token in a single atomic loop with no `try/catch` and no per-token isolation [1](#0-0) . Because `queueNewRewards` only appends to `rewardTokens` and there is no admin or manager function anywhere in the contract to remove a token from that array [2](#0-1) , if any single registered reward token later begins reverting on `transfer` (e.g. it gets paused, its address is blacklisted, or it self-destructs), every subsequent call to `getReward` for every account reverts, permanently blocking settlement/claim of all other, healthy reward tokens as well.

### Finding Description
- `queueNewRewards` (onlyManager) is the only path that adds tokens to `rewardTokens`; there is no corresponding removal function [2](#0-1) .
- `getReward` is `onlyMasterMagpie`-gated but is reachable by any unprivileged user through `MasterMagpie`'s claim/multiclaim path, since `IBaseRewardPool.getReward` is the only claim entrypoint declared in the interface used by `MasterMagpie` [3](#0-2) .
- `getReward`'s `updateReward` modifier calls `_updateFor`, which loops over the entire `rewardTokens` array to snapshot `userRewards`/`userRewardPerTokenPaid` [4](#0-3) , and then `getReward`'s own body again loops over the entire array calling `_sendReward` for each token [5](#0-4) .
- `_sendReward` performs an unguarded `IERC20(_rewardToken).safeTransfer(_receiver, toSend)` [6](#0-5) . If any one `_rewardToken` in the array reverts on `transfer` (blacklisted receiver/pausable token/broken contract), that revert propagates up through the whole `getReward` call (no try/catch anywhere in the loop), reverting the entire transaction — including the state updates for every other, still-healthy reward token that would otherwise have settled successfully.
- Because there is no owner/manager function to prune the offending token from `rewardTokens`, this is a **permanent** condition once triggered: every future call to `getReward` for any account will revert, and `userRewards` for legitimate tokens can never be paid out through this entrypoint again.
- The contract does provide `getRewards(address,address,address[])`, which lets a caller pass an explicit subset of tokens and thus could skip the bad token, but that function is also gated by `onlyMasterMagpie` and there is no evidence that `MasterMagpie` exposes a selective-claim path to end users (only the single-token-array `getReward` is declared in `IBaseRewardPool` and used for claiming) — so in practice ordinary users going through `MasterMagpie.multiclaim` have no way to avoid the reverting token.

Regarding the specific invariant framed in the question — `balanceOf(account)` staying reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken, account).staked` — this cannot actually be broken by this bug: `balanceOf` is a pure pass-through read of `stakingInfo` and is never cached or mutated by `getReward` [7](#0-6) , so it always stays reconciled by construction regardless of `getReward` succeeding or reverting. The real, valid invariant broken is that a single misbehaving reward token can permanently block settlement of all the other reward tokens, causing `userRewards` balances for the healthy tokens to become permanently unclaimable.

### Impact Explanation
Once one queued reward token becomes non-transferable, all users lose the ability to claim any reward token (not just the broken one) through `getReward`, and there is no admin path to remove the bad token and unblock the array. Users who have accrued large `userRewards` balances across multiple epochs have that yield permanently frozen. This matches "High - Permanent freezing of unclaimed yield," since the funds are the reward tokens sitting in the contract that become perpetually unclaimable via the intended entrypoint.

### Likelihood Explanation
This does not require an attacker to directly force a revert (the attacker cannot call `queueNewRewards`, which is `onlyManager`), so the trigger is an external event (a reward token being paused/blacklisting the rewarder or receiver, or a broken/self-destructed token contract) rather than a directly attacker-controlled action. Given this, likelihood depends on the reward manager queuing a token that can later misbehave (e.g., a token with pausability/blacklist controls, common among many ERC-20s used as incentive tokens). No capital is required to trigger it once such a token exists in `rewardTokens`; once triggered, the freeze is permanent and affects every staker.

### Recommendation
Wrap each `_sendReward` (and ideally the reward-accrual bookkeeping) in a `try/catch`, or use a low-level `call` with a bounded gas stipend and treat failures as "skip and keep queued" rather than reverting the whole loop, so a single failing token cannot block distribution of the rest. Additionally, add a manager/owner function to remove/deactivate a reward token from `rewardTokens` (and `isRewardToken`) so a permanently broken token can be pruned, and expose the existing `getRewards(address,address,address[])` selective-claim function through `MasterMagpie` so users have a first-class fallback path that does not depend on iterating every token.

### Proof of Concept
Foundry test plan:
1. Deploy `mWOMSVBaseRewarder`, initialize with a legitimate reward token A, and stake via `MasterMagpie` for an account.
2. As the manager, call `queueNewRewards` to add a second, maliciously pausable/blacklistable token B, and `queueNewRewards` to fund both A and B over several simulated epochs so the victim accrues sizable `userRewards[A][victim]` and `userRewards[B][victim]`.
3. Cause token B's `transfer` to always revert (e.g., pause it or blacklist the rewarder/receiver — simulate with a mock ERC20 whose `transfer` reverts).
4. Call `getReward(victim, victim)` through `MasterMagpie` and assert the entire call reverts.
5. Assert that even though token A is perfectly healthy, `userRewards[A][victim]` remains stuck and unclaimable through `getReward` in this and all subsequent calls (repeat the call to show it always reverts).
6. Confirm there is no owner/manager function callable to remove token B from `rewardTokens`, demonstrating the freeze is permanent.
7. Note: assert that `balanceOf(victim)` still equals `IMasterMagpie(masterMagpie).stakingInfo(stakingToken, victim).staked` throughout (this remains trivially true since `balanceOf` is a direct pass-through and unaffected by the bug) — the actual damage to assert is the permanent inability to withdraw `userRewards[A][victim]`.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L146-149)
```text
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L233-247)
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
            _sendReward(rewardToken, _account, _receiver);
        }

        return true;
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L278-291)
```text
    function queueNewRewards(uint256 _amountReward, address _rewardToken)
        override
        external
        onlyManager
        returns (bool)
    {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }

        _provisionReward(_amountReward, _rewardToken);
        return true;
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L348-360)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        uint256 userMWOMSVAmount = balanceOf(_account);

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;

            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userMWOMSVAmount);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L362-376)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver) internal {
        uint256 forfeitAmount = _calExpireForfeit(_account, userRewards[_rewardToken][_account]);
        uint256 toSend = userRewards[_rewardToken][_account] - forfeitAmount;


        userRewards[_rewardToken][_account] = 0;
            
        if (toSend > 0) {
            IERC20(_rewardToken).safeTransfer(_receiver, toSend);
            emit RewardPaid(_account, _receiver, toSend, _rewardToken);
        }

        if(forfeitAmount > 0)
            _queueNewRewardsWithoutTransfer(forfeitAmount, _rewardToken);
    }
```

**File:** interfaces/IBaseRewardPool.sol (L40-40)
```text
    function getReward(address _account, address _receiver) external returns (bool);
```
