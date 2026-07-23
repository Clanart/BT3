The code is fully readable. Let me trace the exact execution path before rendering a verdict.

**Key facts established:**

1. `multicall` is `public payable` — ETH can be sent with it.
2. `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` are all `external payable`.
3. `receive()` only blocks *plain* ETH transfers (no calldata); payable function calls bypass it entirely — ETH sent with a payable call is credited to `address(this).balance` regardless.
4. If a user calls `exactInputSingle{value: 1 ether}(amountIn=0.5 ether WETH)` without a subsequent `refundETH`, 0.5 ETH is stranded in the router across transaction boundaries.
5. The `pay` function's WETH branch reads `address(this).balance` — the *entire* contract balance, not just the current caller's `msg.value`.

**Execution trace of the exploit:**

- userB: `exactInputSingle{value: 1 ether}(tokenIn=WETH, amountIn=0.5 ether)` — no `refundETH`. 0.5 ETH stranded.
- userA: `exactInputSingle(tokenIn=WETH, amountIn=1 ether)` with only 0.5 WETH approved.
- Pool callback fires → `pay(WETH, userA, pool, 1 ether)`:
  - `nativeBalance = 0.5 ether` (userB's stranded ETH)
  - Enters `else if (nativeBalance > 0)` branch
  - Deposits 0.5 ETH → WETH, transfers to pool
  - `safeTransferFrom(userA, pool, 0.5 WETH)` — only pulls 0.5 WETH from userA
  - Pool receives full 1 WETH; userA's swap succeeds
  - userB's 0.5 ETH is permanently gone

The PAYER_EXCLUSIVITY invariant is broken: `address(this).balance` is shared state across all callers, not scoped to the current transaction's `msg.value`.

---

### Title
Cross-User ETH Consumption in `PeripheryPayments.pay` WETH Branch — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay` uses `address(this).balance` as a shared pool when settling WETH payments. ETH stranded from a prior transaction (e.g., a user who sent excess ETH without calling `refundETH`) is silently consumed to partially fund a subsequent user's WETH swap, causing permanent loss of the stranded ETH.

### Finding Description
The `pay` function's WETH branch reads the contract's entire native ETH balance: [1](#0-0) 

When `0 < address(this).balance < value`, it deposits all available ETH as WETH and pulls only the remainder from `payer` via `transferFrom`. This logic is correct when the ETH in the contract was sent by the current caller in the same transaction. It is incorrect when ETH from a *prior* transaction is present, because that ETH belongs to a different user.

ETH can be stranded between transactions because all swap entry points are `payable`: [2](#0-1) 

A user who sends excess ETH with `exactInputSingle{value: X}(amountIn < X)` and omits `refundETH` leaves `X - amountIn` ETH in the contract permanently (until another user's swap consumes it). The `receive()` guard only blocks plain transfers; it does not prevent ETH from accumulating via payable calls. [3](#0-2) 

### Impact Explanation
Direct, permanent loss of user principal. The stranded ETH owner loses their ETH with no recourse; the subsequent swapper benefits by having their WETH requirement reduced. The pool receives the correct total, so pool solvency is unaffected, but the ETH owner suffers a full loss of the stranded amount. This meets the Critical/High threshold for direct loss of user principal.

### Likelihood Explanation
The intended usage pattern (shown in tests) is `multicall{value}([swap, refundETH])`. [4](#0-3) 

However, `refundETH` is not enforced on-chain. Any user who calls a payable swap function directly with excess ETH (without multicall+refundETH) will strand ETH. This is a realistic user error, and the stranded ETH is silently stolen by the next WETH swap rather than reverting or being recoverable.

### Recommendation
Scope the ETH available for WETH payment to `msg.value` rather than `address(this).balance`. Pass `msg.value` (or a tracked per-call ETH budget) down through the call stack to `pay`, and use only that amount in the WETH branch. Alternatively, track consumed ETH within the transaction and refund the remainder atomically at the end of each swap entry point, rather than relying on the caller to include `refundETH`.

### Proof of Concept
```
1. userB calls router.exactInputSingle{value: 1 ether}(
       tokenIn=WETH, amountIn=0.5 ether, ...
   )
   // pay() uses 0.5 ETH, 0.5 ETH stranded in router

2. userA (with only 0.5 WETH approved) calls router.exactInputSingle(
       tokenIn=WETH, amountIn=1 ether, ...
   )
   // pay(WETH, userA, pool, 1 ether):
   //   nativeBalance = 0.5 ether (userB's)
   //   deposits 0.5 ETH → WETH → pool
   //   transferFrom(userA, pool, 0.5 WETH)  ← only 0.5 WETH pulled
   // swap succeeds; userB's 0.5 ETH permanently lost
``` [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
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
