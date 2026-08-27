## Analysis

The reported bug class (silently-failing ERC20 `transfer()` calls whose boolean return value is never checked, leading to loss of user funds) has a direct, unprivileged-wallet-reachable analog in this codebase's Magpie WOM-incentive contracts.

### Title
Unchecked ERC20 `transfer()` return value in `incentiveDeposit` causes silent loss of user reward tokens - (File: `wombat/ArbWomUp.sol`, `wombat/ArbWomUp2.sol`)

### Summary
`ArbWomUp.incentiveDeposit()` and `ArbWomUp2.incentiveDeposit()` are public entry points that let any wallet deposit WOM and receive an immediate USDT/BUSD incentive reward. Both functions call the raw ERC20 `transfer()` function directly and never check its return value, while unconditionally updating internal accounting (`claimedReward[msg.sender]`) beforehand.

### Finding Description
In `ArbWomUp.sol`, `incentiveDeposit` computes the reward, records it in `claimedReward`, and then sends it with a bare `transfer()` call whose return value is discarded: [1](#0-0) 

The same pattern exists in `ArbWomUp2.sol`, where the reward token is BUSD: [2](#0-1) 

Both `_deposit` helpers use `safeTransferFrom` for pulling WOM in from the user, showing the developers are aware of and use `SafeERC20` elsewhere in the same files — but the outbound reward transfer to the user is done with the unchecked, raw `IERC20.transfer()` call instead of `safeTransfer`. [3](#0-2) [4](#0-3) 

If the configured reward token (`usdt` / `busd`) is swapped for, or behaves as, a token that returns `false` on failed transfer instead of reverting (a common non-standard ERC20 behavior, e.g. paused/blacklist-style tokens), the `transfer()` call silently fails while `claimedReward[msg.sender]` has already been permanently incremented in the same transaction. Because `getRewardAmount()` subtracts already-`claimedReward` amounts from future reward calculations, the user can never claim that portion of the reward again — the tokens remain stranded in the contract with no user-facing recovery path (only `transferToAdmin()` exists, and it only sweeps the deposited WOM, not the USDT/BUSD reward token).

### Impact Explanation
Any ordinary user calling `incentiveDeposit()` is directly exposed: the accounting state (`claimedReward`) is updated unconditionally, so a failed-but-unreverted transfer results in a permanent, unrecoverable loss of the user's earned incentive reward — funds that are neither delivered to the user nor available for future claim. This matches the accepted Sherlock finding's core defect (unchecked transfer causing permanent fund loss to the end user) rather than a governance/oracle/admin issue.

### Likelihood Explanation
Likelihood depends on the reward token's implementation. As written, the code is not defensively coded against non-standard tokens (unlike other transfers in the same files that already use `SafeERC20`), so the exposure exists any time the reward token is set to (or migrated to) an ERC20 implementation that returns `false` rather than reverting on failure (e.g., due to a blacklist, pause, or insufficient allowance-like edge case). This is the same generic ERC20-compatibility risk the referenced report addresses, applied here to a genuinely unprivileged, permissionless entry point.

### Recommendation
Replace the raw `IERC20(usdt).transfer(...)` and `IERC20(busd).transfer(...)` calls in `ArbWomUp.incentiveDeposit` and `ArbWomUp2.incentiveDeposit` with OpenZeppelin's `SafeERC20.safeTransfer`, consistent with the `safeTransferFrom`/`safeApprove` usage already present in the same contracts. Also consider updating `claimedReward` only after confirming successful transfer, or wrapping the whole flow so a failed transfer reverts the entire transaction rather than partially updating state.

### Proof of Concept
1. Owner configures `usdt` (in `ArbWomUp`) to a token that returns `false` instead of reverting on transfer failure (e.g., due to a temporary pause/blacklist condition on the recipient, or a low-level call failure scenario for a non-standard token).
2. A user calls `incentiveDeposit(amount)`: `_deposit` pulls their WOM via `safeTransferFrom` (succeeds), `claimedReward[msg.sender]` is incremented by `rewardToSend`, and `IERC20(usdt).transfer(msg.sender, rewardToSend)` returns `false` without reverting.
3. The transaction completes successfully (no revert), emitting `USDTRewarded`, but the user never received the USDT.
4. On any subsequent `incentiveDeposit` call, `getRewardAmount` subtracts the already-recorded `claimedReward[msg.sender]`, permanently reducing/zeroing out the user's future reward — the previously "sent" USDT remains stuck in the contract with no path for the user to reclaim it.

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

**File:** wombat/ArbWomUp2.sol (L154-160)
```text
    function _deposit(uint256 _amount) internal {
        IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        userWOMDeposited[msg.sender] += _amount;
        totalAccumulated += _amount;

        emit WomDeposited(msg.sender, _amount);
    }
```
