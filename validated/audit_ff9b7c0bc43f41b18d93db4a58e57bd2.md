### Title
Missing Payer-Attribution Check in `pay()` Allows Theft of Router-Held Native ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses the router's entire native-ETH balance to settle a WETH swap leg without verifying that the ETH was deposited by the current payer. Any ETH left on the router by a prior user can be consumed by a subsequent unprivileged caller who supplies `tokenIn = WETH` with zero `msg.value`, stealing the prior user's funds.

---

### Finding Description

`pay()` is the single settlement primitive for all router swap callbacks: [1](#0-0) 

When `token == WETH` and `payer != address(this)`, the function inspects `address(this).balance` — the router's **total** native-ETH balance — and uses it to fund the WETH deposit before pulling any shortfall from the payer: [2](#0-1) 

There is no check that the native ETH on the router was deposited by the current payer. The router accumulates ETH whenever a user sends `msg.value` that exceeds the swap's actual WETH cost and does not include a `refundETH()` call in the same multicall. The tests explicitly demonstrate this residue pattern: [3](#0-2) 

The `exactInputSingle` entry-point sets `payer = msg.sender` and then calls the pool, which triggers the callback and ultimately `pay()`: [4](#0-3) 

No guard in `exactInputSingle`, `metricOmmSwapCallback`, or `_justPayCallback` checks whether `msg.value >= amountIn` or whether the router's native balance is attributable to the current caller. The callback path is: [5](#0-4) 

**Analog to the external bug**: `_getIndex` returns index `0` by default when a token is not found, causing a non-member token to be treated as pool token `0`. Here, `pay()` returns the router's accumulated ETH balance by default when the current payer has sent no ETH, causing a non-contributing payer to be treated as if they funded the swap — the same class of "missing existence/ownership check produces a wrong default value."

---

### Impact Explanation

**Direct loss of user principal.** Any ETH stranded on the router (from a prior user's `msg.value` overshoot or a failed `refundETH()` step) is fully claimable by the next caller who submits `exactInputSingle` or `exactInput` with `tokenIn = WETH` and zero `msg.value`. The attacker receives the full swap output; the victim's ETH is consumed by the pool as WETH payment. Loss is bounded only by the router's instantaneous ETH balance, which can be up to the victim's entire `msg.value`.

---

### Likelihood Explanation

**High.** The residue condition is created by any WETH-input swap where the user does not append `refundETH()` — a common omission when users call `exactInputSingle` directly rather than through a multicall bundle. The exploit requires no special role, no flash loan, and no prior state setup: a public `exactInputSingle(tokenIn=WETH, amountIn=routerBalance)` call is sufficient. MEV bots can detect the router's ETH balance on-chain and front-run the victim's `refundETH()`.

---

### Recommendation

Add a payer-attribution guard in the WETH branch of `pay()`. Only consume native ETH from the router when it was explicitly deposited by the current transaction (i.e., `msg.value` of the current top-level call). One approach: track the "current-call ETH budget" in transient storage at the `multicall`/`exactInputSingle` entry-point and deduct from it inside `pay()`, reverting if the budget is exhausted. Alternatively, require `msg.value >= amountIn` at the `exactInputSingle` level when `tokenIn == WETH` and the payer is external, and pull any shortfall exclusively via `safeTransferFrom`.

---

### Proof of Concept

```
1. Victim calls exactInputSingle{value: 1 ETH}(
       pool=WETH/TOKEN1, tokenIn=WETH, amountIn=0.5 ETH, recipient=victim, ...
   )
   — pay() deposits 0.5 ETH as WETH, transfers to pool.
   — Router retains 0.5 ETH (victim forgot refundETH()).

2. Attacker (no ETH, no WETH approval) calls exactInputSingle{value: 0}(
       pool=WETH/TOKEN1, tokenIn=WETH, amountIn=0.5 ETH, recipient=attacker, ...
   )
   — _setNextCallbackContext sets payer=attacker, tokenToPay=WETH.
   — Pool executes swap, calls metricOmmSwapCallback.
   — _justPayCallback calls pay(WETH, attacker, pool, 0.5 ETH).
   — address(this).balance == 0.5 ETH >= 0.5 ETH → branch taken.
   — Router deposits victim's 0.5 ETH as WETH, transfers to pool.
   — Attacker receives TOKEN1 output; victim's 0.5 ETH is gone.
```

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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
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
