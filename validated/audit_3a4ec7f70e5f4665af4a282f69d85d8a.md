The `pay` function in `PeripheryPayments.sol` is the critical code to analyze. Let me read the full file and the multicall flow to confirm the vulnerability path.

The vulnerability is real and confirmed. Here is the full finding:

---

### Title
Stranded ETH from prior user consumed as WETH payment for subsequent user — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`PeripheryPayments.pay` uses the router's entire native ETH balance (`address(this).balance`) when settling a WETH payment. Because ETH sent as `msg.value` in payable calls is never tracked per-user, any ETH left in the router from a prior transaction is silently consumed as part of a later user's WETH swap input, effectively stealing the prior user's ETH.

### Finding Description

The hybrid WETH branch of `pay` reads the router's global native balance: [1](#0-0) 

When `0 < nativeBalance < value`, it wraps all available native ETH, transfers it to the pool, then pulls only the shortfall from `payer` via `safeTransferFrom`. The balance check at line 74 is against `address(this).balance` — the entire router balance — not the ETH contributed by the current caller.

ETH can be stranded in the router in two ways, both without any special permissions:

1. **Over-send with no `refundETH`**: User A calls `exactInputSingle{value: 1 ETH}(tokenIn=WETH, amountIn=0.5 ETH)`. The callback wraps only 0.5 ETH; the remaining 0.5 ETH stays in the router. [2](#0-1) 

2. **ETH sent for a non-WETH swap**: User A calls `multicall{value: 0.5 ETH}([exactInputSingle(tokenIn=token1, ...)])`. The swap consumes token1, not ETH; the 0.5 ETH is never touched and remains in the router. [3](#0-2) 

The `receive()` guard only blocks direct bare ETH transfers; it does not prevent ETH from accumulating via `msg.value` in payable function calls. [4](#0-3) 

Once stranded, User B calls `exactInputSingle(tokenIn=WETH, amountIn=1 ETH)`. In the callback, `pay(WETH, userB, pool, 1e18)` fires. With `nativeBalance = 0.5 ETH`:
- 0.5 ETH (User A's) is wrapped and sent to the pool
- Only 0.5 WETH is pulled from User B instead of 1 WETH

User A's ETH is permanently lost; User B receives a 0.5 ETH subsidy. [5](#0-4) 

### Impact Explanation

Direct loss of user principal. User A's ETH is consumed by User B's swap with no recourse. The pool receives the correct total WETH, so pool accounting is unaffected, but User A's ETH is gone and User B pays less than owed. Severity: **High**.

### Likelihood Explanation

Any user who sends ETH via a payable router function without including `refundETH` in the same multicall, or who sends ETH for a non-WETH swap, creates the precondition. A griever or MEV bot can monitor the mempool for such transactions and immediately follow with a WETH swap to claim the stranded ETH. No privileged access is required.

### Recommendation

Track only the ETH contributed by the current call, not the global balance. One approach: snapshot `address(this).balance` before the swap entry and pass the delta as the available native amount, or require that `msg.value` exactly equals the ETH portion of the payment and revert otherwise. Alternatively, enforce that any ETH sent to a non-WETH swap is zero, and that WETH swaps consume exactly `msg.value` (reverting if `msg.value > amountIn`).

### Proof of Concept

```
// Step 1: User A strands 0.5 ETH
router.exactInputSingle{value: 0.5 ether}(ExactInputSingleParams({
    tokenIn: address(token1),   // NOT WETH — ETH is never consumed
    amountIn: 1000,
    ...
}));
// router.balance == 0.5 ether

// Step 2: User B exploits it
// User B has approved 0.5 WETH (not 1 WETH) to the router
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: address(weth),
    amountIn: 1 ether,
    ...
}));
// pay(WETH, userB, pool, 1e18):
//   nativeBalance = 0.5 ether  →  wraps 0.5 ETH (User A's), pulls 0.5 WETH from User B
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
