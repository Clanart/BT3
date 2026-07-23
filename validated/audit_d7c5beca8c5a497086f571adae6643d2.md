Audit Report

## Title
Cross-User ETH Theft via Residual Router Balance in `pay()` WETH Branch — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` uses `address(this).balance` — the router's total native ETH — rather than the current call's `msg.value` to fund WETH payments. ETH stranded in the router from a prior user's overpayment can be consumed by a subsequent caller's WETH swap, constituting direct cross-user theft of principal.

## Finding Description
**Root cause:** In `pay()`, when `token == WETH` and `payer != address(this)`, the branch at line 74 reads the contract-level ETH accumulator:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
``` [1](#0-0) 

This balance is not scoped to the current transaction's `msg.value`.

**ETH stranding:** When User A calls `exactInputSingle` with `tokenIn=WETH` and `msg.value > amountIn`, `pay()` deposits exactly `amountIn` ETH and the surplus (`msg.value - amountIn`) remains in the router. The `receive()` guard only blocks non-WETH ETH pushes; it does not prevent accumulation from overpayment. [2](#0-1) 

**Exploit path:**
1. User A calls `exactInputSingle{value: 2 ether}` with `amountIn=1 ether`, never calls `refundETH()`. Router retains 1 ETH.
2. User B calls `exactOutputSingle{value: 0}` with `tokenIn=WETH`, `amountInMaximum=type(uint256).max`.
3. `_setNextCallbackContext` stores `payer=UserB`, `tokenToPay=WETH` in transient storage. [3](#0-2) 
4. The pool fires `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, UserB, pool, amountIn)`. [4](#0-3) 
5. Inside `pay()`, `nativeBalance = address(this).balance` equals User A's stranded 1 ETH. Since `nativeBalance >= amountIn`, the router wraps User A's ETH and transfers WETH to the pool — settling User B's swap entirely from User A's funds.

**Existing guards are insufficient:** The `amountIn > params.amountInMaximum` check at line 145 only caps the amount paid; it does not verify the source of funds. [5](#0-4) 

## Impact Explanation
User A loses ETH they could have recovered via `refundETH()`. User B receives full swap output without spending any ETH or WETH from their own balance. This is direct, cross-user theft of principal with no protocol-level recovery mechanism. Impact: **High**.

## Likelihood Explanation
ETH stranding arises organically from any WETH-path swap where `msg.value` exceeds actual swap cost (slippage buffers, partial fills, user error). The router's ETH balance is publicly readable on-chain. The attacker requires no privileged access, no malicious pool, and no non-standard token — only the observation of a non-zero router ETH balance followed by a call to `exactOutputSingle` with `tokenIn=WETH` and `msg.value=0`. Likelihood: **Medium**.

## Recommendation
Track the ETH available for the current call in transient storage at each `exactInput*`/`exactOutput*` entry point (storing `msg.value`), and use that tracked value — not `address(this).balance` — inside `pay()`. Alternatively, enforce that `address(this).balance` at the end of each swap entry equals `address(this).balance` at entry minus `msg.value` (asserting no residual ETH is consumed beyond what was sent in the current call).

## Proof of Concept
```solidity
function test_exactOutputSingle_stealsStrandedETH() public {
    // 1. User A sends 2 ETH but only 1 ETH is needed; does NOT call refundETH()
    vm.deal(userA, 2 ether);
    vm.prank(userA);
    router.exactInputSingle{value: 2 ether}(ExactInputSingleParams({
        tokenIn: WETH, amountIn: 1 ether, /* ... */
    }));
    assertEq(address(router).balance, 1 ether); // 1 ETH stranded

    // 2. User B calls exactOutputSingle with WETH tokenIn, zero msg.value
    vm.deal(userB, 0);
    vm.prank(userB);
    router.exactOutputSingle{value: 0}(ExactOutputSingleParams({
        tokenIn: WETH, amountOut: TARGET, amountInMaximum: type(uint256).max, /* ... */
    }));

    // 3. Router consumed User A's ETH; User B spent nothing
    assertEq(address(router).balance, 0);
    assertEq(IERC20(WETH).balanceOf(userB), 0); // no WETH spent by User B
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-135)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L145-146)
```text
    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
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
