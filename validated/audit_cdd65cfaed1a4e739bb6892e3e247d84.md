Audit Report

## Title
`sweepToken` and `unwrapWETH9` accept arbitrary `recipient` with no caller binding, enabling front-running theft of router-held tokens — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary

`PeripheryPayments.sweepToken` and `PeripheryPayments.unwrapWETH9` are `public payable` functions that drain the router's entire ERC-20 or WETH balance to a caller-supplied `recipient` with no check that `recipient == msg.sender` or that the caller deposited the tokens. Any actor who observes a pending sweep transaction in the mempool can front-run it and redirect the full balance to themselves, causing 100% loss of the victim's swap output.

## Finding Description

Both functions are confirmed in production code at `metric-periphery/contracts/base/PeripheryPayments.sol` lines 37–55:

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(recipient, balanceWETH);   // ← entire balance, any recipient
    }
}

function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) {
        IERC20(token).safeTransfer(recipient, balanceToken);  // ← entire balance, any recipient
    }
}
```

The intended safe usage pattern — confirmed by the test suite — is to compose the swap (with `recipient: address(router)`) and the sweep atomically inside a single `multicall`. The test at `metric-periphery/test/MetricOmmSimpleRouter.native.t.sol` lines 135–162 demonstrates this:

```solidity
calls[0] = exactInputSingle(..., recipient: address(router), ...);
calls[1] = unwrapWETH9(0, recipient);
router.multicall(calls);
```

However, `multicall` is not enforced. Both `sweepToken` and `unwrapWETH9` are independently callable as standalone `public` functions. When a user submits the swap and the sweep as two separate transactions, the window between them is exploitable. Neither function contains any guard binding the `recipient` to `msg.sender` or verifying the caller deposited the balance. The router's balance is a shared, unattributed pool — any caller can drain it to any address.

The `refundETH` function, by contrast, correctly hardcodes `msg.sender` as the recipient (line 61), demonstrating the protocol is aware of the pattern but did not apply it to `sweepToken` or `unwrapWETH9`.

## Impact Explanation

A user who routes a swap with `recipient = address(router)` and then calls `unwrapWETH9` or `sweepToken` in a separate transaction loses **100% of their swap output**. The attacker gains the full router balance. This is a direct loss of user principal with no partial recovery, meeting the Critical/High direct-loss threshold under the Metric OMM allowed impact gate.

## Likelihood Explanation

The attack requires only that Alice submit the sweep as a separate transaction rather than inside a `multicall`. This is realistic for: EOA scripts that call each step individually, third-party frontends that do not compose `multicall`, and programmatic integrators. The attacker needs only to monitor the public mempool for calls to `unwrapWETH9` or `sweepToken` on the router and submit the same call with `recipient = attacker` at a higher gas price — a standard MEV operation requiring no special privileges, no malicious setup, and no non-standard tokens.

## Recommendation

Restrict `sweepToken` and `unwrapWETH9` so that `recipient` must equal `msg.sender`, or remove the `recipient` parameter and always send to `msg.sender`:

```solidity
function sweepToken(address token, uint256 amountMinimum) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) {
        IERC20(token).safeTransfer(msg.sender, balanceToken);
    }
}
```

If an arbitrary `recipient` must be supported for contract integrators, add a NatSpec warning that these functions must always be composed inside an atomic `multicall` with the preceding swap, and consider adding a reentrancy-style guard or a per-transaction deposit accounting mechanism.

## Proof of Concept

1. Alice calls `exactInputSingle({tokenIn: tokenA, tokenOut: WETH, recipient: address(router), amountIn: X, ...})` — WETH lands on the router.
2. Alice submits a second transaction: `unwrapWETH9(X, Alice)`.
3. Eve observes Alice's pending `unwrapWETH9` call in the mempool.
4. Eve submits `unwrapWETH9(0, Eve)` with a higher gas price.
5. Eve's transaction executes first: the router's entire WETH balance is unwrapped and sent to Eve.
6. Alice's transaction executes next: `balanceWETH == 0`, `amountMinimum == X`, revert `InsufficientWETH`.
7. Alice loses 100% of her swap output; Eve gains it.

Foundry test plan:
```solidity
function test_frontrun_unwrapWETH9() public {
    // Alice swaps token1 → WETH, output lands on router
    vm.prank(alice);
    router.exactInputSingle(ExactInputSingleParams({..., recipient: address(router), ...}));

    uint256 routerWeth = weth.balanceOf(address(router));
    assertGt(routerWeth, 0);

    // Eve front-runs with amountMinimum=0, recipient=eve
    vm.prank(eve);
    router.unwrapWETH9(0, eve);

    assertEq(eve.balance, routerWeth);   // Eve stole Alice's output
    assertEq(weth.balanceOf(address(router)), 0);

    // Alice's sweep now reverts
    vm.prank(alice);
    vm.expectRevert(PeripheryPayments.InsufficientWETH.selector);
    router.unwrapWETH9(routerWeth, alice);
}
```