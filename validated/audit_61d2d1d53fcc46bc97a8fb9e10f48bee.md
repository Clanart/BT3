WomUp.sol uses `safeTransfer`/`safeTransferFrom` throughout, so it's not vulnerable. The clearest analog is in `ArbWomUp2.sol`, in the user-callable `incentiveDeposit` function.

### Title
Unsafe reward transfer in `incentiveDeposit` can permanently freeze user's BUSD incentive - (File: wombat/ArbWomUp2.sol)

### Summary
`ArbWomUp2.incentiveDeposit` pays out the user's BUSD incentive reward using a bare `IERC20(busd).transfer(...)` call whose boolean return value is discarded, instead of OpenZeppelin's `safeTransfer`. Any user can call this unprivileged, permissionless function.

### Finding Description
`incentiveDeposit` is a public, unprivileged entry point that any wallet can call to deposit WOM and receive a BUSD reward proportional to their deposit tier. The reward payout path calls the token directly: [1](#0-0) 

The contract otherwise consistently uses `SafeERC20`'s `safeTransfer`/`safeTransferFrom`/`safeApprove` (e.g. in `_deposit`), showing the intended pattern: [2](#0-1) 

but the reward disbursement at line 94 (`IERC20(busd).transfer(msg.sender, rewardToSend);`) does not use `safeTransfer` and does not check the boolean return value. If the configured `busd` token (settable by `setup` in `ArbWomUp2.sol`, or more generally any ERC20 that follows the legacy pattern of returning `false` instead of reverting on failure, e.g. due to insufficient allowance-independent transfer restrictions, blacklist, or paused state) fails to move tokens, the call still returns successfully. Execution then proceeds to increment the user's `claimedReward[msg.sender]` bookkeeping as if the transfer succeeded: [3](#0-2) 

Because `claimedReward` is accounted for before/without verifying the transfer outcome, and `getRewardAmount` deducts already-`claimedReward` amounts from future reward calculations, a silently-failed transfer permanently reduces the user's future claimable reward by the amount that was never actually received — the user's WOM deposit is consumed (via `safeTransferFrom` in `_deposit`) but the corresponding BUSD incentive is irrecoverably lost.

### Impact Explanation
This results in permanent loss of a user's earned incentive/yield with no recovery path: the WOM deposit is taken via `safeTransferFrom` (always succeeds/reverts correctly), but the BUSD reward can silently fail while the contract's internal accounting (`claimedReward`) marks it as paid, permanently freezing/forfeiting that portion of the user's unclaimed yield.

### Likelihood Explanation
Any ordinary user can trigger this simply by calling `incentiveDeposit` when the configured reward token exhibits non-reverting failure behavior (e.g., blacklist-style tokens or tokens with restricted transfer conditions) or has insufficient balance in a way that returns `false` rather than reverting. No privileged role or governance action is required to trigger the loss once the token deviates from strict ERC20 semantics.

### Recommendation
Replace `IERC20(busd).transfer(msg.sender, rewardToSend);` with `IERC20(busd).safeTransfer(msg.sender, rewardToSend);` (the contract already imports and uses `SafeERC20` elsewhere), ensuring the transfer reverts the whole transaction — including the `claimedReward` update — on failure.

### Proof of Concept
1. Deploy `ArbWomUp2` with `busd` set to a token that returns `false` on failed transfer instead of reverting (or simulate by making the contract's BUSD balance insufficient at call time due to a reentrant/parallel drain, or a token with a transfer restriction).
2. User calls `incentiveDeposit(amount, minMGPRec, false)`.
3. `_deposit` succeeds (WOM pulled via `safeTransferFrom`), `claimedReward[msg.sender]` is incremented by `rewardToSend`.
4. `IERC20(busd).transfer(msg.sender, rewardToSend)` returns `false` silently; no BUSD is received by the user.
5. User's `claimedReward` now reflects a reward they never received, permanently reducing all future `getRewardAmount` calculations by that amount — the lost yield is unrecoverable. [1](#0-0)

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

**File:** wombat/ArbWomUp.sol (L119-125)
```text
    function _deposit(uint256 _amount) internal whenNotPaused {
        IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        userWOMDeposited[msg.sender] += _amount;
        totalAccumulated += _amount;

        emit WomDeposited(msg.sender, _amount);
    }
```
