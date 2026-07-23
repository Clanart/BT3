### Title
Native ETH sent to non-WETH swap functions is silently stranded and immediately stealable via `refundETH` - (File: metric-periphery/contracts/MetricOmmSimpleRouter.sol / metric-periphery/contracts/base/PeripheryPayments.sol)

---

### Summary

All four swap entry points in `MetricOmmSimpleRouter` are declared `payable`, so the EVM accepts `msg.value` unconditionally. However, the internal `pay()` helper in `PeripheryPayments` only consumes the router's native ETH balance when `token == WETH`. When `tokenIn` is any other ERC-20, `pay()` falls through to `safeTransferFrom` and the native ETH is never touched. The stranded ETH is then immediately claimable by any third party through the public, attribution-free `refundETH()` helper.

---

### Finding Description

**Payable swap functions accept ETH regardless of `tokenIn`:** [1](#0-0) 

All four functions are `payable`. The EVM deposits `msg.value` into the contract's balance before execution begins, with no check that `tokenIn == WETH`.

**`pay()` only drains native ETH in the WETH branch:** [2](#0-1) 

When `token != WETH` and `payer != address(this)`, execution reaches the `else` branch at line 86 and calls `safeTransferFrom`. The `address(this).balance` is never read or spent. Any ETH that arrived via `msg.value` remains on the router after the swap completes.

**`refundETH()` is public and attribution-free:** [3](#0-2) 

`refundETH` sends the router's **entire** ETH balance to `msg.sender`. There is no record of who deposited the ETH, so any caller—including a front-running bot—can drain it.

---

### Impact Explanation

A user who sends ETH alongside a non-WETH swap (e.g., via a misconfigured multicall, a frontend bug, or a direct call) loses that ETH permanently. The loss is not bounded by dust thresholds: the user could send any amount. Because `refundETH` is public and stateless, the stranded ETH is extractable in the same block by an unrelated party. This constitutes a direct, unprivileged loss of user principal above Sherlock thresholds.

---

### Likelihood Explanation

The pattern is common in Uniswap v3-style routers: users are expected to pair swaps with `refundETH()` inside a `multicall`. Any user or integrator who:
- calls a swap function directly with `msg.value > 0` and `tokenIn != WETH`, or
- builds a multicall that sends ETH for a WETH hop but accidentally routes through a non-WETH pool first,

will strand ETH. MEV bots routinely monitor for stranded balances on well-known router addresses and call `refundETH` immediately.

---

### Recommendation

1. **Reject non-zero `msg.value` when `tokenIn != WETH`:** Add a guard at the top of each swap entry point (or inside `pay()`) that reverts if `msg.value > 0 && token != WETH`.
2. **Alternatively, add attribution to `refundETH`:** Track per-sender ETH deposits in transient storage and only refund the caller's own contribution. This matches the pattern used for the swap callback context.
3. **Emit an event or revert on leftover ETH:** After the swap completes, if `address(this).balance > 0` and `tokenIn != WETH`, revert or auto-refund `msg.sender`.

---

### Proof of Concept

```
Actors: Alice (victim), Bob (attacker)

1. Alice calls:
   router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
       pool:            address(pool),
       tokenIn:         address(token1),   // NOT WETH
       tokenOut:        address(token2),
       zeroForOne:      true,
       amountIn:        1_000,
       amountOutMinimum: 0,
       recipient:       alice,
       deadline:        block.timestamp + 1,
       priceLimitX64:   0,
       extensionData:   ""
   }));

   - EVM deposits 1 ETH into router.balance before execution.
   - Swap executes; callback calls pay(token1, alice, pool, 1000).
   - token1 != WETH → safeTransferFrom(alice, pool, 1000) succeeds.
   - address(router).balance == 1 ether (untouched).
   - Alice's 1 ETH is stranded.

2. Bob (in the same or next block) calls:
   router.refundETH();

   - balance = address(router).balance = 1 ether
   - _transferETH(bob, 1 ether) → Bob receives Alice's 1 ETH.
``` [4](#0-3) [5](#0-4) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/interfaces/IMetricOmmSimpleRouter.sol (L166-174)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);

  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut);

  // ============ Mutating: exact output ============

  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn);

  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn);
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
