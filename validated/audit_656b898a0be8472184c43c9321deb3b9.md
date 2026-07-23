I have all the code I need. Let me analyze the vulnerability claim against the actual implementation.

The vulnerability is real and traceable through the production code. Here is the full analysis:

---

### Title
Residual ETH in Router Silently Subsidizes Subsequent WETH Swaps, Stealing Prior User's Stranded ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`PeripheryPayments.pay` contains a partial-ETH branch that wraps any native ETH sitting in the router before pulling the remainder from the payer's WETH allowance. Because the router is `payable` and accumulates ETH from prior users who omit `refundETH()`, a subsequent WETH swap silently consumes that stranded ETH, permanently stealing it from its rightful owner.

### Finding Description

`PeripheryPayments.pay` handles WETH payments with three branches: [1](#0-0) 

The middle branch (`nativeBalance > 0 && nativeBalance < value`) wraps all native ETH currently held by the router and sends it to the pool, then pulls only `value - nativeBalance` from the payer's WETH allowance. It does not verify that the native ETH belongs to the current caller.

The router is `payable` at multiple entry points: [2](#0-1) [3](#0-2) 

A user who calls `multicall{value: 1 ether}(exactInputSingle(amountIn=0.5 ether))` without appending `refundETH()` leaves 0.5 ETH stranded in the router. The next user who calls `exactInputSingle(tokenIn=WETH, amountIn=1 ether)` triggers `pay(WETH, user2, pool, 1 ether)`, which sees `nativeBalance=0.5 ETH`, wraps it, and pulls only 0.5 WETH from user2. The pool receives the correct 1 ETH, but user1's 0.5 ETH is permanently consumed without their consent.

The `receive()` guard only blocks direct ETH pushes; it does not prevent ETH from accumulating via `msg.value` on `payable` entry points: [4](#0-3) 

### Impact Explanation

- **User A** (prior depositor): loses their stranded ETH permanently — it is wrapped and forwarded to the pool on behalf of User B's swap.
- **User B** (attacker or innocent next swapper): pays less WETH than the pool actually received, getting a discount funded by User A.
- The pool itself is not insolvent (it receives the correct amount), but User A suffers a direct, irreversible loss of principal with no recourse.

This meets the High impact threshold: direct loss of user principal through a public, unprivileged swap path.

### Likelihood Explanation

The pattern of `multicall{value}(swap)` without `refundETH()` is a common integration mistake, explicitly anticipated by the test suite: [5](#0-4) 

Any user who sends excess ETH and omits `refundETH()` — or whose transaction reverts after the ETH is already in the router — creates the vulnerable state. An attacker can monitor the mempool or simply be the next WETH swapper.

### Recommendation

In the partial-ETH branch, only consume native ETH that was sent by the **current caller in the current transaction** (i.e., `msg.value`). One approach: track `msg.value` in transient storage at the entry point and cap the native ETH consumed in `pay` to that amount, reverting or ignoring any residual balance from prior transactions. Alternatively, revert if `address(this).balance > msg.value` at entry, ensuring no cross-user ETH leakage is possible.

### Proof of Concept

```
1. User A: router.multicall{value: 0.3 ether}([exactInputSingle(tokenIn=WETH, amountIn=0.3 ether)])
   → pay() sees nativeBalance=0.3 ETH >= value=0.3 ETH → wraps all, sends to pool
   → router.balance = 0 ✓ (clean case)

2. User A: router.multicall{value: 1 ether}([exactInputSingle(tokenIn=WETH, amountIn=0.3 ether)])
   → pay() sees nativeBalance=1 ETH >= value=0.3 ETH → wraps 0.3 ETH, sends to pool
   → router.balance = 0.7 ETH (stranded, no refundETH call)

3. User B: router.exactInputSingle(tokenIn=WETH, amountIn=1 ether)
   → pay(WETH, userB, pool, 1 ether)
   → nativeBalance=0.7 ETH > 0, < 1 ETH → middle branch
   → wraps 0.7 ETH (User A's), sends to pool
   → safeTransferFrom(userB, pool, 0.3 WETH)
   → pool receives 1 ETH ✓, but userB paid only 0.3 WETH
   → User A loses 0.7 ETH with no recourse
``` [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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
