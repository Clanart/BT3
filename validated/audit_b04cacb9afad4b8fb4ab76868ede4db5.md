### Title
Incorrect/permanent reduction of `claimedReward` on failed USDT transfer causes permanent loss of unclaimed yield in `ArbWomUp.incentiveDeposit` - (File: wombat/ArbWomUp.sol)

### Summary
`ArbWomUp.incentiveDeposit()` increments the user's `claimedReward` mapping *before* sending the USDT reward, and it sends the reward using the plain, non-reverting, return-value-unchecked `IERC20(usdt).transfer(...)` call instead of `safeTransfer`. If the transfer returns `false` (a normal ERC20 failure mode, not a revert), the user's `claimedReward` is permanently increased even though they received no tokens, permanently reducing/erasing their future claimable incentive.

### Finding Description
In `incentiveDeposit`, the reward bookkeeping is updated unconditionally, and the actual token transfer's success is never checked: [1](#0-0) 

```
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

`claimedReward[msg.sender] += rewardToSend` runs unconditionally, and `IERC20(usdt).transfer(...)` is called raw — its boolean return value is discarded and not checked with `require`, and the contract does not use `SafeERC20.safeTransfer` here (even though `SafeERC20` is imported and used elsewhere, e.g. in `_deposit`). Standard-compliant ERC20 tokens that return `false` on failure (rather than reverting) — for example, if the contract's USDT balance is insufficient to cover `rewardToSend`, or the token has any transfer restriction — will silently fail while `claimedReward` is already incremented.

Because `getRewardAmount` computes future entitlement as `(rewardAmount / DENOMINATOR) - claimedReward[_account]` (see `wombat/ArbWomUp.sol` lines 80-98), any silent `transfer` failure permanently and irreversibly reduces (or can zero out) the user's future claimable USDT reward, exactly mirroring the reported bug class: state (`s_clientOnlyClRewards` analog = `claimedReward`) is updated regardless of whether the underlying value-transfer call actually succeeded. [2](#0-1) 

This is directly reachable by any ordinary user calling `incentiveDeposit` — no privileged role is required.

### Impact Explanation
Users who deposit WOM to `ArbWomUp` and are entitled to a USDT incentive can permanently lose that unclaimed yield if the contract's USDT balance runs low (a realistic condition, since `getRewardAmount` itself caps the reward at `usdtleft = IERC20(usdt).balanceOf(address(this))`, so under-funding is an expected operating state) or the token transfer otherwise returns `false`. Since `claimedReward` is not reverted back down, the loss is permanent and cannot be reclaimed by the user through any function in the contract.

### Likelihood Explanation
The condition is plausible during normal operation: `getRewardAmount` explicitly caps the reward to the contract's current USDT balance, meaning the contract can be (and by design sometimes is) exactly balance-constrained. In that near-zero-balance situation, `transfer` returning `false` for a "not enough balance" case is a standard ERC20 failure mode (many implementations return `false` rather than reverting), which will hit this unchecked path.

### Recommendation
Use `SafeERC20.safeTransfer` (already imported via `using SafeERC20 for IERC20`) instead of the raw `IERC20(usdt).transfer(...)` call so a failed transfer reverts the whole transaction, and/or only update `claimedReward[msg.sender]` after confirming the transfer succeeded, mirroring the recommendation in the referenced report: "It's recommended not to update [reward accounting] if all transfer calls were reverted/failed."

### Proof of Concept
1. Owner funds `ArbWomUp` with a small amount of USDT, e.g. exactly enough for one user's reward.
2. User A calls `incentiveDeposit` and successfully drains most/all of the USDT balance, receiving their reward and incrementing `claimedReward[A]`.
3. User B (already having deposited WOM previously, entitled to a reward) calls `incentiveDeposit` again. `getRewardAmount` computes `usdtReward` based on `rewardAmount` tiers but caps it to `usdtleft` (near zero); suppose due to rounding/edge behavior `rewardToSend` computed is still nonzero but `usdt.transfer` returns `false` because the balance is insufficient at execution.
4. `claimedReward[B]` is incremented by `rewardToSend` even though `transfer` returned `false` and User B received 0 USDT.
5. User B's future `getRewardAmount` calls now subtract this phantom `claimedReward[B]`, permanently reducing/eliminating any future incentive they are owed, with no path in the contract to correct or reclaim it.

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
