Audit Report

## Title
`unwrapWETH9` Missing Zero-Address Check on `recipient` Allows Permanent ETH Burn - (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`unwrapWETH9` accepts a caller-supplied `recipient` address with no zero-address guard. When `recipient == address(0)`, the internal `_transferETH` issues a low-level call to `address(0)` which succeeds in the EVM (address(0) has no code; the call returns `true`), permanently burning the unwrapped ETH. Any unprivileged caller can invoke `unwrapWETH9(0, address(0))` to drain and burn all WETH held by the router.

## Finding Description
`unwrapWETH9` reads the router's full WETH balance, calls `IWETH9(WETH).withdraw(balanceWETH)` to convert it to native ETH, then calls `_transferETH(recipient, balanceWETH)`: [1](#0-0) 

`_transferETH` uses a bare low-level call with no zero-address guard: [2](#0-1) 

When `to == address(0)`, the EVM executes `CALL` to address 0, which has no code. The call returns `(true, "")`, `ok == true`, no revert is triggered, and the ETH is permanently destroyed. The only existing guard is `if (balanceWETH < amountMinimum)`, which is trivially bypassed by passing `amountMinimum = 0`.

By contrast, `sweepToken` uses OpenZeppelin's `safeTransfer`, which internally validates `to != address(0)` and would revert — creating an asymmetry where WETH unwrap silently burns while ERC-20 sweep reverts: [3](#0-2) 

## Impact Explanation
Direct loss of user principal. The documented and tested ETH-output pattern routes WETH to the router (`recipient: address(router)`) then calls `unwrapWETH9` to receive native ETH: [4](#0-3) 

When these two steps are not atomic (i.e., not wrapped in a single `multicall`), an attacker can front-run the `unwrapWETH9` call with `unwrapWETH9(0, address(0))`, burning the victim's entire swap output. Additionally, any WETH sent directly to the router is permanently at risk. The loss is irreversible with no recourse.

## Likelihood Explanation
The attack requires no special privilege — any EOA can call `unwrapWETH9(0, address(0))`. The multicall atomic pattern is safe, but standalone non-atomic usage (a documented and tested pattern) creates a front-running window. Any WETH stranded on the router between transactions is permanently vulnerable. The attack is repeatable and costs only gas.

## Recommendation
Add a zero-address guard at the top of `unwrapWETH9`:

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    if (recipient == address(0)) revert InvalidRecipient();
    ...
}
```

Apply the same guard to `sweepToken` for consistency, and optionally add a defense-in-depth check inside `_transferETH`.

## Proof of Concept

```
1. Alice calls exactInputSingle(tokenIn=token1, tokenOut=WETH, recipient=address(router))
   → Router now holds X WETH.

2. Bob (attacker) front-runs Alice's pending unwrapWETH9(X, alice) with:
   router.unwrapWETH9(0, address(0))

3. Bob's tx executes:
   - balanceWETH = X  (passes amountMinimum=0 check)
   - IWETH9(WETH).withdraw(X)  → router receives X ETH (receive() allows WETH sender)
   - _transferETH(address(0), X)
     → address(0).call{value: X}("") returns (true, "")
     → ok == true, no revert
   → X ETH permanently burned

4. Alice's unwrapWETH9 executes:
   - balanceWETH = 0  (passes amountMinimum check if amountMinimum=0)
   - balanceWETH > 0 is false, nothing sent
   → Alice receives nothing; full swap output is lost
```

Foundry test plan: deploy router with mock WETH, transfer WETH to router, call `router.unwrapWETH9(0, address(0))` from an unprivileged address, assert `address(0).balance` increased by the WETH amount and router WETH/ETH balances are zero.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-45)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L52-54)
```text
    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L90-93)
```text
  function _transferETH(address to, uint256 value) internal {
    (bool ok,) = to.call{value: value}("");
    if (!ok) revert ETHTransferFailed();
  }
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L135-162)
```text
  function test_multicall_tokenForWeth_thenUnwrapEth() public {
    uint128 amountIn = 3_000;
    uint256 recipientEthBefore = recipient.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token1),
        tokenOut: address(weth),
        zeroForOne: false,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: address(router),
        deadline: _deadline(),
        priceLimitX64: type(uint128).max,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.unwrapWETH9.selector, uint256(0), recipient);
    router.multicall(calls);

    assertGt(recipient.balance, recipientEthBefore, "recipient received eth");
    assertEq(weth.balanceOf(address(router)), 0, "router weth cleared");
    assertEq(address(router).balance, 0, "router eth cleared");
  }
```
