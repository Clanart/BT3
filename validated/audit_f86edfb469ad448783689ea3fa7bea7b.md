Audit Report

## Title
Residual ETH in Router Consumed by Subsequent WETH Swap via Hybrid `pay` Branch — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay` function's hybrid WETH branch reads `address(this).balance` globally without scoping to the current transaction's `msg.value`. ETH left in the router from a prior user's `multicall` (where `refundETH()` was omitted) can be silently consumed by a later caller's WETH swap, causing direct, unrecoverable ETH loss for the prior user.

## Finding Description
`PeripheryPayments.pay` at lines 78–81 contains the hybrid branch:

```solidity
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
}
``` [1](#0-0) 

When triggered, it deposits **all** of `address(this).balance` as WETH and sends it to the pool, then pulls only the shortfall from `payer`. The balance is not scoped to the current transaction.

ETH accumulates legitimately because:
1. `multicall` is `payable` and forwards `msg.value` to the contract balance via `functionDelegateCall`. [2](#0-1) 
2. `refundETH()` is a separate, optional step — there is no automatic refund. [3](#0-2) 
3. `receive()` only blocks direct ETH sends from non-WETH addresses; it does not prevent `msg.value` accumulation from payable calls. [4](#0-3) 

**Exploit path:**
- **Tx 1 (User A):** Calls `multicall{value: X+Y}(...)`. Swap consumes `X` ETH. User A omits `refundETH()`. `Y` ETH remains in the router.
- **Tx 2 (User B / attacker):** Calls `exactInputSingle` with `tokenIn=WETH, amountIn=Z` where `Y < Z`. The swap callback triggers `pay(WETH, userB, pool, Z)`. Since `nativeBalance = Y > 0` and `Y < Z`, the hybrid branch fires: deposits User A's `Y` ETH → transfers `Y` WETH to pool, then pulls only `Z - Y` WETH from User B.
- **Result:** Pool receives `Z` WETH. User B paid only `Z - Y`. User A's `Y` ETH is permanently lost.

No existing guard prevents this: `receive()` does not block `msg.value` accumulation, `refundETH()` is opt-in, and `pay` has no per-transaction ETH accounting. [5](#0-4) 

## Impact Explanation
User A suffers a direct, unrecoverable loss of `Y` ETH — a principal-level fund loss, not dust or fees. User B (or a deliberate attacker monitoring the router's balance) receives a subsidy equal to `Y` ETH on their swap. This constitutes direct loss of user principal above Sherlock thresholds and matches the allowed impact gate: "Critical/High/Medium direct loss of user principal."

## Likelihood Explanation
Stranded ETH in the router is a realistic and common condition. Users routinely send a round-number `msg.value` slightly above `amountIn` to avoid reverts, and multicall bundles that omit `refundETH()` are common in practice. An attacker can monitor the router's ETH balance on-chain and immediately follow any transaction that leaves a non-zero balance, making this repeatable and low-effort to exploit.

## Recommendation
Scope ETH consumption in `pay` to the current transaction's `msg.value` only. Options include:
- Track consumed ETH via a transient storage slot initialized to `msg.value` at each router entry point, and use that tracked value instead of `address(this).balance` in the hybrid branch.
- Automatically call `refundETH()` at the end of every swap entry point (e.g., in `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).
- Require `address(this).balance == 0` at the start of each non-multicall entry point, reverting if residual ETH is present.

## Proof of Concept
```solidity
// Foundry test sketch
function test_residualETHStolenByWETHSwap() public {
    address userA = makeAddr("userA");
    address userB = makeAddr("userB");

    vm.deal(userA, 2 ether);
    deal(weth, userB, 1 ether);
    vm.prank(userB);
    IERC20(weth).approve(address(router), type(uint256).max);

    // Tx 1: userA multicall with 2 ETH, swap only needs 1 ETH, no refundETH step
    vm.prank(userA);
    bytes[] memory calls = new bytes[](1);
    calls[0] = abi.encodeCall(router.exactInputSingle, (...amountIn: 1 ether, tokenIn: weth...));
    router.multicall{value: 2 ether}(calls);
    assertEq(address(router).balance, 1 ether); // 1 ETH stranded

    // Tx 2: userB swaps 1.5 WETH — hybrid branch fires, consuming userA's 1 ETH
    vm.prank(userB);
    router.exactInputSingle(...tokenIn: weth, amountIn: 1.5 ether...);

    // userB only spent 0.5 WETH from wallet; router's 1 ETH (userA's) was consumed
    assertEq(address(router).balance, 0);
    assertEq(IERC20(weth).balanceOf(userB), 0.5 ether); // saved 1 ETH worth
    // userA's 1 ETH is permanently lost
}
```

### Citations

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
