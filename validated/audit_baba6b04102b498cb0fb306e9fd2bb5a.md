I need to verify the key aspects of this claim, particularly whether ETH can actually strand on the router and whether the `receive()` guard prevents it.

Audit Report

## Title
Stranded ETH on Router Consumed by Subsequent User's WETH Payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay` reads `address(this).balance` as a global, unaccounted ETH pool when settling WETH payments. Because `exactInputSingle` (and other `payable` swap functions) do not automatically refund excess `msg.value`, any ETH left on the router from a prior user is silently consumed to subsidize a subsequent user's WETH swap, causing irreversible loss of the first user's ETH.

## Finding Description
`exactInputSingle` is `payable` and stores no per-call ETH budget. When a user sends `msg.value > amountIn` without appending `refundETH()` in a multicall, the surplus ETH remains on the router after the call completes. The protocol's own test suite documents this pattern explicitly: excess ETH is only recovered if the caller includes `refundETH()` as a second multicall leg.

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) does not prevent ETH from arriving via a `payable` function call — it only blocks plain ETH transfers with no calldata. ETH sent alongside `exactInputSingle{value: X}(...)` is accepted unconditionally.

When the next user calls any WETH `exactInput*` function, `pay()` executes:

```solidity
uint256 nativeBalance = address(this).balance;   // global, not per-user
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
}
```

The `nativeBalance > 0 && nativeBalance < value` branch wraps and forwards the stranded ETH to the pool, then pulls only the shortfall from the legitimate payer via `transferFrom`. The stranded ETH is irreversibly transferred to the pool to settle a different user's swap.

Concrete exploit path:
1. User A calls `exactInputSingle{value: 2 ETH}(amountIn=1 ETH, tokenIn=WETH)` directly (no multicall, no `refundETH()`). 1 ETH remains on the router.
2. User B calls `exactInputSingle(amountIn=1.5 ETH, tokenIn=WETH)` with no ETH sent, relying on WETH allowance.
3. In the swap callback, `pay(WETH, userB, pool, 1.5 ETH)` is invoked. `nativeBalance = 1 ETH`.
4. Router wraps User A's 1 ETH → transfers 1 ETH WETH to pool.
5. Router pulls only 0.5 ETH WETH from User B via `transferFrom`.
6. User B receives a full 1.5 ETH swap paying only 0.5 ETH. User A's 1 ETH is permanently lost.

No existing guard prevents this: the transient callback context stores User B as `payer`, but the ETH consumed belongs to User A. There is no per-call ETH budget tracking.

## Impact Explanation
Direct, irreversible loss of User A's ETH principal. The router holds no record of whose ETH it holds, so there is no recovery path. User B receives a subsidized trade. This is an unauthorized consumption of another user's funds meeting the High severity threshold (direct loss of user principal above Sherlock thresholds).

## Likelihood Explanation
- The protocol's own tests confirm that excess ETH is only refunded if `refundETH()` is explicitly appended to a multicall. Users calling `exactInputSingle` directly (not via multicall) with `msg.value > amountIn` will always leave residual ETH.
- No special permissions, malicious pool setup, or privileged access required — only two sequential public router calls.
- An MEV bot can monitor the router's ETH balance and immediately follow with a WETH swap to capture the subsidy, making this reliably exploitable.

## Recommendation
Track per-call ETH entitlement rather than using the global balance. Record `msg.value` at the top-level call entry (e.g., in transient storage) and deduct from it as ETH is consumed in `pay`, reverting if the per-call budget is exceeded. Alternatively, pass the available ETH budget explicitly through the call stack so `pay` can only use ETH that arrived in the current top-level call.

## Proof of Concept
```solidity
function test_strandedEthSubsidizesOtherUser() public {
    // User A sends 2 ETH but swaps only 1 ETH worth of WETH, no refundETH
    vm.prank(userA);
    router.exactInputSingle{value: 2 ether}(
        ExactInputSingleParams({tokenIn: WETH, amountIn: 1 ether, ...})
    );
    // Router now holds 1 ETH (user A's unrefunded ETH)
    assertEq(address(router).balance, 1 ether);

    uint256 userBWethBefore = weth.balanceOf(userB);
    // User B swaps 1.5 ETH worth of WETH, sends no ETH
    vm.prank(userB);
    router.exactInputSingle(
        ExactInputSingleParams({tokenIn: WETH, amountIn: 1.5 ether, ...})
    );

    // User B's WETH allowance was only reduced by 0.5 ETH (not 1.5 ETH)
    assertEq(userBWethBefore - weth.balanceOf(userB), 0.5 ether);
    // User A's 1 ETH is gone, router is empty
    assertEq(address(router).balance, 0);
}
```