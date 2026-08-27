### Title
Empty `getRewards` implementation permanently freezes user rewards on selective claims - (File: `rewards/BaseRewardPool.sol`)

### Summary
`BaseRewardPool.getRewards(address _account, address _receiver, address[] memory _rewardTokens)` is declared and marked `override external` but its function body is completely empty, performing no logic at all. [1](#0-0) 
This is the same bug class as the reported "incomplete message handling" issue: a code path that exists and is wired up as reachable/routed, but whose implementation was never filled in, silently dropping the operation instead of performing the expected state transition.

### Finding Description
`MasterMagpie._claimBaseRewarder` is the internal routine invoked by every user-facing claim path (`multiclaim`, `multiclaimSpec`, `multiclaimFor`, and the withdraw/unstake harvest flow) to pay out bonus reward tokens held in each pool's `BaseRewardPool`. When a caller supplies a non-empty `_rewardTokens` array (i.e. asks to selectively claim specific reward tokens, as `multiclaimSpec` explicitly supports), it calls `rewarder.getRewards(_account, _receiver, _rewardTokens)` instead of `getReward`: [2](#0-1) 

Because `BaseRewardPool.getRewards` is an empty stub, this call:
- Does not transfer any reward tokens to `_receiver`.
- Does not zero out `userRewards[rewardToken][_account]`.
- Does not emit `RewardPaid`.

Compare with the working `getReward` (claim-all) implementation, which correctly loops over `rewardTokens`, transfers balances, and zeroes `userRewards`: [3](#0-2) 

Critically, the rest of `_multiClaim` in `MasterMagpie` still marks the claim as processed for MGP-side accounting (`unClaimedMgp` reset, `rewardDebt` updated) regardless of whether the bonus-token side actually paid out: [4](#0-3) 

Since `userRewards[token][account]` in `BaseRewardPool` is only cleared inside `getReward`/`getRewards`/`_updateFor`, and `getRewards` never clears it, one might assume the reward is merely "still pending" and recoverable later via `getReward`. However, `updateFor`/`_updateFor` (called via the `updateReward` modifier and via `_harvestBaseRewarder`) recomputes `userRewards[_account]` using `earned()`, which is based on `rewardPerToken - userRewardPerTokenPaid`. Any subsequent `getReward` call (claim-all) would still pay out the correct amount since `userRewardPerTokenPaid` is unaffected by the failed `getRewards` call — but the funds routed to a **selective per-token claim** are simply never delivered to `_receiver` in that transaction, and any protocol integration (e.g. `ManualCompound.compound`, which relies on `multiclaimOnBehalf` with explicit `_rewards[i]` arrays) that expects tokens to arrive at `msg.sender`/receiver after this call will not receive them: [5](#0-4) 

Because `ManualCompound.compound` immediately re-approves/converts/locks whatever balance it observes after `multiclaimOnBehalf` returns, and `getRewards` performs zero token movement, compounding of selectively-specified reward tokens silently no-ops — the user's on-chain transaction succeeds but the expected reward conversion/lock/transfer never happens, with no revert to alert the caller.

### Impact Explanation
This does not directly move funds out of the protocol to an attacker (no privileged/malicious-admin path required), but it results in reward tokens intended for the user's selective claim never being transferred in that call, while the transaction reports success. For flows that depend on the returned balance in the same transaction (`ManualCompound.compound`), this causes the compounding/convert/lock step to be skipped for that reward set, and the user receives nothing back for tokens they explicitly enumerated — effectively a freezing/loss of the harvested yield for that call path, since `updateFor` recalculation only helps if the user later remembers to re-claim via the all-inclusive `getReward` function instead.

### Likelihood Explanation
High likelihood of being triggered unintentionally: `multiclaimSpec` and `multiclaimOnBehalf` (used by `ManualCompound.compound`) are ordinary, unprivileged user-facing entry points that pass non-empty `_rewardTokens` arrays by design, meaning the broken `getRewards` path is the expected/common path for these functions rather than an edge case.

### Recommendation
Implement `BaseRewardPool.getRewards` (and the analogous stub, if present, in `BaseRewardPoolV2.sol`) to mirror `getReward`'s logic but restricted to the provided `_rewardTokens` array: transfer each specified token's `userRewards[token][_account]` balance to `_receiver`, zero the corresponding `userRewards` entry, and emit `RewardPaid` for each, consistent with the pattern demonstrated by the reported analog fix (filling in every declared/routed handler rather than leaving it a no-op).

### Proof of Concept
1. User stakes into a `BaseRewardPool`-backed pool via `MasterMagpie` and accrues bonus reward tokens (`rewardTokens` array non-empty, `userRewards[token][user] > 0`).
2. User calls `MasterMagpie.multiclaimSpec([stakingToken], [[rewardTokenAddr]])`.
3. This resolves to `_multiClaim` → `_claimBaseRewarder(stakingToken, user, user, [rewardTokenAddr])`.
4. Since `_rewardTokens.length > 0`, `rewarder.getRewards(user, user, [rewardTokenAddr])` is invoked.
5. `BaseRewardPool.getRewards` executes its empty body — no `IERC20.safeTransfer`, no event, no state change to `userRewards`.
6. Transaction succeeds, `unClaimedMgp` is reset for the MGP leg, but the bonus reward token balance was never sent to the user; if invoked through `ManualCompound.compound`, the subsequent convert/lock/transfer step also observes a zero balance for that token and silently skips it. [6](#0-5) [1](#0-0)

### Citations

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

**File:** rewards/BaseRewardPool.sol (L242-244)
```text
    function getRewards(address _account, address _receiver, address[] memory _rewardTokens) override external {

    }
```

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

**File:** rewards/MasterMagpie.sol (L618-629)
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
    }
```

**File:** rewards/ManualCompound.sol (L121-138)
```text
    // @param _minRec the expected min mWom to receive upon convert with smart wom convert
    // @param _lockMgp the flag for if MGP should be locked
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
        uint256 rewardTokensLength = rewards.length;        
        IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
        // send none compoundable reward back to caller
        for(uint256 i; i < _lps.length; i++) {
            uint256 rewardLength = _rewards[i].length;
            if (rewardLength > 0) {
                for (uint j; j < rewardLength; j++) {
                    if (!compoundableRewards[_rewards[i][j]]) {
                        uint256 rewardBalance = IERC20(_rewards[i][j]).balanceOf(address(this));
                        if (rewardBalance > 0)
                            IERC20(_rewards[i][j]).safeTransfer(msg.sender, rewardBalance);
                    }
                }
            }
        }
```
