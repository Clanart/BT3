Audit Report

## Title
Stranded ETH from prior user consumed as WETH payment for subsequent user — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay` resolves WETH payments by reading `address(this).balance` — the router's entire native ETH balance — rather than only the ETH contributed by the current caller. Any ETH left in the router from a prior transaction (via over-send or a non-WETH payable call) is silently consumed as part of a later user's WETH swap input, causing direct loss of the prior user's ETH.

## Finding Description

The WETH branch of `pay` at [1](#0-0)  reads `address(this).balance` globally. When `0 < nativeBalance < value`, it wraps all available native ETH, transfers it to the pool, then pulls only the shortfall from `payer` via `safeTransferFrom`. There is no mechanism to distinguish ETH belonging to the current caller from ETH left by a prior caller.

ETH can be stranded in the router without any special permissions via two paths:

1. **Over-send with no `refundETH`**: A user calls `exactInputSingle{value: 1 ETH}` with `tokenIn=WETH, amountIn=0.5 ETH`. The callback wraps only 0.5 ETH; the remaining 0.5 ETH stays in the router.
2. **ETH sent for a non-WETH swap**: A user calls `multicall{value: 0.5 ETH}([exactInputSingle(tokenIn=token1, ...)])`. The swap consumes `token1`, not ETH; the 0.5 ETH is never touched.

The `receive()` guard at [2](#0-1)  only blocks direct bare ETH transfers from non-WETH addresses. It does not prevent ETH from accumulating via `msg.value` in payable function calls such as `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, or `multicall` — all of which are `payable`.

Once stranded, a subsequent user calling `exactInputSingle(tokenIn=WETH, amountIn=1 ETH)` triggers `pay(WETH, userB, pool, 1e18)`. With `nativeBalance = 0.5 ETH`, the router wraps and sends User A's 0.5 ETH to the pool, then pulls only 0.5 WETH from User B instead of 1 WETH. User A's ETH is permanently lost; User B receives a 0.5 ETH subsidy. The `multicall` implementation at [3](#0-2)  uses `functionDelegateCall` and is `payable`, making it a direct vector for stranding ETH across calls.

## Impact Explanation

Direct loss of user principal. User A's ETH is consumed by User B's swap with no recourse. The pool receives the correct total WETH so pool accounting is unaffected, but User A's ETH is permanently gone and User B pays less than owed. This meets the High severity threshold for direct loss of user principal.

## Likelihood Explanation

No privileged access is required. Any user who sends ETH via a payable router function without including `refundETH` in the same multicall, or who sends ETH for a non-WETH swap, creates the precondition. A griever or MEV bot can monitor the mempool for such transactions and immediately follow with a WETH swap to claim the stranded ETH. The condition is repeatable and requires no special setup.

## Recommendation

Track only the ETH contributed by the current call, not the global balance. Concretely: snapshot `address(this).balance` before the swap entry point and pass the delta as the available native amount into `pay`, or require that `msg.value` exactly equals the ETH portion of the payment and revert if `msg.value > amountIn`. Additionally, enforce that any ETH sent to a non-WETH swap is zero (revert if `msg.value > 0 && tokenIn != WETH`).

## Proof of Concept

```solidity
// Step 1: User A strands 0.5 ETH (non-WETH swap with ETH attached)
router.exactInputSingle{value: 0.5 ether}(ExactInputSingleParams({
    tokenIn: address(token1),   // NOT WETH — ETH is never consumed
    amountIn: 1000,
    ...
}));
// router.balance == 0.5 ether

// Step 2: User B exploits it (only approves 0.5 WETH, not 1 WETH)
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: address(weth),
    amountIn: 1 ether,
    ...
}));
// pay(WETH, userB, pool, 1e18):
//   nativeBalance = 0.5 ether → wraps 0.5 ETH (User A's), pulls 0.5 WETH from User B
// User A loses 0.5 ETH; User B pays 0.5 WETH instead of 1 WETH
assert(address(router).balance == 0);
assert(userA_eth_lost == 0.5 ether);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```
