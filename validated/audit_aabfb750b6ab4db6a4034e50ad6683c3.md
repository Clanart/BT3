### Title
Unchecked `IERC20.transfer()` in `incentiveDeposit()` risks silent loss of user incentive rewards - ([File: wombat/ArbWomUp.sol], [File: wombat/ArbWomUp2.sol])

### Summary
Both `ArbWomUp.sol` and `ArbWomUp2.sol` are user-facing airdrop/incentive-distribution contracts that let any wallet deposit WOM and receive a USDT/BUSD reward. Even though both contracts import `SafeERC20` and declare `using SafeERC20 for IERC20`, the reward payout itself uses the raw, unchecked `IERC20(...).transfer()` call instead of `safeTransfer()`.

### Finding Description
In `ArbWomUp.sol`, `incentiveDeposit()` records the reward as claimed and then pays it out with a raw transfer: [1](#0-0) 

The same pattern exists in `ArbWomUp2.sol`: [2](#0-1) 

Both contracts already import and alias `SafeERC20`/`using SafeERC20 for IERC20` (as in the referenced report's root cause), and correctly use `safeTransferFrom`/`safeApprove` elsewhere in the same contracts (e.g. `_deposit()`), but forget to use `safeTransfer` for the reward payout to `msg.sender`. This is the same bug class as the referenced `BribeVault.sol#transferBribes()` finding: a raw `.transfer()` call is used where `safeTransfer` was clearly intended.

Critically, in both functions `claimedReward[msg.sender] += rewardToSend;` is updated **before** the unchecked `transfer()` call. If the configured reward token (`usdt`/`busd`) is a non-standard ERC20 that returns `false` on failure instead of reverting (rather than a token that omits the return value entirely and would cause an ABI-decode revert), the unchecked call would not detect the failure, and `claimedReward` would already reflect the reward as paid. This permanently overstates the user's claimed amount in `getRewardAmount()`'s accounting (`- claimedReward[_account]` / `- calDoubledCounted(_account)`), so the user's yield allotment is permanently reduced/lost with no way to reclaim it.

### Impact Explanation
This directly affects an unprivileged wallet interacting with a normal distribution function. If the payout silently fails, the affected user's incentive reward is deducted from their entitlement bookkeeping without the funds ever reaching them, resulting in a permanent loss of that unclaimed yield with no owner-only recovery path in these functions.

### Likelihood Explanation
Likelihood depends on the specific ERC20 token configured as `usdt`/`busd` at `setup()`/init time. For fully compliant tokens (which return `true`/revert normally), this code path does not misbehave functionally. It only manifests for tokens that return `false` on failure without reverting — a known ERC20 non-compliance pattern (distinct from, but adjacent to, the classic USDT-no-return-value issue referenced in the report). This is a real code defect (unchecked return value) inconsistent with the rest of the codebase's `safeTransfer`/`safeApprove` usage, though real-world exploitability is contingent on the token implementation actually deployed.

### Recommendation
Replace the raw `IERC20(usdt).transfer(msg.sender, rewardToSend)` and `IERC20(busd).transfer(msg.sender, rewardToSend)` calls with `safeTransfer`, consistent with the rest of each contract. Additionally, consider moving `claimedReward[msg.sender] += rewardToSend;` to after a successful transfer (or checking-effects-interactions with a verified transfer) so a failed payout cannot be recorded as claimed.

### Proof of Concept
1. Admin configures `usdt` (in `ArbWomUp.sol`) or `busd` (in `ArbWomUp2.sol`) to a token whose `transfer()` returns `false` on failure without reverting (e.g., insufficient allowance/limits imposed by the token itself, or a token with non-standard failure semantics).
2. A user calls `incentiveDeposit(amount)`, depositing WOM via `safeTransferFrom` (succeeds) at [3](#0-2) .
3. `claimedReward[msg.sender] += rewardToSend` executes at [4](#0-3) , then `IERC20(usdt).transfer(msg.sender, rewardToSend)` at line 76 returns `false` silently.
4. The transaction succeeds overall (no revert), the user's WOM is locked in, but the reward tokens never arrive, and `claimedReward` now blocks the user from claiming that reward amount again in future calls via `getRewardAmount()`.

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
