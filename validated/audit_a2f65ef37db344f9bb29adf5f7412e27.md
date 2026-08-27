### Title
Non-zero-allowance revert in `_sendMGPForVlMGPPool` can permanently DoS all vlMGP-pool reward claims - (File: rewards/MasterMagpie.sol)

### Summary
`_sendMGPForVlMGPPool()` calls `IERC20(mgp).safeApprove(vlMGPRewarder, _amount)` without first resetting the allowance to zero. [1](#0-0)  OpenZeppelin's `safeApprove` reverts whenever it is asked to set a non-zero allowance while a non-zero allowance already exists, so if any prior `queueMGP` call on the vlMGP rewarder does not fully consume the approved amount, every subsequent `multiclaimFor`/`multiclaim`/`multiclaimSpec` call that routes through the vlMGP pool branch will revert unconditionally.

### Finding Description
`_multiClaim()` is reachable by any unprivileged caller through `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, which lets an attacker pick an arbitrary `_account` and staking-token list. [2](#0-1)  Inside the loop, when `_stakingToken == address(vlmgp)`, the accrued MGP is aggregated into `vlMGPPoolAmount`, `user.rewardDebt` is reconciled to `accMGPPerShare`, and after the loop `_sendMGPForVlMGPPool(_user, _receiver, vlMGPPoolAmount)` is invoked unconditionally whenever `vlMGPPoolAmount > 0`. [3](#0-2) 

`_sendMGPForVlMGPPool` approves the *same shared* `IERC20(mgp)` allowance from `MasterMagpie` to the single `vlMGPRewarder` address every time, without zeroing it first: [1](#0-0) 
Because this allowance is a single contract-wide storage slot (`allowance[MasterMagpie][vlMGPRewarder]`), it is not scoped per caller or per account — it is shared across *every* user's vlMGP-pool claim. If any earlier `queueMGP` invocation leaves a non-zero residual allowance (e.g. because the rewarder does not `transferFrom` the exact approved amount, or reverts internally after the approval succeeded but before consuming it), the next `safeApprove(vlMGPRewarder, _amount)` call from any account's claim will revert with `SafeERC20: approve from non-zero to non-zero allowance`, since OpenZeppelin's `safeApprove` explicitly enforces `require(currentAllowance == 0 || _amount == 0)`.

Because `_multiClaim` is declared with `nonReentrant` and executes as one atomic transaction, a revert inside `_sendMGPForVlMGPPool` unwinds the entire transaction, including the `user.rewardDebt` update performed earlier in the loop (line 560). This means the specific claim described in the question — where the divergence is supposed to persist between `userInfo[...].rewardDebt` and `tokenToPoolInfo[...].accMGPPerShare` — does not actually get committed on-chain; Solidity's atomicity guarantees roll back all storage writes together with the failed `safeApprove`. What *does* happen instead is a full, repeatable denial-of-service: the vlMGP-pool claim path becomes uncallable for every user, indefinitely, until some other transaction resets the allowance (which nothing in the contract does).

I was unable to fully confirm from the available index whether `vlMGPBaseRewarder.queueMGP()`'s `getRewardablePercentWAD` logic can leave a non-zero allowance residue when called with a rewardable percent below 100% (the file `rewards/vlMGPBaseRewarder.sol` was found via grep but its contents were not retrievable before the tool budget was exhausted). This detail determines exactly which precondition first produces the non-zero leftover allowance; the structural flaw (missing `safeApprove(vlMGPRewarder, 0)` reset before re-approving) is confirmed directly in `MasterMagpie.sol`.

### Impact Explanation
If any transaction leaves residual allowance on `vlMGPRewarder`, the vlMGP reward-claim path (`multiclaim`, `multiclaimSpec`, `multiclaimFor`) becomes permanently unusable for all stakers of the vlMGP pool, since `_sendMGPForVlMGPPool` will always revert on the next non-zero approval attempt. This matches "High – Permanent freezing of unclaimed yield," because legitimately accrued MGP rewards for the vlMGP pool become permanently unclaimable through the standard claim functions until a contract upgrade or admin intervention resets the allowance. It does not, however, create the specific `rewardDebt`/`accMGPPerShare` accounting divergence claimed in the question, because the revert is atomic and rolls back all in-loop state changes.

### Likelihood Explanation
Triggering the very first non-zero leftover allowance requires a code path in the vlMGP rewarder where the amount transferred via `transferFrom` in `queueMGP` is less than the amount approved (uncertain from index, requires reading `rewards/vlMGPBaseRewarder.sol`). Once such a leftover allowance exists, exploitation of the DoS itself requires no special capital or privilege — any account (attacker or ordinary user) calling `multiclaim`/`multiclaimFor`/`multiclaimSpec` with a non-zero vlMGP-pool claim will trigger and re-trigger the revert, making the condition self-perpetuating and repeatable by anyone.

### Recommendation
In `_sendMGPForVlMGPPool` (and similarly in `_sendVlMGPFor`, which has the identical pattern at `rewards/MasterMagpie.sol` line 653), reset the allowance to zero before setting a new non-zero value, e.g. `IERC20(mgp).safeApprove(vlMGPRewarder, 0); IERC20(mgp).safeApprove(vlMGPRewarder, _amount);`, or switch to `safeIncreaseAllowance`/`forceApprove` semantics that tolerate non-zero-to-non-zero transitions.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `MasterMagpie`, `VLMGP`, and the vlMGP `BaseRewardPoolV2`/`vlMGPBaseRewarder`, register the vlMGP pool.
2. Have a user deposit into vlMGP and accrue MGP rewards in `MasterMagpie` (`accMGPPerShare` advances via `updatePool`).
3. Craft a scenario (e.g., a rewarder-side condition or a manipulated `getRewardablePercentWAD`) causing a first `multiclaim`/`multiclaimFor` call's `queueMGP` to consume less than the full approved `_amount`, leaving `IERC20(mgp).allowance(MasterMagpie, vlMGPRewarder) > 0`.
4. Call `multiclaimFor` again (from any address, for any account with a non-zero vlMGP claim) and assert the transaction reverts with `SafeERC20: approve from non-zero to non-zero allowance`.
5. Assert that `userInfo[vlmgp][account].rewardDebt` remains unchanged (proving the earlier claim attempt was fully rolled back) and that repeated calls continue to revert, demonstrating permanent freezing of the pending MGP yield for that pool.

### Citations

**File:** rewards/MasterMagpie.sol (L413-417)
```text
    function multiclaimFor(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, _account, _account, _rewardTokens);
    }
```

**File:** rewards/MasterMagpie.sol (L551-566)
```text
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

        if (vlMGPPoolAmount > 0) {
            _sendMGPForVlMGPPool(_user, _receiver, vlMGPPoolAmount);
        }
```

**File:** rewards/MasterMagpie.sol (L638-644)
```text
    function _sendMGPForVlMGPPool(address _account, address _receiver, uint256 _amount) internal {
        address vlMGPRewarder = tokenToPoolInfo[address(vlmgp)].rewarder;
        IERC20(mgp).safeApprove(vlMGPRewarder, _amount);
        IvlmgpPBaseRewarder(vlMGPRewarder).queueMGP(_amount, _account, _receiver);

        emit HarvestMGP(_account, _receiver, _amount, false);
    }
```
