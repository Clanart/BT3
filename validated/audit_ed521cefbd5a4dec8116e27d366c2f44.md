### Title
Unchecked `IERC20.transfer()` return value combined with pre-incrementing `claimedReward` can permanently burn a user's USDT reward - (File: wombat/ArbWomUp.sol)

### Summary
`ArbWomUp.incentiveDeposit()` credits the caller's `claimedReward` mapping *before* paying out the USDT reward via a bare `IERC20(usdt).transfer()` call whose boolean return value is never checked. Any ERC20 that returns `false` on failure instead of reverting (a common, EIP-20–compliant behavior) will cause the reward to be silently not delivered while the contract's accounting still treats it as paid, permanently locking that portion of the user's yield.

### Finding Description
`incentiveDeposit()` is a fully public, unprivileged entry point that any wallet can call after depositing WOM: [1](#0-0) 

The sequence is:
1. `rewardToSend` is computed via `getRewardAmount`.
2. `_deposit(_amount)` pulls WOM from the user with `safeTransferFrom` (correctly checked).
3. `claimedReward[msg.sender] += rewardToSend` is recorded **first**.
4. `IERC20(usdt).transfer(msg.sender, rewardToSend)` is called with the return value discarded.

Because `claimedReward` is an accounting variable subtracted from future reward computations in `getRewardAmount`: [2](#0-1) 

if the underlying `usdt` token implementation returns `false` on a failed transfer rather than reverting (this is valid EIP-20 behavior, and is exactly the class of bug the referenced Sherlock finding on Sentiment's `Account.sol` flags — `try/catch` around a raw `transfer()` masking failures), the user's `claimedReward` is already permanently incremented even though no tokens were actually received. All subsequent calls to `getRewardAmount`/`incentiveDeposit` compute `usdtReward = (rewardAmount / DENOMINATOR) - claimedReward[_account]`, so the "already claimed" amount is subtracted forever, and the user has no path to reclaim the lost USDT reward.

### Impact Explanation
This results in permanent freezing/loss of a user's earned (but undelivered) USDT reward with no recovery mechanism — the accounting state (`claimedReward`) is updated unconditionally regardless of whether the external `transfer()` call actually succeeded. This matches the "theft or permanent freezing of unclaimed yield" impact category, triggered purely by an ordinary user calling a public function with no privileged role involved.

### Likelihood Explanation
Likelihood depends on the `usdt` token's transfer semantics (whether it reverts vs. returns `false` on failure, e.g., due to insufficient contract balance for large reward tiers) and on `usdtleft` sizing already being bounded by `getRewardAmount`, which somewhat protects against balance shortfalls but does not fully eliminate non-reverting failure modes for non-standard tokens. It is a straightforward, single-transaction interaction requiring no special conditions beyond a token/tier configuration that produces a failed-but-non-reverting transfer.

### Recommendation
Replace the raw `IERC20(usdt).transfer(...)` call in `incentiveDeposit()` with OpenZeppelin's `SafeERC20.safeTransfer()` (already imported and used elsewhere in the same file for `safeTransferFrom`), and/or move the `claimedReward` update to occur only after a confirmed-successful transfer.

### Proof of Concept
1. Configure/assume `usdt` as a token whose `transfer()` returns `false` on failure instead of reverting (permitted by EIP-20).
2. User calls `incentiveDeposit(amount)`; `claimedReward[msg.sender]` is incremented by `rewardToSend` at [3](#0-2) .
3. `IERC20(usdt).transfer(msg.sender, rewardToSend)` returns `false` (transfer fails) but the call does not revert; the function completes successfully and emits `USDTRewarded`.
4. User's wallet never receives the USDT, but `claimedReward[msg.sender]` is permanently increased, so future `getRewardAmount` calls subtract this already-"claimed" amount, permanently denying the user that portion of their reward.

### Citations

**File:** wombat/ArbWomUp.sol (L69-78)
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
```

**File:** wombat/ArbWomUp.sol (L80-98)
```text
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
