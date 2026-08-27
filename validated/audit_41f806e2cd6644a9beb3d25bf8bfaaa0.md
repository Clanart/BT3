### Title
Unchecked/unsafe ERC20 `.transfer()` in `incentiveDeposit()` causes silent loss of reward accounting - ([File: wombat/ArbWomUp.sol / wombat/ArbWomUp2.sol])

### Summary
`ArbWomUp.incentiveDeposit()` and `ArbWomUp2.incentiveDeposit()` are unprivileged, user-callable entry points that pay out a USDT/BUSD reward using a raw `IERC20(...).transfer(...)` call instead of OpenZeppelin's `safeTransfer`, even though both contracts already import and use `SafeERC20` for every other token movement in the same function (`_deposit` uses `safeTransferFrom`).

### Finding Description
In `incentiveDeposit`, reward accounting is updated unconditionally before/without checking the result of the token transfer: [1](#0-0) 

```
function incentiveDeposit(uint256 _amount) external _checkAmount(_amount) whenNotPaused nonReentrant {
    uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender);
    _deposit(_amount);
    claimedReward[msg.sender] += rewardToSend;

    IERC20(usdt).transfer(msg.sender, rewardToSend);
    emit USDTRewarded(msg.sender, rewardToSend);
}
``` [2](#0-1) 

The same pattern exists in `ArbWomUp2.incentiveDeposit()` for the `busd` reward, and `ArbWomUp3.adminWithdrawReward()` also uses raw `.transfer()`, though that one is admin-only.

Because `claimedReward[msg.sender]` is incremented *before* the transfer's success is checked, and the return value of `.transfer()` is discarded entirely, a token that returns `false` on failure instead of reverting (rather than a token that omits the return value altogether, which would revert on ABI decoding) will silently fail to deliver the reward while the contract still records it as claimed. Subsequent calls to `getRewardAmount()` subtract the already-inflated `claimedReward[_account]` from the theoretically owed amount: [3](#0-2) 

This means the user permanently loses the ability to claim that portion of the reward tier bonus, since the accounting has already marked it "claimed" even though tokens never left the contract to the user.

This directly matches the bug class in the referenced report (TRST-M-5): using unsafe/unchecked ERC20 `transfer()` for reward/recovery payouts instead of `SafeERC20`, which fails silently or unpredictably for non-standard tokens.

### Impact Explanation
If the `usdt`/`busd` reward token ever returns `false` on transfer failure (e.g., temporarily insufficient contract balance due to a race between concurrent `incentiveDeposit` calls, since `getRewardAmount` caps the payout to the *current* balance read at the start of the call but the balance can change before the actual transfer executes within the same transaction sequence across users), the caller's `claimedReward` is permanently incremented without the tokens ever being received. This is a permanent loss/freezing of unclaimed yield owed to an ordinary depositing wallet, since there is no recovery path for the user to reclaim the difference — `getRewardAmount` will always subtract the already-recorded `claimedReward`.

### Likelihood Explanation
The `incentiveDeposit` function is fully permissionless and reachable by any wallet holding WOM. The precondition for triggering the loss (transfer returning `false` without reverting) depends on the specific `usdt`/`busd` token deployment and timing of contention for the reward pool's balance, so likelihood is moderate rather than certain, but the code path itself contains no safeguard (no `safeTransfer`, no return-value check, no reversion of the `claimedReward` accounting on failure).

### Recommendation
Replace the raw `IERC20(usdt).transfer(...)` / `IERC20(busd).transfer(...)` calls in `incentiveDeposit()` (both `ArbWomUp.sol` and `ArbWomUp2.sol`) with `safeTransfer` from the already-imported `SafeERC20` library, consistent with the rest of the contract's token handling (`_deposit` already uses `safeTransferFrom`). Additionally, update `claimedReward[msg.sender]` only after confirming the transfer succeeded, or perform the transfer before updating accounting state.

### Proof of Concept
1. Owner configures `rewardMultiplier`/`rewardTier` and funds the contract with a limited `usdt` balance.
2. Two users, A and B, submit `incentiveDeposit` transactions in the same block such that the combined `rewardToSend` for both exceeds the contract's actual `usdt` balance at execution time (the `usdtleft` check in `getRewardAmount` is evaluated per-call and can be stale relative to a second pending transaction).
3. User B's transaction executes after A's balance-consuming transfer; the `usdt` token (if it returns `false` on insufficient allowance/balance rather than reverting) causes B's `.transfer()` call to silently fail.
4. `claimedReward[B]` has already been incremented by `rewardToSend` before the failed transfer, so B never receives the USDT reward but can never re-claim it in future calls, since `getRewardAmount` subtracts the already-recorded `claimedReward[B]`. [4](#0-3)

### Citations

**File:** wombat/ArbWomUp.sol (L69-98)
```text
    function incentiveDeposit(
        uint256 _amount
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender);
        _deposit(_amount);
        claimedReward[msg.sender] += rewardToSend;
        
        IERC20(usdt).transfer(msg.sender, rewardToSend);
        emit USDTRewarded(msg.sender, rewardToSend);
    }

    function getRewardAmount(uint256 _amount, address _account) external view returns (uint256) {
        if (_amount == 0 || rewardMultiplier.length == 0) return 0;
        uint256 accumulated = _amount + userWOMDeposited[_account];

        uint256 rewardAmount = 0;
        uint256 i = 1;
        while (i < rewardTier.length && accumulated > rewardTier[i]) {
            rewardAmount +=
                (rewardTier[i] - rewardTier[i - 1]) *
                rewardMultiplier[i - 1];
            i++;
        }
        rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1];

        uint256 usdtReward = (rewardAmount / DENOMINATOR) - claimedReward[_account];
        uint256 usdtleft = IERC20(usdt).balanceOf(address(this));

        return usdtReward > usdtleft ? usdtleft : usdtReward;
    }
```

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
