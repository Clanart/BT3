### Title
Unchecked/raw ERC20 `transfer` for user-facing airdrop rewards can silently fail and permanently lock user rewards - ([File: wombat/ArbWomUp2.sol])

### Summary
`ArbWomUp2.incentiveDeposit` (the arbitrum airdrop incentive flow) pays out `busd` rewards to depositing users with a raw `IERC20(busd).transfer(msg.sender, rewardToSend)` call whose return value is never checked, while the accounting (`claimedReward[msg.sender] += rewardToSend`) is updated unconditionally before/independent of whether the transfer actually succeeded.

### Finding Description
In the non-bull branch of `incentiveDeposit`, the contract deposits the user's WOM, increments `claimedReward[msg.sender]` by the computed reward, and then attempts to pay the reward using a bare `.transfer()` call with no return-value check and no `SafeERC20` usage: [1](#0-0) 

Unlike `BaseRewardPool`, `mWOMSVBaseRewarder`, `vlMGPBaseRewarder`, `DelegateVoteRewardPool`, and `mWOM.sol`'s deposit/convert paths, which correctly use `SafeERC20.safeTransfer`/`safeTransferFrom` (e.g. [2](#0-1) , [3](#0-2) ), this airdrop distributor's reward-payout line neither checks a boolean return value nor uses `safeTransfer`. Since the state (`claimedReward`) is already updated to reflect the reward as "paid" regardless of transfer outcome, any failure mode of the reward token's `transfer` (returning `false` without reverting, being paused, exceeding an allowance/blacklist restriction, or any other non-reverting failure) results in the user's owed reward being permanently unrecoverable: the contract has no other function that re-pays a previously "claimed" reward, and `claimedReward` bookkeeping already marks it as distributed.

### Impact Explanation
This directly matches the "unclaimed yield/airdrop distribution" impact category: an ordinary user calling the standard airdrop-incentive entrypoint can have their earned BUSD reward permanently and silently lost with no revert, no error, and no path to reclaim it, because the contract's internal accounting treats the reward as already paid the moment the (potentially failing) `transfer` call returns.

### Likelihood Explanation
`incentiveDeposit` is a plain external, non-privileged function invoked by any wallet participating in the airdrop/incentive program — no special role or governance action is required to trigger the vulnerable code path, only a normal deposit call.

### Recommendation
Replace the raw transfer with OpenZeppelin's `SafeERC20.safeTransfer`, which already is imported and used elsewhere in the same contract (e.g. `safeTransferFrom` on `wom`, `safeApprove` on `busd`):
```solidity
IERC20(busd).safeTransfer(msg.sender, rewardToSend);
```
This ensures any failure in transferring the reward token causes the whole transaction to revert, keeping `claimedReward` accounting consistent with actual token transfers and preventing silent, permanent loss of user rewards. The same pattern should also be checked in the analogous admin-only `transferToAdmin`/`adminWithdrawReward` functions in `ArbWomUp2.sol`/`ArbWomUp3.sol`, though those are owner-only and out of scope for this unprivileged-wallet analog.

### Proof of Concept
1. Owner configures `busd` to point at a token whose `transfer` can return `false` without reverting under certain conditions (e.g. paused state, blacklist, insufficient balance edge case depending on token implementation), consistent with the bug class cited in the external report (USDT/BNB/OMG-style tokens).
2. A user calls `incentiveDeposit(amount, minMGPRec, false)`.
3. `_deposit(amount)` succeeds (pulls WOM in), `claimedReward[msg.sender] += rewardToSend` executes.
4. `IERC20(busd).transfer(msg.sender, rewardToSend)` returns `false` (no revert) because of a transient token-side condition.
5. The function completes successfully; `BUSDRewarded` event is emitted despite no tokens having moved.
6. The user's `claimedReward` is already incremented, so the reward accounting reflects a paid reward that was never received, and there is no other function to reclaim it — the reward is permanently lost to the user.

### Citations

**File:** wombat/ArbWomUp2.sol (L82-97)
```text
    function incentiveDeposit(
        uint256 _amount, uint256 _minMGPRec, bool _bullMode
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        if (_amount == 0) return;

        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender);
        _deposit(_amount);
        claimedReward[msg.sender] += rewardToSend;
        
        if (_bullMode) {
            _bullMGP(rewardToSend, _minMGPRec, msg.sender);
        } else {
            IERC20(busd).transfer(msg.sender, rewardToSend);
            emit BUSDRewarded(msg.sender, rewardToSend);
        }
    }
```

**File:** rewards/BaseRewardPool.sol (L232-236)
```text
            if (reward > 0) {
                userRewards[rewardToken][_account] = 0;
                IERC20(rewardToken).safeTransfer(_receiver, reward);
                emit RewardPaid(_account, _receiver, reward, rewardToken);
            }
```

**File:** wombat/mWOM.sol (L107-119)
```text
            IERC20(wom).safeTransferFrom(msg.sender, wombatStaking, _amount);
            _lockWom(_amount, false);

        } else {
            IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        }

        if(_forStake) {
            if (helper == address(0))
                revert HelperNotSet();
            _mint(address(this), _amount);
            IERC20(address(this)).safeApprove(helper, _amount);
            ISimpleHelper(helper).depositFor(_amount, address(msg.sender));
```
