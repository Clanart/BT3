The code is clear. Let me confirm the exact `pay()` branch behavior for the scenario described.

The exploit path is fully confirmed. Here is the assessment:

---

### Title
`refundETH()` Has No Caller Attribution — Any Address Can Steal ETH Stranded by a Prior `exactInputSingle{value: excess}` Call — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`refundETH()` is an unrestricted public function that transfers the router's entire native ETH balance to `msg.sender`. Because `exactInputSingle` is `payable` and the `pay()` helper only consumes exactly `amountIn` worth of native ETH (leaving any excess on the contract), a user who sends more ETH than `amountIn` without bundling a `refundETH` step in the same `multicall` will strand the surplus on the router. Any attacker can then call `refundETH()` in a subsequent transaction and receive the victim's ETH.

### Finding Description

`exactInputSingle` is declared `payable`: [1](#0-0) 

When `tokenIn == WETH` and `msg.value > amountIn`, the `pay()` helper is invoked during the swap callback. It reads `address(this).balance` and, because `nativeBalance >= value`, deposits exactly `amountIn` worth of ETH as WETH and forwards it to the pool: [2](#0-1) 

The remaining `msg.value - amountIn` ETH is never returned automatically. It sits on the router after the call returns.

`refundETH()` has no access control and no per-depositor accounting. It unconditionally sends the full contract balance to `msg.sender`: [3](#0-2) 

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `payable` function calls: [4](#0-3) 

There is no mechanism that ties stranded ETH to the original depositor. Any caller of `refundETH()` in a later transaction claims the entire balance.

### Impact Explanation

Direct theft of user ETH. A victim who calls `exactInputSingle{value: 1 ether}` with `amountIn = 0.5 ether` loses 0.5 ETH to the first attacker who calls `refundETH()` afterward. The loss is bounded only by the victim's `msg.value` overage and is repeatable across every such transaction.

### Likelihood Explanation

The intended safe pattern — `multicall{value}([exactInputSingle(...), refundETH()])` — is shown in tests: [5](#0-4) 

However, `exactInputSingle` is a standalone `payable` function with no enforcement that it must be called through `multicall`. Any user who calls it directly with excess ETH (a natural mistake, especially for wallets or integrators that estimate gas/value conservatively) will strand funds. An attacker can monitor the mempool for such calls and back-run them with `refundETH()`.

### Recommendation

Two complementary fixes:

1. **Automatic refund at the end of each swap entry point**: after `_clearExpectedCallbackPool()`, refund `address(this).balance` to `msg.sender` unconditionally (matching how Uniswap v3 periphery handles this in later revisions).
2. **Restrict `refundETH()` to `msg.sender` only within `multicall`**: since `multicall` uses `delegatecall`, `msg.sender` is preserved, so a `refundETH` step inside a multicall already sends to the original caller. The standalone external exposure is the risk; removing the `external` visibility or adding a reentrancy-safe caller check would close the cross-transaction theft window.

### Proof of Concept

```
// Tx 1 — victim
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    pool: pool,
    tokenIn: WETH,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 0.5 ether,   // only 0.5 ETH consumed by pay()
    amountOutMinimum: 0,
    recipient: victim,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// router.balance == 0.5 ether after this tx

// Tx 2 — attacker (separate transaction, no prior interaction)
router.refundETH();
// attacker receives 0.5 ether; victim's ETH is gone
assert(attacker.balance increased by 0.5 ether);
assert(router.balance == 0);
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
