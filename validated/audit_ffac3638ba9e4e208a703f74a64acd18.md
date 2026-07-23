Audit Report

## Title
`unwrapWETH9` Missing Zero-Address Check on `recipient` Enables Permanent ETH Burn - (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`unwrapWETH9` accepts a caller-supplied `recipient` with no zero-address guard. When `recipient == address(0)`, the internal `_transferETH` executes `address(0).call{value: value}("")`, which succeeds in the EVM (address(0) has no code; the call returns `true`), causing the `ok` check to pass and permanently burning the unwrapped ETH. Any unprivileged caller can invoke `unwrapWETH9(0, address(0))` to destroy any WETH balance held by the router.

## Finding Description
`unwrapWETH9` at `PeripheryPayments.sol` L37–45 reads the router's full WETH balance, calls `IWETH9(WETH).withdraw(balanceWETH)` to convert it to native ETH, then calls `_transferETH(recipient, balanceWETH)`. There is no `require(recipient != address(0))` guard anywhere in this function or in `_transferETH` (L90–93):

```solidity
function _transferETH(address to, uint256 value) internal {
    (bool ok,) = to.call{value: value}("");
    if (!ok) revert ETHTransferFailed();
}
```

A low-level CALL to `address(0)` with ETH value succeeds unconditionally in the EVM — address(0) has no code, so the call returns `(true, "")`. The `ok` check passes, no revert is triggered, and the ETH is permanently destroyed. The only existing guard is the `amountMinimum` check (`if (balanceWETH < amountMinimum) revert`), which is trivially bypassed by passing `amountMinimum = 0`.

By contrast, `sweepToken` (L48–55) uses `IERC20(token).safeTransfer(recipient, balanceToken)`, which internally reverts on `recipient == address(0)` via OpenZeppelin's `SafeERC20` — creating an asymmetry where ERC20 sweeps are protected but ETH unwraps are not.

The documented and tested ETH-output pattern (confirmed in `MetricOmmSimpleRouter.native.t.sol` L8–10 and L135–162, L202–239) is: swap `tokenX → WETH` with `recipient = address(router)`, then call `unwrapWETH9` in the same `multicall`. When these two steps are not atomic (separate transactions), WETH accumulates on the router and is exposed to this attack.

## Impact Explanation
Direct, permanent loss of user principal. An attacker front-running a user's standalone `unwrapWETH9` call with `unwrapWETH9(0, address(0))` drains the router's entire WETH balance as burned ETH. The user's swap output is completely lost with no recourse. This meets the Critical/High threshold of direct loss of user principal by an unprivileged caller.

## Likelihood Explanation
The attack requires no special privilege — any EOA can call `unwrapWETH9(0, address(0))`. WETH accumulates on the router whenever a user sends a swap with `recipient = address(router)` in a standalone transaction (not wrapped in `multicall`). The test suite confirms this is a supported usage pattern. Additionally, any WETH transferred directly to the router (e.g., airdrop, mistaken transfer) is permanently at risk. Front-running is straightforward on any chain with a public mempool.

## Recommendation
Add a zero-address guard at the top of `unwrapWETH9` (and symmetrically in `sweepToken`):

```solidity
error InvalidRecipient();

function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    if (recipient == address(0)) revert InvalidRecipient();
    ...
}
```

Optionally add the same guard inside `_transferETH` as defense-in-depth.

## Proof of Concept
```
1. Alice calls exactInputSingle(tokenIn=token1, tokenOut=WETH, recipient=address(router))
   → Router now holds X WETH.

2. Bob (attacker) observes Alice's pending unwrapWETH9(X, alice) in the mempool.
   Bob front-runs with: router.unwrapWETH9(0, address(0))

3. Execution of Bob's tx:
   - balanceWETH = X  (> 0, passes amountMinimum=0 check at L39)
   - IWETH9(WETH).withdraw(X)  → router receives X ETH (L42)
   - _transferETH(address(0), X)  (L43)
     → address(0).call{value: X}("") returns (true, "")  (L91)
     → ok == true, no revert  (L92)
   → X ETH permanently burned

4. Alice's unwrapWETH9 call sees balanceWETH == 0, receives nothing.
   Alice loses her entire swap output.
```

Foundry test plan: deploy `MetricOmmSimpleRouter` with mock WETH and pool; mint WETH to router; call `router.unwrapWETH9(0, address(0))` from an unprivileged address; assert `address(0).balance` increased by the WETH amount and router WETH/ETH balances are zero.