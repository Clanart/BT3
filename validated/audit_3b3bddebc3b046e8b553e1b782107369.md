### Title
`pay()` uses `safeTransfer` instead of `safeTransferFrom` when WETH is the input token and no native ETH is present, allowing theft of any WETH stranded on the router — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` contains a wrong branch in its WETH-handling path. When `token == WETH`, `payer != address(this)`, and the router holds zero native ETH, the code calls `IERC20(WETH).safeTransfer(recipient, value)` — transferring from the **router's own WETH balance** — instead of `IERC20(WETH).safeTransferFrom(payer, recipient, value)`, which would pull from the **user**. Any WETH that accumulates on the router (e.g., from a prior swap that directed WETH output to the router address) can be stolen by any caller who invokes `exactInputSingle` with `tokenIn = WETH` and sends no ETH.

---

### Finding Description

`PeripheryPayments.pay` has three branches for the WETH case:

```
else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);          // wrap ETH → send WETH ✓
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // hybrid ✓
    } else {
        IERC20(WETH).safeTransfer(recipient, value);          // ← BUG: should be safeTransferFrom
    }
}
``` [1](#0-0) 

The third branch (`nativeBalance == 0`) is reached whenever a user calls `exactInputSingle` (or any single-hop swap) with `tokenIn = WETH` and sends no ETH. Instead of pulling WETH from the user via `safeTransferFrom`, the router attempts to transfer from its own WETH balance. If the router holds WETH (stranded from any prior operation), the pool is paid and the swap succeeds — but the user pays nothing.

The callback path that reaches `pay` is:

1. `exactInputSingle` sets `payer = msg.sender` and `tokenToPay = params.tokenIn` in transient storage.
2. The pool calls `metricOmmSwapCallback` on the router.
3. The router dispatches to `_justPayCallback`, which calls `pay(_getTokenToPay(), _getPayer(), msg.sender, amount)`.
4. With `payer = user` (not `address(this)`), `token = WETH`, and `nativeBalance = 0`, the buggy `else` branch fires. [2](#0-1) 

WETH can accumulate on the router when a user calls `exactInputSingle` with `tokenOut = WETH` and `recipient = address(router)` (a valid pattern for chaining operations in a multicall). If that WETH is not consumed in the same transaction (e.g., the multicall is not used, or a subsequent step is skipped), it becomes stranded and stealable.

---

### Impact Explanation

Any WETH balance held by the router is freely claimable by any caller. The attacker:
1. Observes stranded WETH on the router.
2. Calls `exactInputSingle` with `tokenIn = WETH`, `amountIn ≤ router's WETH balance`, and no ETH.
3. The router pays the pool from its own WETH balance; the attacker receives the output token without spending anything.

This is a direct loss of user principal (the WETH that was stranded belongs to a prior user who directed output to the router).

---

### Likelihood Explanation

WETH strands on the router whenever a user calls `exactInputSingle` with `tokenOut = WETH` and `recipient = address(router)` outside of a multicall, or when a multicall is composed such that an intermediate WETH output is not consumed. The `sweepToken` helper is public but requires a separate transaction; a front-runner can steal the WETH before the sweep lands. The trigger is fully unprivileged.

---

### Recommendation

Replace the buggy `else` branch with `safeTransferFrom`:

```solidity
} else {
    // No native ETH: pull WETH directly from the payer
    IERC20(WETH).safeTransferFrom(payer, recipient, value);
}
``` [3](#0-2) 

---

### Proof of Concept

```solidity
// 1. Strand WETH on the router (simulates a prior user directing WETH output to the router)
weth.transfer(address(router), 1_000e18);

// 2. Attacker calls exactInputSingle with tokenIn=WETH, no ETH, no WETH approval
uint256 stolen = router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(wethTokenPool),
        tokenIn:          address(weth),
        tokenOut:         address(token1),
        zeroForOne:       true,
        amountIn:         1_000e18,
        amountOutMinimum: 0,
        recipient:        attacker,
        deadline:         block.timestamp + 1,
        priceLimitX64:    0,
        extensionData:    ""
    })
);
// Router's WETH balance is now 0; attacker received token1 without paying.
assert(weth.balanceOf(address(router)) == 0);
assert(token1.balanceOf(attacker) == stolen);
```

The pool's `IncorrectDelta` check passes because the router did transfer WETH to the pool — just from its own balance rather than the payer's. The payer's WETH balance and allowance are never touched. [4](#0-3) [5](#0-4)

### Citations

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
