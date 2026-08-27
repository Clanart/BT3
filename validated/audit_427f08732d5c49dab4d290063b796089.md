Based on my investigation, I found a valid analog in the ArbWomUp airdrop/incentive distribution contracts.

### Title
Non-standard reward token (USDT) transferred via unsafe `IERC20.transfer()` can permanently brick the incentive distribution and strand reward funds - ([File: wombat/ArbWomUp.sol])

### Summary
`ArbWomUp.sol`'s user-facing `incentiveDeposit()` function pays out USDT rewards using the raw OpenZeppelin `IERC20.transfer()` call instead of `SafeERC20.safeTransfer()`. Real-world USDT (Tether) is well known for not returning a `bool` from `transfer()`/`transferFrom()`. Since Solidity's ABI decoder for a function declared to return `bool` will revert if no return data is present, every call to `incentiveDeposit()` will unconditionally revert once the reward-payout line is reached, permanently bricking the airdrop incentive function and leaving the USDT reward pool balance permanently stuck in the contract with no recovery path for that token.

### Finding Description
In `wombat/ArbWomUp.sol`, `incentiveDeposit()` performs a compliant `safeTransferFrom` for the WOM deposit but then rewards the caller with a plain call: [1](#0-0) 

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

The contract already imports and elsewhere uses `SafeERC20`/`safeTransferFrom` for the `wom` token [2](#0-1) , but the `usdt` payout at line 76 bypasses `SafeERC20` and calls the interface method directly. Because `IERC20.transfer` is declared to return `bool`, Solidity generates ABI-decoding code for the return value regardless of whether the caller uses it. If `usdt` is (or is later configured via upgrade/redeploy to be) the real mainnet/BSC USDT contract — whose `transfer()` implementation returns no data at all — the ABI decode will fail and the entire transaction reverts, every single time, for every caller.

The same unsafe pattern (plain `.transfer()` for a payout/reward token) is repeated in the sibling contracts `ArbWomUp2.sol` (BUSD reward) and `ArbWomUp3.sol` (ARB/MGP `adminWithdrawReward`), confirming this is a systemic pattern rather than an isolated typo: [3](#0-2) [4](#0-3) 

Critically, `ArbWomUp.sol` has no admin recovery function for the `usdt` balance — only `transferToAdmin()` exists, and it only sweeps the `wom` token: [5](#0-4) 

So if the reward payout reverts unconditionally, there is no way to ever recover or distribute the USDT sitting in the contract.

### Impact Explanation
If `usdt` is configured to a non-standard ERC20 (such as the canonical Tether USDT contract, which is the obvious candidate given the variable name), every call to `incentiveDeposit()` reverts permanently. This is a complete, permanent denial of service on the airdrop-incentive distribution feature for all users, and the USDT balance held by the contract as the reward pool becomes permanently unclaimable/frozen since no owner withdrawal path exists for that token. This satisfies the "permanent freezing of funds" / "permanent freezing of unclaimed yield" impact bar.

### Likelihood Explanation
The likelihood is high given the intended use: `usdt` is a configuration parameter set once at `__arbWomUp_init()` and is very plausibly the real Tether token (the variable is literally named `usdt`), which is the textbook non-compliant ERC20 that this bug class targets. The failure is deterministic (not merely probabilistic) once such a token is used — it triggers on every call to the core user-facing function.

### Recommendation
Replace the raw `IERC20(usdt).transfer(...)` call with `IERC20(usdt).safeTransfer(...)` using the already-imported `SafeERC20` library (as is already done for the `wom` token in the same contract). Apply the same fix to the equivalent `busd`/`arb`/`mgp` transfers in `ArbWomUp2.sol` and `ArbWomUp3.sol`. Additionally, add an owner-only recovery function for the reward token in case of a stuck balance.

### Proof of Concept
1. Deploy `ArbWomUp` and initialize it with `usdt` pointing at a token contract that implements `transfer()`/`transferFrom()` without returning a `bool` (mirroring real mainnet/BSC USDT behavior).
2. Fund the contract with WOM allowance and USDT reward balance; call `setMultiplier` to configure reward tiers.
3. As any ordinary wallet, call `incentiveDeposit(_amount)`.
4. Observe: `_deposit(_amount)` succeeds (pulls WOM via `safeTransferFrom`), but the subsequent `IERC20(usdt).transfer(msg.sender, rewardToSend)` call reverts during ABI-decoding of the (missing) return value, causing the entire transaction — including the WOM deposit — to revert.
5. Every subsequent call to `incentiveDeposit()` reverts identically; the USDT reward pool remains stuck in the contract indefinitely with no admin recovery function available for that token.

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

**File:** wombat/ArbWomUp.sol (L119-125)
```text
    function _deposit(uint256 _amount) internal whenNotPaused {
        IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        userWOMDeposited[msg.sender] += _amount;
        totalAccumulated += _amount;

        emit WomDeposited(msg.sender, _amount);
    }
```

**File:** wombat/ArbWomUp.sol (L137-143)
```text
    function transferToAdmin() external onlyOwner {
        uint256 balance = IERC20(wom).balanceOf(address(this));
        if (balance == 0) revert ZeroBalance();
        IERC20(wom).transfer(owner(), balance);

        emit WomTransferredToAdmin(balance, owner());
    }
```

**File:** wombat/ArbWomUp2.sol (L91-96)
```text
        if (_bullMode) {
            _bullMGP(rewardToSend, _minMGPRec, msg.sender);
        } else {
            IERC20(busd).transfer(msg.sender, rewardToSend);
            emit BUSDRewarded(msg.sender, rewardToSend);
        }
```

**File:** wombat/ArbWomUp3.sol (L290-295)
```text
    function adminWithdrawReward() external onlyOwner {
        uint256 arbBalance = IERC20(arb).balanceOf(address(this));
        uint256 mgpBalance = IERC20(mgp).balanceOf(address(this));
        IERC20(arb).transfer(owner(), arbBalance);
        IERC20(mgp).transfer(owner(), mgpBalance);
    }
```
