The code confirms the claim. Let me verify the key details:

**`PeripheryPayments.sol` L74** — `nativeBalance = address(this).balance` uses the entire router balance, not the current caller's `msg.value`. [1](#0-0) 

**`PeripheryPayments.sol` L32-34** — `receive()` only blocks direct ETH pushes from non-WETH addresses; it does NOT prevent ETH from accumulating via `msg.value` on payable entry points. [2](#0-1) 

**`MetricOmmSimpleRouter.sol` L67** — `exactInputSingle` is `payable`, so `msg.value > amountIn` leaves surplus ETH in the router with no on-chain enforcement of a trailing `refundETH()`. [3](#0-2) 

The exploit path is fully reachable and the code matches the claim exactly.

---

Audit Report

## Title
Stranded ETH in Router Consumed by Subsequent WETH Swap Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` uses `address(this).balance` — the router's entire native ETH balance — when deciding how much ETH to wrap into WETH, with no check that the ETH was deposited by the current caller. Any ETH left in the router from a prior transaction (e.g., from a user who sent `msg.value > amountIn` and omitted `refundETH()`) is silently consumed to fund a subsequent caller's swap, resulting in direct loss of the prior user's ETH.

## Finding Description
In `PeripheryPayments.pay()` (L69–88), when `token == WETH` and `payer != address(this)`, the function reads `nativeBalance = address(this).balance` (L74) — the router's total ETH — and uses it to wrap WETH before pulling any remainder from the payer via `transferFrom`. The three branches are:

- `nativeBalance >= value` → wrap entirely from router balance, no `transferFrom` from payer (L75–77)
- `nativeBalance > 0` → wrap partial from router, pull remainder from payer (L78–81)
- `nativeBalance == 0` → pull all from payer (L82–83)

The router's payable entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) all accept `msg.value`. The `receive()` guard (L32–34) only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` on these payable functions. A user who sends `msg.value > amountIn` and omits a trailing `refundETH()` leaves the surplus permanently in the router until consumed. The next caller whose swap triggers `pay(WETH, bob, pool, value)` will have their payment partially or fully funded by the stranded ETH, with no `transferFrom` deducted from them for that portion.

## Impact Explanation
Direct loss of user principal. Alice's surplus ETH is consumed to fund Bob's swap with no compensation. Bob receives a free (or discounted) swap; Alice's ETH is unrecoverable. This is a permissionless theft of user funds — Bob requires no approvals, no special role, and no coordination with Alice. Severity: **High**.

## Likelihood Explanation
The precondition — ETH stranded in the router — is realistic and common:
1. Users calling `exactInputSingle{value: X}` with `X > amountIn` and no `refundETH()` afterward.
2. `multicall` batches that include a WETH swap but omit the trailing `refundETH()` call.

The router provides no on-chain enforcement that `refundETH()` is called. A griefing bot can monitor the mempool for transactions that leave ETH in the router and immediately follow with a zero-value WETH swap to consume the stranded ETH.

## Recommendation
Track only the ETH deposited by the current caller. Pass `msg.value` as a parameter through to `pay()` and use it instead of `address(this).balance`:

```solidity
function pay(address token, address payer, address recipient, uint256 value, uint256 msgValue) internal {
    ...
    } else if (token == WETH) {
        uint256 nativeBalance = msgValue; // only current caller's ETH
        ...
    }
}
```

Alternatively, at the start of every payable entry point, assert `address(this).balance == msg.value` and revert if the router holds pre-existing ETH, preventing any accumulation of stranded funds.

## Proof of Concept
```solidity
function test_strandedEthStolenByNextSwapper() public {
    // Alice sends 1 ETH but only needs 0.9 ETH for her swap
    vm.prank(alice);
    router.exactInputSingle{value: 1 ether}(
        ExactInputSingleParams({ pool: pool, tokenIn: WETH, amountIn: 0.9 ether, ... })
    );
    // Alice forgets refundETH(); 0.1 ETH is now stranded in router
    assertEq(address(router).balance, 0.1 ether);

    // Bob swaps with value=0, amountIn=0.1 ETH (tokenIn=WETH)
    // pay() sees nativeBalance=0.1 ETH >= value=0.1 ETH → wraps Alice's ETH, no transferFrom on Bob
    vm.prank(bob);
    router.exactInputSingle{value: 0}(
        ExactInputSingleParams({ pool: pool, tokenIn: WETH, amountIn: 0.1 ether, ... })
    );

    // Bob received output without paying; Alice's 0.1 ETH is gone
    assertEq(address(router).balance, 0);
}
```

The `pay()` call at L75–77 wraps `address(this).balance` without verifying it was deposited by the current caller, consuming Alice's stranded ETH to fund Bob's swap.

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
