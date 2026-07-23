Audit Report

## Title
`PeripheryPayments.pay()` WETH branch consumes unattributed router ETH balance, enabling cross-user ETH theft - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
The `pay()` internal function's WETH branch reads `address(this).balance` — the router's aggregate native ETH balance — rather than the current caller's `msg.value` contribution. Any ETH stranded on the router from a prior transaction (e.g., a user who over-sent `msg.value` without calling `refundETH()`) is silently consumed to settle a subsequent user's WETH obligation. The prior user permanently loses their ETH while the subsequent user receives a free or discounted swap. Additionally, the public `refundETH()` function sends the entire router ETH balance to any caller, providing a second direct drain vector.

## Finding Description
In `PeripheryPayments.pay()` (lines 73–84), when `token == WETH` and `payer != address(this)`:

```solidity
uint256 nativeBalance = address(this).balance;   // reads ALL router ETH
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

`address(this).balance` is the router's total ETH, not the current caller's `msg.value`. The `receive()` guard (lines 32–34) only blocks direct ETH transfers from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` on payable entry points. `exactInputSingle` (line 67) is `external payable` and accepts arbitrary `msg.value` with no enforcement that `msg.value == params.amountIn`. When User A calls `exactInputSingle{value: X}` with `amountIn < X` and omits `refundETH()`, the surplus `X - amountIn` ETH remains on the router. The next WETH-input swap by User B will find `nativeBalance >= value` and deposit User A's ETH to settle User B's obligation without pulling any WETH from User B.

The second drain vector is `refundETH()` (lines 58–63), which is `external` and callable by any address, sending the full `address(this).balance` to `msg.sender` — allowing a front-running bot to atomically extract stranded ETH.

## Impact Explanation
Direct loss of user principal — High. User A's stranded ETH is consumed 1:1 to settle User B's pool payment. User A receives nothing in return; their ETH is permanently transferred to the pool as WETH on behalf of User B. The loss is bounded only by how much ETH a user over-sends. This satisfies the "Critical/High direct loss of user principal above Sherlock thresholds" allowed impact gate.

## Likelihood Explanation
Medium. The intended usage pattern (wrap swap + `refundETH()` inside `multicall{value}`) is not enforced on-chain. Users calling `exactInputSingle{value: X}` directly with any excess `msg.value` — a natural pattern for a single-hop ETH-input swap — will strand ETH. A mempool-monitoring bot can atomically follow with a WETH swap or `refundETH()` call to extract the value, making exploitation repeatable and requiring no special privileges.

## Recommendation
Track the current transaction's `msg.value` contribution separately from the router's aggregate balance. Pass the caller-attributed ETH budget explicitly into `pay()` rather than reading `address(this).balance`:

```solidity
function pay(address token, address payer, address recipient, uint256 value, uint256 ethBudget) internal {
    ...
    } else if (token == WETH) {
        uint256 nativeBalance = ethBudget; // only the caller's own ETH
        ...
    }
}
```

Alternatively, restrict `refundETH()` so it only refunds `msg.sender` their own attributed ETH (tracked per-call), or enforce that `address(this).balance == 0` at the start of every non-multicall payable entry point.

## Proof of Concept
```
Setup:
  - Router deployed with WETH and a valid WETH/token1 pool with liquidity.

Step 1 — User A strands ETH:
  vm.prank(userA);
  router.exactInputSingle{value: 2_000}(ExactInputSingleParams({
      tokenIn: WETH, amountIn: 1_000, ...
  }));
  // pay() sees address(this).balance=2_000 >= value=1_000
  // → deposits 1_000 ETH as WETH, sends to pool
  // → 1_000 ETH stranded on router
  assertEq(address(router).balance, 1_000);

Step 2 — User B exploits (0 WETH approved, 0 msg.value):
  vm.prank(userB);
  router.exactInputSingle(ExactInputSingleParams({
      tokenIn: WETH, amountIn: 1_000, ...
  }));
  // pay(WETH, userB, pool, 1_000):
  //   address(this).balance = 1_000 (User A's ETH) >= value = 1_000
  //   → deposits User A's 1_000 ETH as WETH, transfers to pool
  //   → User B's WETH never pulled
  assertEq(address(router).balance, 0);       // User A's ETH consumed
  assertGt(token1.balanceOf(userB), 0);       // User B received swap output at zero cost

Alternative — direct drain via refundETH():
  vm.prank(attacker);
  router.refundETH(); // sends all stranded ETH to attacker
```