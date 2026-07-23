Audit Report

## Title
Unprotected `sweepToken` and `unwrapWETH9` Allow Any Caller to Drain Router-Held Tokens and WETH — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`sweepToken` and `unwrapWETH9` in `PeripheryPayments.sol` are `public payable` with no access control and accept a fully caller-specified `recipient`. Any unprivileged caller can drain the entire router balance of any ERC-20 token or WETH to an arbitrary address. Tokens become stranded on the router whenever a user routes swap output to `address(router)` and issues the subsequent sweep call in a separate transaction rather than atomically via `multicall`.

## Finding Description
Both functions are confirmed in the production code at the cited lines:

`unwrapWETH9` ( [1](#0-0) ) sweeps the entire WETH balance of the contract to a caller-supplied `recipient` with no `msg.sender` check. `sweepToken` ( [2](#0-1) ) does the same for any ERC-20. The `amountMinimum` guard is caller-controlled and provides no protection when set to `0`.

The standard two-step pattern — `exactInputSingle(..., recipient=address(router))` followed by `unwrapWETH9(minAmount, alice)` — is safe only when both calls are batched atomically inside `multicall`. [3](#0-2)  If the user or front-end issues them as separate transactions, WETH is stranded on the router between them. An attacker observing the first transaction can immediately call `unwrapWETH9(0, attacker)` after it confirms, draining the victim's WETH before their second transaction executes.

The same window exists for `sweepToken` with any ERC-20 output token routed through `address(router)` as an intermediate recipient in `exactInput` multi-hop flows. [4](#0-3) 

By contrast, `refundETH` correctly hardcodes `msg.sender` as the recipient, [5](#0-4)  demonstrating that the omission in `sweepToken` and `unwrapWETH9` is inconsistent with the contract's own design intent.

## Impact Explanation
Direct loss of user principal. Any tokens or WETH stranded on the router are immediately claimable by any unprivileged caller who specifies themselves as `recipient`. The stolen amount equals the full router balance of the targeted token at the time of the call, which can be arbitrarily large depending on the victim's swap size. This meets the Sherlock threshold for Medium/High direct loss of user funds.

## Likelihood Explanation
Medium. The attack requires tokens to be stranded on the router first, which occurs when a user or front-end sends a swap with `recipient=address(router)` and the subsequent `sweepToken`/`unwrapWETH9` call in a separate transaction rather than the same `multicall`. Front-end bugs, manual interactions, and integrations that construct two-step router flows non-atomically all make this realistic. The attack itself requires no special privileges and is trivially executable by any on-chain observer.

## Recommendation
Add caller attribution to `sweepToken` and `unwrapWETH9`, matching the behavior of `refundETH`:

```solidity
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    require(recipient == msg.sender, "recipient must be caller");
    // ...
}

function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    require(recipient == msg.sender, "recipient must be caller");
    // ...
}
```

Alternatively, track per-user deposited balances in transient storage and only allow each caller to sweep their own attributed share.

## Proof of Concept
```solidity
// Step 1: Alice swaps USDC → WETH, routing output to the router
vm.prank(alice);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(usdc),
    tokenOut: address(weth),
    zeroForOne: true,
    amountIn: 1_000e6,
    amountOutMinimum: 0,
    recipient: address(router),   // WETH lands on router
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));

uint256 routerWeth = weth.balanceOf(address(router));
assertGt(routerWeth, 0, "WETH stranded on router");

// Step 2: Bob drains Alice's WETH before she can unwrap it
uint256 bobEthBefore = bob.balance;
vm.prank(bob);
router.unwrapWETH9(0, bob);   // amountMinimum=0, recipient=bob — no revert

// Step 3: Bob received Alice's ETH; router is empty
assertEq(bob.balance - bobEthBefore, routerWeth, "Bob stole Alice's ETH");
assertEq(weth.balanceOf(address(router)), 0, "router drained");

// Step 4: Alice's follow-up unwrap reverts with InsufficientWETH
vm.prank(alice);
vm.expectRevert();
router.unwrapWETH9(1, alice);
```

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L48-55)
```text
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
