Let me check the base router and transient storage context to complete the trace.

### Title
Stranded ETH in Router Consumed by Attacker's WETH Swap via Partial-ETH Branch in `pay()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` function's partial-ETH branch uses the router's **entire native ETH balance** — including ETH stranded by prior users — to partially fund a WETH swap, pulling only the remainder from the actual payer. An attacker who calls `exactInputSingle(tokenIn=WETH)` while the router holds stranded ETH pays less WETH than the pool receives, effectively stealing the stranded ETH.

---

### Finding Description

`PeripheryPayments.pay()` has three branches for `token == WETH`: [1](#0-0) 

The middle branch (`nativeBalance > 0 && nativeBalance < value`) wraps **all** of `address(this).balance` into WETH and sends it to the pool, then pulls only `value - nativeBalance` from the payer via `transferFrom`. The critical flaw is that `address(this).balance` is the router's **total** ETH balance — it is not scoped to the current caller's `msg.value`.

ETH can be stranded in the router through any `payable` entrypoint:

- `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` / `multicall` are all `payable`. [2](#0-1) 

- The `receive()` guard only blocks **plain ETH transfers** (no calldata); it does **not** block ETH sent as `msg.value` alongside a function call. [3](#0-2) 

So if User A calls `exactInputSingle(tokenIn=USDC, msg.value=30 ETH)`, the 30 ETH is accepted by the router but never consumed (the `pay()` call for USDC takes the `safeTransferFrom` branch). The ETH sits in the router until someone calls `refundETH()` — which is a separate, optional call.

**Attack flow:**

1. User A calls `exactInputSingle(tokenIn=USDC, amountIn=X, msg.value=30 ETH)` without a subsequent `refundETH()`. Router now holds 30 ETH.
2. Attacker calls `exactInputSingle(tokenIn=WETH, amountIn=100, msg.value=0)`.
3. Router sets callback context: `payer=attacker`, `token=WETH`. [4](#0-3) 
4. Pool executes the swap and calls `metricOmmSwapCallback`, which calls `_justPayCallback`: [5](#0-4) 
5. `pay(WETH, attacker, pool, 100)` is invoked. `nativeBalance = 30`. The partial-ETH branch fires: [6](#0-5) 
6. Router wraps 30 ETH → 30 WETH → pool. Then `safeTransferFrom(attacker, pool, 70)`. Pool receives 100 WETH; attacker paid only 70 WETH from their own balance.

User A's 30 ETH is permanently lost to the attacker.

---

### Impact Explanation

Direct loss of user principal. Any ETH stranded in the router (from `msg.value` sent with non-WETH swaps, excess ETH in WETH swaps, or `multicall` without `refundETH()`) can be stolen by any attacker who monitors the router's ETH balance and submits a WETH swap for `amountIn > stranded_balance`. The attacker profits by exactly `min(stranded_balance, amountIn)` at the expense of the prior user.

---

### Likelihood Explanation

The `multicall` + `refundETH()` pattern is standard UX for WETH-paying routers, but it is easy to omit `refundETH()` or to send ETH with a non-WETH swap by mistake. Frontrunners can watch the mempool or the router's ETH balance on-chain and exploit any stranded ETH atomically in the same block it appears.

---

### Recommendation

In the partial-ETH branch, only use `msg.value` (the ETH the current caller explicitly sent), not `address(this).balance`. Track the current call's ETH contribution separately, or restrict the WETH-from-ETH path to `nativeBalance == msg.value` (i.e., only use ETH that arrived with this exact transaction). Alternatively, remove the partial-ETH branch entirely and require callers to either send the full `value` as ETH or approve the full `value` as WETH — never a mix sourced from the contract's ambient balance.

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_strandedEthStolenViaWethSwap() public {
    // 1. User A accidentally sends 30 ETH with a USDC swap (no refundETH call)
    vm.deal(userA, 30 ether);
    vm.prank(userA);
    router.exactInputSingle{value: 30 ether}(ExactInputSingleParams({
        pool: usdcEthPool,
        tokenIn: USDC,
        ...
        amountIn: 1000e6
    }));
    // Router now holds 30 ETH

    // 2. Attacker has 70 WETH approved to router, wants 100 WETH worth of output
    deal(WETH, attacker, 70 ether);
    vm.prank(attacker);
    IERC20(WETH).approve(address(router), 70 ether);

    uint256 attackerWethBefore = IERC20(WETH).balanceOf(attacker);

    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: wethTokenPool,
        tokenIn: WETH,
        amountIn: 100 ether,
        ...
    }));

    // Attacker paid only 70 WETH; pool received 100 WETH (30 from stranded ETH)
    assertEq(attackerWethBefore - IERC20(WETH).balanceOf(attacker), 70 ether);
    assertEq(address(router).balance, 0); // 30 ETH consumed
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmSimpleRouter.sol (L166-174)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);

  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut);

  // ============ Mutating: exact output ============

  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn);

  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn);
```

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
