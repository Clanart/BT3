### Title
Staker cannot withdraw principal if blacklisted by a bonus reward token pushed during withdrawal - (File: `rewards/MasterMagpie.sol`)

### Summary
`MasterMagpie.withdraw()` unconditionally harvests and pushes all pending bonus reward tokens to the withdrawing user in the same transaction as the principal-token transfer. If any registered bonus reward token is a blacklist-capable token (e.g. USDC-style) and the staker has been blacklisted for that token, the push-transfer inside the harvest reverts, which reverts the entire withdrawal and permanently blocks the user from ever retrieving their staked principal through the normal path.

### Finding Description
`withdraw()` calls `_withdraw` → `_harvestAndUnstake`, which unconditionally harvests MGP and calls `_harvestBaseRewarder` for `msg.sender` before the staking token is transferred back: [1](#0-0) [2](#0-1) [3](#0-2) 

The bonus-reward harvest ultimately pushes each registered reward token directly to the account via `safeTransfer` in the rewarder: [4](#0-3) 

This is the same anti-pattern as the referenced report: an unprivileged operation (`repay`/here `withdraw`) that returns a user's own asset (collateral/here principal staking token) is bundled atomically with a direct ("push") ERC20 transfer to that same user for an unrelated token (loan token/here bonus reward token). If that token blacklists the recipient, the whole transaction reverts and the unrelated, otherwise-retrievable asset becomes stuck.

Because the staking token transfer and the reward-token transfer happen in one atomic call with no way to skip the reward harvest, a staker who becomes blacklisted for even one bonus reward token in the pool (which can be added by the pool operator over time via `queueNewRewards`/`isRewardToken`) can never call `withdraw()` again to reclaim their principal LP/staking tokens. [5](#0-4) 

The only alternate exit, `emergencyWithdraw`, is gated `whenPaused`, i.e. it is only usable when the contract owner pauses the whole protocol — not something the affected staker can trigger themselves: [6](#0-5) 

Thus an ordinary staker has no permissionless way to recover their own principal once blacklisted for a bonus reward token, mirroring the root cause of the referenced report (forced push-transfer to/from a potentially blacklisted party, bundled with retrieval of the user's own collateral/principal).

### Impact Explanation
A staker's principal staking tokens become permanently locked in `MasterMagpie` with no self-service recovery path, since `withdraw()`/`withdrawFor()` (and equally `multiclaim`/`_multiClaim`, which force-pushes rewards to `_receiver`) always attempt to push bonus rewards to the same address before/along with returning principal, and there is no owner-independent bypass. This is a permanent freezing of user funds for an unprivileged wallet, satisfying the "permanent freezing of funds" impact bar.

### Likelihood Explanation
Requires: (1) a bonus reward token registered for the pool that supports blacklisting (e.g., a USDC-like stablecoin bribe/reward), and (2) the staker becoming blacklisted by that token's issuer for reasons external to the protocol (sanctions, compliance, etc.). This is plausible for any pool with a fiat-backed stablecoin as a bonus reward, and requires no malicious/privileged action within this protocol — only an external, real-world blacklisting event, consistent with the accepted precedent in the referenced report.

### Recommendation
Decouple principal withdrawal from reward-token distribution: allow reward tokens to accrue to an internal claimable balance (pull-based) instead of forcing a push `safeTransfer` during `withdraw`/`_harvestAndUnstake`, or allow withdrawal of principal to proceed even if a reward-token transfer fails (e.g., wrap the reward transfer in a try/catch and credit the amount to a claimable mapping on failure), similar to the mitigation suggested in the referenced report (push-then-pull accounting).

### Proof of Concept
1. Pool operator/rewarder registers a USDC-like reward token via `queueNewRewards`, setting `isRewardToken[token] = true`. [5](#0-4) 
2. User stakes into the pool and accrues a nonzero balance of that reward token.
3. The token issuer blacklists the user's address (external event).
4. User calls `MasterMagpie.withdraw(stakingToken, amount)`; `_harvestAndUnstake` triggers `_harvestBaseRewarder`, which calls `_sendReward`'s `safeTransfer` to the blacklisted user and reverts. [7](#0-6) [4](#0-3) 
5. The entire `withdraw` transaction reverts; the user cannot retrieve their staked principal, and `emergencyWithdraw` is unavailable unless the contract owner pauses the protocol. [6](#0-5)

### Citations

**File:** rewards/MasterMagpie.sol (L344-346)
```text
    function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _withdraw(_stakingToken, msg.sender, _amount, false);
    }
```

**File:** rewards/MasterMagpie.sol (L434-447)
```text
    /// @notice Withdraw all available tokens without caring about rewards. EMERGENCY ONLY. 
    ///         Locked Token can not be emergent withdraw.
    /// @param _stakingToken Staking token of the pool
    /// @dev withdrawFor of the rewarder with the third param at false is an emergency withdraw
    function emergencyWithdraw(address _stakingToken) external whenPaused {
        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][msg.sender];
        uint256 availableaAmount = user.available;
        user.available = 0;
        IERC20(pool.stakingToken).safeTransfer(address(msg.sender), availableaAmount);
        emit EmergencyWithdraw(msg.sender, _stakingToken, availableaAmount);
        user.amount = user.amount - availableaAmount;
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;
    }
```

**File:** rewards/MasterMagpie.sol (L508-514)
```text
    function _withdraw(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        _harvestAndUnstake(_stakingToken, _account, _amount, _isVlMgp);

        if (!_isVlMgp)
            IERC20(tokenToPoolInfo[_stakingToken].stakingToken).safeTransfer(address(msg.sender), _amount);
        emit Withdraw(_account, _stakingToken, _amount);
    }
```

**File:** rewards/MasterMagpie.sol (L516-534)
```text
    function _harvestAndUnstake(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        updatePool(_stakingToken);

        UserInfo storage user = userInfo[_stakingToken][_account];

        if (!_isVlMgp && user.available < _amount)
            revert WithdrawAmountExceedsStaked();
        else if(user.amount < _amount && _isVlMgp)
            revert UnlockAmountExceedsLocked();
        
        _harvestMGP(_stakingToken, _account);
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount - _amount;
        
        if(!_isVlMgp)
            user.available = user.available - _amount;
        user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L273-286)
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

**File:** rewards/BaseRewardPoolV2.sol (L323-327)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver, uint256 _amount) internal {
        userRewards[_rewardToken][_account] = 0;
        IERC20(_rewardToken).safeTransfer(_receiver, _amount);
        emit RewardPaid(_account, _receiver, _amount, _rewardToken);
    }
```
