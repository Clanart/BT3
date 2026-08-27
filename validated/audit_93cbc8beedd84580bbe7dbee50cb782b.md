### Title
Unchecked USDT/BUSD `transfer()` return value in `ArbWomUp`/`ArbWomUp2::incentiveDeposit()` permanently understates future rewards - ([File: wombat/ArbWomUp.sol], [File: wombat/ArbWomUp2.sol])

### Summary
`ArbWomUp::incentiveDeposit()` and `ArbWomUp2::incentiveDeposit()` credit a user's `claimedReward` mapping and then attempt to pay out the reward token (`usdt`/`busd`) using the raw `IERC20.transfer()` call without checking its boolean return value or using `safeTransfer`. If the transfer silently fails (returns `false` instead of reverting, which many ERC20 tokens do), the user's WOM deposit and `claimedReward` accounting are still permanently updated even though the reward tokens were never delivered.

### Finding Description
In `wombat/ArbWomUp.sol`, `incentiveDeposit()`: [1](#0-0) 
computes `rewardToSend`, calls `_deposit(_amount)` to pull the user's WOM, permanently increments `claimedReward[msg.sender] += rewardToSend`, and only afterwards calls `IERC20(usdt).transfer(msg.sender, rewardToSend)` with no success check.

The same unsafe pattern exists in `wombat/ArbWomUp2.sol`: [2](#0-1) 
where `claimedReward[msg.sender] += rewardToSend` is updated before the unchecked `IERC20(busd).transfer(msg.sender, rewardToSend)` call (in the non-bull-mode branch).

Crucially, `claimedReward[_account]` is subtracted from all future reward calculations: [3](#0-2) 
so once `claimedReward` is incremented, that portion of the reward can never be computed/paid again by `getRewardAmount()`, regardless of whether the actual token transfer succeeded.

This is directly reachable by any ordinary wallet by calling `incentiveDeposit()` — no privileged role is required — matching the report's bug class of not verifying token transfer results before continuing state-changing logic.

### Impact Explanation
If the configured reward token (`usdt` or `busd`) ever returns `false` on a failed transfer (a common pattern for non-reverting ERC20 implementations, e.g. due to blacklisting, pausing, or other transfer restrictions on the recipient) instead of reverting, the user's WOM has already been deposited and `claimedReward` has already been permanently incremented by the un-paid amount. The user can never re-claim that reward portion through this contract, since `getRewardAmount()` always nets out the already-recorded `claimedReward`. This results in a permanent loss/freeze of that user's incentive reward.

### Likelihood Explanation
Likelihood is low-to-moderate: it depends on the specific reward token's transfer semantics or transient conditions causing a non-reverting failure (e.g., a blacklisted/paused recipient, or a token implementation that returns `false` rather than reverting on failure). This mirrors the External Report's own low-likelihood/high-impact classification for the analogous `transferUsdc()` issue.

### Recommendation
Use `SafeERC20.safeTransfer` (already imported via `using SafeERC20 for IERC20;` in both contracts) instead of the raw `.transfer()` call, and/or explicitly check the boolean return value and revert the whole `incentiveDeposit` flow (including the `claimedReward` update) if the transfer fails, so reward accounting and actual token delivery remain consistent.

### Proof of Concept
1. Admin configures `usdt` (in `ArbWomUp`) as a token whose `transfer()` implementation returns `false` on failure instead of reverting (or the recipient becomes restricted/blacklisted by the token at the time of the call).
2. A user calls `incentiveDeposit(amount)`. `_deposit()` pulls the user's WOM via `safeTransferFrom` (succeeds), `claimedReward[msg.sender] += rewardToSend` is executed unconditionally.
3. `IERC20(usdt).transfer(msg.sender, rewardToSend)` returns `false` (transfer fails), but execution continues and the function returns successfully, emitting `USDTRewarded`.
4. The user never receives `rewardToSend` USDT, yet `claimedReward[msg.sender]` is permanently increased, so any subsequent call to `getRewardAmount()`/`incentiveDeposit()` will never include that already-"claimed" (but never received) amount — permanently freezing that portion of the user's reward.

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
