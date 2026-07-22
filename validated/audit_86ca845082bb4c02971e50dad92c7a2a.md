### Title
Router `pay()` Consumes Stranded Native ETH Without Ownership Attribution, Enabling Residue Theft - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

### Summary
The `pay()` function in `PeripheryPayments.sol` uses the router's entire native ETH balance (`address(this).balance`) to fund WETH payments without verifying that the ETH belongs to the current payer. Any ETH stranded on the router from a prior user's `multicall{value: X}()` call can be silently consumed by the next WETH swap, causing direct loss of the prior user's principal.

### Finding Description
When `pay()` is invoked with `token == WETH` and `payer != address(this)`, it reads the router's native ETH balance and uses it to fund the WETH payment before pulling from the payer's WETH allowance:

```solidity
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
}
``` [1](#0-0) 

The check `nativeBalance > 0` is the direct analog of the external report's `postSwapBalance > preSwapBalance`: it accepts any positive ETH balance and consumes it without attribution. There is no guard that tracks which user deposited the ETH or limits consumption to the current transaction's `msg.value`.

ETH arrives on the router legitimately via `multicall{value: X}()` — a documented pattern for native-ETH WETH swaps. If the user sends more ETH than the swap consumes (e.g., to ensure the swap succeeds) and omits `refundETH()` from the multicall, the surplus is stranded. The `receive()` guard only blocks direct `.call{value}` transfers from non-WETH addresses; it does not prevent ETH from arriving as `msg.value` in a `payable` multicall. [2](#0-1) [3](#0-2) 

Once ETH is stranded, any subsequent caller who executes a WETH swap through the router will have their payment funded — fully or partially — by the stranded ETH. The stranded ETH is deposited as WETH and transferred to the pool on behalf of the new caller, reducing or eliminating the new caller's WETH pull from their own balance.

### Impact Explanation
**Direct loss of user principal.** User A's stranded ETH is irreversibly consumed by User B's swap. User A cannot recover it via `refundETH()` after the fact because the ETH has already been deposited as WETH and transferred to the pool. The loss equals `min(stranded_ETH, swap_value)` per consuming swap. A single stranded deposit of 1 ETH can be drained across multiple subsequent WETH swaps.

### Likelihood Explanation
The stranded-ETH precondition is reachable through normal usage. The documented pattern for native-ETH input is `multicall{value: X}([exactInputSingle(WETH, amountIn), refundETH()])`. Omitting `refundETH()` — or sending excess ETH to ensure the swap succeeds — strands the surplus. Once stranded, any unprivileged caller who executes a WETH swap triggers the consumption. No special permissions, malicious setup, or non-standard tokens are required.

### Recommendation
Track the ETH balance attributable to the current transaction in transient storage (e.g., store `msg.value` at multicall entry and decrement it as ETH is consumed in `pay()`). In `pay()`, limit native ETH consumption to the per-transaction budget rather than `address(this).balance`. Alternatively, require that `pay()` only uses native ETH when `payer == address(this)` (i.e., mid-path hops), and always pull WETH from the external payer directly for the first hop.

### Proof of Concept

```
// Step 1: User A strands ETH on the router
// User A sends 1 ETH but only swaps 1000 wei, omitting refundETH()
router.multicall{value: 1 ether}([
    abi.encodeCall(router.exactInputSingle, ExactInputSingleParams({
        pool: pool,
        tokenIn: WETH,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1000,          // only 1000 wei consumed
        amountOutMinimum: 0,
        recipient: userA,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }))
    // no refundETH() — 1 ETH - 1000 wei stranded on router
]);

// Step 2: User B calls a WETH swap with msg.value = 0
// pay() sees address(this).balance = ~1 ETH, uses it to fund User B's swap
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: WETH,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 500,               // funded entirely by User A's stranded ETH
    amountOutMinimum: 0,
    recipient: userB,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// User B receives token1 without spending any WETH from their own balance.
// User A's stranded ETH is reduced by 500 wei; they cannot recover it.
```

The `pay()` branch at line 75–77 deposits exactly `value` wei of the router's native ETH as WETH and transfers it to the pool, consuming User A's stranded ETH to settle User B's obligation. [4](#0-3)

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
