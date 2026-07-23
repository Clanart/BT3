Audit Report

## Title
Router `pay()` Consumes Unattributed Native ETH Balance to Settle Any Caller's WETH Obligation, Enabling Theft of Stranded ETH — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay()` function reads `address(this).balance` — the router's total native ETH — when settling a WETH obligation, with no per-user attribution. Any ETH left on the router from a prior caller who omitted `refundETH()` is indistinguishable from the current caller's ETH, allowing a second unprivileged user to consume the first user's stranded ETH to fund their own swap at zero cost.

## Finding Description
In `PeripheryPayments.sol` lines 73–84, when `token == WETH` and `payer != address(this)`, `pay()` unconditionally reads `address(this).balance` and uses it first before pulling from `payer`:

```solidity
uint256 nativeBalance = address(this).balance;   // entire router ETH
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

ETH legitimately arrives on the router via `msg.value` in `multicall` (line 39) or `exactInputSingle` (line 67), both of which are `payable`. The `receive()` guard (lines 32–34) only blocks direct pushes from non-WETH addresses; it does not prevent `msg.value` from accumulating. `refundETH()` (lines 58–63) is optional and not enforced.

The exploit path:
1. User A calls `multicall{value: 1 ether}([exactInputSingle(amountIn=0.5 ether, tokenIn=WETH)])` without appending `refundETH()`. The swap consumes 0.5 ETH; 0.5 ETH remains on the router.
2. User B (no ETH, no WETH, no approval) calls `exactInputSingle(tokenIn=WETH, amountIn=0.5 ether)`.
3. `exactInputSingle` sets callback context with `payer=userB` (line 71), calls `pool.swap`, which triggers `metricOmmSwapCallback` → `_justPayCallback` (lines 192–199) → `pay(WETH, userB, pool, 0.5 ether)`.
4. `address(this).balance == 0.5 ether` (User A's ETH). The router deposits it as WETH and transfers to the pool on behalf of User B.
5. User B receives full swap output. User A's 0.5 ETH is permanently lost.

No existing guard prevents this: the callback pool check (`_requireExpectedCallbackCaller`) only validates the caller is the expected pool, not that the ETH being spent belongs to the current user.

## Impact Explanation
Direct loss of user principal. User A's stranded ETH is consumed to settle User B's WETH obligation. User B receives full swap output without spending any ETH or WETH. The loss equals 100% of the stranded ETH amount, which trivially exceeds the Critical threshold for any non-dust amount. This is a direct fund loss impact matching the allowed impact gate (direct loss of user principal above Sherlock thresholds).

## Likelihood Explanation
Medium. ETH stranding requires a user to send `msg.value > amountIn` without appending `refundETH()`. This is a realistic omission: integrators or wallets constructing multicalls may over-estimate `msg.value` as a safety buffer and omit `refundETH()`. A user calling `exactInputSingle` directly with `msg.value > amountIn` also strands the excess. Once ETH is stranded, exploitation requires zero privileges — any address can trigger it with a single public call.

## Recommendation
Replace the unconditional `address(this).balance` read with only the ETH the current call context is entitled to use. Options:
1. Pass `msg.value` explicitly into `pay()` for the WETH branch and use only that amount.
2. Snapshot the balance before the swap and use only the delta: `uint256 nativeBalance = address(this).balance - _balanceBeforeCall`.
3. Enforce atomic refund at the end of `multicall`: revert if `address(this).balance > 0` after all delegatecalls complete, so no ETH can ever be stranded.

## Proof of Concept
```
Setup:
  - Router deployed with WETH address.
  - Pool(WETH, token1) exists with liquidity.
  - User A has 1 ETH. User B has 0 ETH, 0 WETH, no approvals.

Step 1 — User A strands ETH:
  vm.prank(userA);
  bytes[] memory calls = new bytes[](1);
  calls[0] = abi.encodeCall(router.exactInputSingle, ExactInputSingleParams({
      pool: pool, tokenIn: WETH, tokenOut: token1,
      zeroForOne: true, amountIn: 0.5 ether,
      amountOutMinimum: 0, recipient: userA,
      deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
  }));
  router.multicall{value: 1 ether}(calls);
  // 0.5 ETH used; 0.5 ETH stranded (no refundETH).
  assertEq(address(router).balance, 0.5 ether);

Step 2 — User B steals stranded ETH:
  vm.prank(userB);
  router.exactInputSingle(ExactInputSingleParams({
      pool: pool, tokenIn: WETH, tokenOut: token1,
      zeroForOne: true, amountIn: 0.5 ether,
      amountOutMinimum: 0, recipient: userB,
      deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
  }));
  // pay(WETH, userB, pool, 0.5 ether) fires; address(this).balance == 0.5 ether (User A's).
  // Router deposits User A's ETH as WETH, sends to pool. User B gets token1 output.
  assertEq(address(router).balance, 0);
  assertGt(token1.balanceOf(userB), 0);
```