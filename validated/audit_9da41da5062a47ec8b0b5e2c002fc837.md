I have all the code I need. Let me trace the exact execution path.

## Execution Trace

**`exactInputSingle` → `_justPayCallback` → `pay()`**

```
exactInputSingle(tokenIn=WETH, amountIn=0.5e18) {value: 1e18}
  → _setNextCallbackContext(pool, JUST_PAY, msg.sender, WETH)
  → pool.swap(...)
      → metricOmmSwapCallback(amount0Delta, amount1Delta, "")
          → _justPayCallback(...)
              → pay(WETH, msg.sender, pool, 0.5e18)
```

**Inside `pay()` for the WETH branch:** [1](#0-0) 

```
nativeBalance = address(this).balance = 1e18
value = 0.5e18
→ nativeBalance >= value → wraps exactly 0.5e18, transfers WETH to pool
→ remaining 0.5e18 ETH stays in router
```

**After `exactInputSingle` returns:** [2](#0-1) 

No automatic refund. The function clears the callback pool and returns `amountOut`. The 0.5e18 ETH is silently stranded.

**`refundETH()` has no ownership tracking:** [3](#0-2) 

It sends `address(this).balance` to `msg.sender` — any caller, not the original depositor.

---

## Assessment

### Title
Excess ETH sent with `exactInputSingle` is stranded in the router and can be stolen by any caller via `refundETH()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
When `exactInputSingle` is called with `tokenIn=WETH` and `msg.value > amountIn`, `pay()` wraps only `amountIn` worth of ETH and sends it to the pool. The surplus ETH remains in the router with no refund and no ownership record. Any third party can immediately call `refundETH()` to drain the entire ETH balance of the router, stealing the user's surplus.

### Finding Description
The `pay()` function's WETH branch correctly wraps only the required `value` when `nativeBalance >= value`: [4](#0-3) 

This is correct for payment purposes, but `exactInputSingle` performs no post-swap ETH refund: [5](#0-4) 

The `refundETH()` function is permissionless and sends the full router ETH balance to `msg.sender`: [3](#0-2) 

There is no mapping of depositor → amount. Any EOA or contract can call `refundETH()` in a subsequent transaction and receive the stranded ETH. A `multicall` does not help here unless the user explicitly includes `refundETH()` as a final entry — a step that is not enforced or documented.

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only applies to direct ETH transfers, not to ETH arriving via a `payable` function call, so it provides no protection here. [6](#0-5) 

### Impact Explanation
Direct loss of user ETH. A user who sends `msg.value=1e18` with `amountIn=0.5e18` loses 0.5e18 ETH to any MEV bot or frontrunner that calls `refundETH()` in the same or next block. The loss scales with the surplus sent.

### Likelihood Explanation
Medium. Users interacting directly with the router (not via a frontend that bundles `refundETH()` in a multicall) are exposed. Frontends that omit the refund step, or users who call the router directly, will routinely leave surplus ETH. MEV bots already monitor routers for stranded ETH.

### Recommendation
Add an automatic ETH refund at the end of each payable swap function:

```solidity
// at the end of exactInputSingle, exactInput, exactOutputSingle, exactOutput
uint256 surplus = address(this).balance;
if (surplus > 0) _transferETH(msg.sender, surplus);
```

Alternatively, enforce that `msg.value == amountIn` when `tokenIn == WETH` and revert otherwise, eliminating the ambiguity entirely.

### Proof of Concept
1. User calls `exactInputSingle({tokenIn: WETH, amountIn: 0.5e18, ...})` with `msg.value = 1e18`.
2. `pay()` wraps 0.5e18 ETH → sends WETH to pool. Router now holds 0.5e18 ETH.
3. `exactInputSingle` returns. No refund issued.
4. Attacker calls `refundETH()`. Router sends 0.5e18 ETH to attacker.
5. User's 0.5e18 ETH is permanently lost.

The same path applies to `exactInput` (first hop, `i==0`, payer=`msg.sender`, token=WETH) and `exactOutputSingle`/`exactOutput`.

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
