The code is clear. Let me trace the exact path:

**`refundETH()` in `PeripheryPayments.sol`:** [1](#0-0) 

It is `external payable` with zero access control — it sends the entire contract ETH balance to `msg.sender`, unconditionally.

**`pay()` in `PeripheryPayments.sol` for WETH token:** [2](#0-1) 

When `token == WETH` and `nativeBalance >= value`, it wraps exactly `value` ETH and transfers it to the pool. Any ETH sent above `value` is **not consumed** — it stays on the router.

**`exactInputSingle` is `payable`:** [3](#0-2) 

It accepts ETH, calls `pay()` which only wraps the exact amount needed, and returns without refunding the surplus.

**`receive()` guard is irrelevant here:** [4](#0-3) 

The `receive()` guard only blocks unsolicited ETH pushes. ETH sent by the user via `msg.value` in `exactInputSingle` bypasses it entirely — it arrives as part of the call, not via a separate `receive()` invocation.

The attack path is fully valid:

---

### Title
Permissionless `refundETH()` allows any caller to steal excess ETH left on the router after a swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.refundETH()` sends the entire native ETH balance of the router to `msg.sender` with no access control. Any ETH left on the router after a user's swap (because the user sent more ETH than the swap required and omitted `refundETH` from their multicall) can be immediately stolen by any third party in a subsequent transaction.

### Finding Description
`exactInputSingle` (and other `payable` swap entry points) accept `msg.value`. Inside `pay()`, when `token == WETH`, only the exact amount owed to the pool is wrapped; surplus ETH remains on the contract. The user is expected to include `refundETH()` in the same `multicall` to recover the surplus. However, `refundETH()` is a standalone `external payable` function with no check that the caller is the original depositor, no transient-storage binding to the current multicall initiator, and no per-user accounting. Any EOA or contract can call it at any time and drain the full ETH balance to themselves.

### Impact Explanation
Direct loss of user principal. A user who sends 1 ETH for a 0.5 ETH-cost swap and omits `refundETH` from their multicall loses 0.5 ETH to the first attacker who calls `refundETH()` in the next block. The loss is bounded only by how much ETH the victim over-sent; it is not dust.

### Likelihood Explanation
Moderate. The pattern of "send ETH → swap → refundETH in multicall" is the documented usage, but omitting the final step is a realistic user error. MEV bots routinely monitor routers for stranded ETH and will extract it within the same block. No special privileges or setup are required by the attacker.

### Recommendation
Bind `refundETH` to the multicall initiator using transient storage. At the start of `multicall`, record `msg.sender` in a transient slot. In `refundETH`, assert that `msg.sender == tload(MULTICALL_SENDER_SLOT)` (or that the call is occurring within an active multicall context). This ensures only the originating user can claim their own surplus ETH.

### Proof of Concept
1. User calls `router.multicall{value: 1 ether}([exactInputSingle(..., amountIn: 0.5 ether, ...)])` — note: no `refundETH` call included.
2. `pay()` wraps 0.5 ETH → WETH and pays the pool. `address(router).balance == 0.5 ether`.
3. Attacker calls `router.refundETH()` in the next transaction.
4. `refundETH` reads `balance = 0.5 ether`, calls `_transferETH(msg.sender, 0.5 ether)`.
5. Attacker receives 0.5 ETH. User's surplus is permanently lost.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
