### Title
Native ETH Stranded by Excess `msg.value` in Payable Swap Functions Is Stolen by Any Subsequent WETH Swap Caller — (`File: metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` function in `PeripheryPayments.sol` settles WETH obligations by consuming `address(this).balance` — the router's **entire** native ETH balance — rather than only the ETH attributable to the current swap. When any user calls a `payable` swap function (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`) with more ETH than the swap consumes, the surplus is silently stranded on the router. A subsequent unprivileged caller can then execute a WETH swap with zero `msg.value`, and `pay()` will use the stranded ETH to settle that caller's obligation, transferring the original user's funds to the attacker's benefit.

---

### Finding Description

`PeripheryPayments.pay()` handles WETH payments as follows:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol  lines 73-84
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
}
```

When `nativeBalance >= value`, the `payer` argument is **completely ignored**. The function wraps exactly `value` wei from the router's native balance and sends WETH to the pool. Any ETH above `value` remains on the router with no attribution to the original sender. [1](#0-0) 

Every public swap entry point is `payable` and calls `pay()` through the swap callback: [2](#0-1) [3](#0-2) 

None of these functions refund unused ETH. The `refundETH()` helper exists for this purpose but must be called explicitly by the user (typically via `multicall`): [4](#0-3) 

`refundETH()` sends `address(this).balance` to `msg.sender` with no access control — any caller receives the full router ETH balance, regardless of who deposited it.

The `_justPayCallback` path that invokes `pay()`: [5](#0-4) 

---

### Impact Explanation

**Direct theft of user ETH principal.** Any ETH stranded on the router from a prior transaction is available to the next caller who executes a WETH swap. The attacker pays nothing (zero `msg.value`) and receives tokens whose cost is borne entirely by the victim's stranded ETH. The loss equals `msg.value_victim − amountActuallyConsumed`, which can be arbitrarily large.

Additionally, any attacker who notices stranded ETH can call `refundETH()` directly to drain the full balance to themselves.

---

### Likelihood Explanation

**Medium–High.** The `exactOutputSingle` and `exactOutput` functions are the primary trigger: users cannot know the exact ETH required before execution (pool state is dynamic), so they routinely send a conservative excess. The `exactInputSingle` and `exactInput` functions are also affected when users send more ETH than `amountIn`. The attack requires only monitoring the router's ETH balance on-chain and submitting a zero-value WETH swap or a bare `refundETH()` call in the same or a subsequent block.

---

### Recommendation

1. **Track per-call ETH**: Record `address(this).balance` at the start of each swap entry point and pass only the delta (`balance_after_entry − balance_before_entry`) as the available native ETH to `pay()`, rather than the full `address(this).balance`.
2. **Auto-refund**: At the end of each `payable` swap function, refund any remaining `address(this).balance` to `msg.sender`.
3. **Restrict `refundETH`**: Limit `refundETH()` to the original depositor, or remove it as a standalone public function and expose it only through `multicall`.

---

### Proof of Concept

**Step 1 — Victim strands ETH:**

Alice calls `exactOutputSingle{value: 2 ether}(params)` where the pool only requires 1 ether of WETH input.

Inside the swap callback, `_justPayCallback` calls:
```
pay(WETH, Alice, pool, 1 ether)
```
`address(this).balance = 2 ether ≥ 1 ether`, so the function deposits exactly 1 ether as WETH and sends it to the pool. The remaining **1 ether stays on the router**. `exactOutputSingle` returns without refunding.

**Step 2 — Attacker steals:**

Bob calls `exactInputSingle{value: 0}(params)` with `params.tokenIn = WETH`, `params.amountIn = 1 ether`, targeting the same or any WETH pool.

Inside the callback:
```
pay(WETH, Bob, pool, 1 ether)
```
`address(this).balance = 1 ether` (Alice's stranded ETH) `≥ 1 ether`. The function deposits Alice's 1 ether as WETH and sends it to the pool. Bob receives tokens worth 1 ether. **Bob sent zero ETH and holds zero WETH; Alice's 1 ether is gone.**

Alternatively, Bob simply calls `refundETH()` after Alice's transaction and receives Alice's 1 ether directly. [6](#0-5) [7](#0-6) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
  }
```
