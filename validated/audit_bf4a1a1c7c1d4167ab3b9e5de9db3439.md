Audit Report

## Title
Router `pay()` consumes total `address(this).balance` for WETH-input swaps, enabling theft of stranded ETH — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay` function in `PeripheryPayments.sol` uses `address(this).balance` — the router's total native ETH balance — when settling WETH-leg payments, rather than scoping to the current transaction's `msg.value`. Any ETH left on the router from a prior user's excess `msg.value` is silently consumed to fund a subsequent caller's WETH swap. Additionally, `refundETH()` sends the entire ETH balance to any caller with no ownership attribution, enabling direct theft of stranded ETH.

## Finding Description
In `pay()` at lines 73–84 of `PeripheryPayments.sol`, when `token == WETH`, the function reads `address(this).balance` as `nativeBalance` and uses it to deposit ETH as WETH without pulling any tokens from `payer` if the balance is sufficient:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // total router ETH, not msg.value
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        ...
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

ETH accumulates on the router when a user calls any payable entry point (e.g., `exactInputSingle`) with `msg.value` exceeding `amountIn` and does not include a `refundETH()` call in the same multicall. The `receive()` guard at lines 32–34 only blocks direct ETH pushes from non-WETH addresses; it does not prevent excess `msg.value` from sitting on the router after a swap completes.

The call path is: `exactInputSingle` (line 67) → pool `swap` → `metricOmmSwapCallback` (line 46) → `_justPayCallback` (line 192) → `pay()` (line 69). The `_getPayer()` stored in transient context is irrelevant when `address(this).balance >= value`, because `pay()` never calls `safeTransferFrom` in that branch.

`refundETH()` at lines 58–63 compounds the issue by sending the entire ETH balance to `msg.sender` with no check that the caller was the original depositor:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

## Impact Explanation
Two concrete loss paths exist, both resulting in direct, unrecoverable loss of victim's principal ETH:

**Path A — free WETH swap:** Attacker calls `exactInputSingle{value: 0}(tokenIn=WETH, amountIn=S)` where `S` equals the stranded ETH. `pay()` sees `address(this).balance == S >= S`, deposits the stranded ETH as WETH, and forwards it to the pool. Attacker receives token-out without spending any ETH or WETH.

**Path B — direct ETH theft:** Attacker calls `refundETH()` in a standalone transaction. The entire stranded ETH balance is transferred to the attacker.

This is a direct loss of user principal ETH, meeting the Critical/High threshold under the allowed impact gate.

## Likelihood Explanation
ETH is stranded whenever a user calls a payable router function with `msg.value > amountIn` and omits `refundETH()` in the same multicall. This is a realistic user error: direct (non-multicall) calls to `exactInputSingle` or `exactOutputSingle` with a safety buffer of ETH leave residue. The attack requires no special privilege, no oracle manipulation, and no pool state precondition — only a monitoring bot watching the router's ETH balance. Both attack paths are executable by any unprivileged EOA.

## Recommendation
1. **Track `msg.value` per entry point in transient storage.** At the start of each payable public function, record `msg.value` in a transient slot. In `pay()`, use `min(transientMsgValue, value)` as the native contribution rather than `address(this).balance`.
2. **Alternatively, auto-refund excess ETH at the end of every non-multicall payable entry point**, so no ETH can survive past the transaction boundary.
3. **Add a caller-binding check to `refundETH()`.** Record the original `msg.sender` in transient storage at multicall entry and restrict `refundETH()` to that address, or document clearly that it is intentionally open and that users must always pair it in the same multicall.

## Proof of Concept
```solidity
// Step 1 — victim overpays and omits refundETH()
vm.deal(victim, 2 ether);
vm.prank(victim);
router.exactInputSingle{value: 2 ether}(ExactInputSingleParams({
    pool: address(pool), tokenIn: address(weth), tokenOut: address(token1),
    zeroForOne: true, amountIn: 1 ether, amountOutMinimum: 0,
    recipient: victim, deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
}));
// 1 ether is now stranded on the router

// Path A — attacker gets free swap
vm.prank(attacker);
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool: address(pool), tokenIn: address(weth), tokenOut: address(token1),
    zeroForOne: true, amountIn: 1 ether, amountOutMinimum: 0,
    recipient: attacker, deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
}));
// attacker receives token1; paid 0 ETH and 0 WETH

// OR Path B — direct ETH theft
vm.prank(attacker);
router.refundETH();
// attacker receives 1 ether
```