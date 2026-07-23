Audit Report

## Title
`pay()` consumes aggregate router ETH balance instead of per-call `msg.value`, enabling stranded ETH theft — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`pay()` reads `address(this).balance` at line 74 of `PeripheryPayments.sol` to determine how much native ETH is available to wrap as WETH. Because the router holds no per-transaction ETH accounting, any ETH left on the router by a prior caller (who sent excess `msg.value` and omitted `refundETH()`) is silently consumed to subsidize a subsequent user's WETH swap. The prior user loses their stranded ETH; the subsequent user receives a proportional discount.

## Finding Description
The vulnerable branch in `pay()` is:

```solidity
// PeripheryPayments.sol L73-84
} else if (token == WETH) {
  uint256 nativeBalance = address(this).balance;   // ← aggregate balance, not msg.value
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
```

All four swap entry points are `payable` and accept arbitrary `msg.value`:
- `exactInputSingle` (L67), `exactInput` (L92), `exactOutputSingle` (L130), `exactOutput` (L154), and `multicall` (L39) in `MetricOmmSimpleRouter.sol`.

When a user sends more ETH than the swap requires, `pay()` wraps only the needed amount and the remainder stays on the router. The `receive()` guard:

```solidity
// PeripheryPayments.sol L32-34
receive() external payable {
  if (msg.sender != WETH) revert NotWETH();
}
```

only blocks bare ETH pushes (no calldata). It does **not** prevent ETH accumulation via `msg.value` in payable function calls. The transient storage in `MetricOmmSwapRouterBase` tracks pool, callback mode, payer, token, and amount — but no per-call ETH budget. `refundETH()` (L58-63) is the intended recovery path but is opt-in and not enforced.

**Exploit flow:**
1. User A calls `exactInputSingle{value: 1 ETH}(tokenIn=WETH, amountIn=0.5 ETH, ...)`. `pay()` wraps 0.5 ETH; 0.5 ETH remains on the router. User A omits `refundETH()`.
2. Attacker calls `exactInputSingle{value: 0}(tokenIn=WETH, amountIn=1 ETH, ...)` with 0.5 WETH approved. `pay()` reads `nativeBalance = 0.5 ETH > 0`, wraps it, then pulls only 0.5 WETH from the attacker via `safeTransferFrom`. Attacker pays 0.5 WETH for a 1 ETH WETH swap.

**Result:** User A loses 0.5 ETH; attacker receives a 50% discount.

## Impact Explanation
Direct loss of user principal. The stranded ETH (up to 100% of the excess `msg.value`) is consumed by an unprivileged subsequent caller. This satisfies the contest's direct-loss-of-principal gate at Medium/High severity.

## Likelihood Explanation
**Medium.** Two realistic triggers:
1. **Accidental:** Any user who calls a payable entry point with excess ETH and omits `refundETH()` from their multicall leaves funds at risk in the next block.
2. **Adversarial front-run:** An attacker observing a pending `refundETH()` in the mempool front-runs it with a WETH swap sized to the stranded amount, consuming the victim's ETH while paying less WETH. Both paths require no privileges beyond a WETH approval on the router.

## Recommendation
Track the ETH budget for the current transaction using transient storage. At the start of each payable entry point, store `msg.value` in a transient slot. Inside `pay()`, replace `address(this).balance` with the transient per-call budget and decrement it as ETH is consumed. This ensures only ETH explicitly sent in the current call can be used to wrap WETH, regardless of the router's aggregate balance.

## Proof of Concept
```
// Foundry test sketch
function test_strandedEthSubsidy() public {
    // 1. User A swaps 0.5 WETH, sends 1 ETH
    vm.prank(userA);
    router.exactInputSingle{value: 1 ether}(
        ExactInputSingleParams({tokenIn: WETH, amountIn: 0.5 ether, ...})
    );
    // userA omits refundETH(); 0.5 ETH stranded on router
    assertEq(address(router).balance, 0.5 ether);

    // 2. Attacker swaps 1 WETH with 0 msg.value, only 0.5 WETH approved
    deal(WETH, attacker, 0.5 ether);
    IERC20(WETH).approve(address(router), 0.5 ether);
    vm.prank(attacker);
    router.exactInputSingle{value: 0}(
        ExactInputSingleParams({tokenIn: WETH, amountIn: 1 ether, ...})
    );
    // Attacker paid only 0.5 WETH; router consumed userA's 0.5 ETH
    assertEq(address(router).balance, 0);
    // userA's 0.5 ETH is gone
}
```

The corrupted value is `nativeBalance` at line 74 of `PeripheryPayments.sol`: it reads the router's global ETH balance instead of the ETH budget belonging to the current caller.