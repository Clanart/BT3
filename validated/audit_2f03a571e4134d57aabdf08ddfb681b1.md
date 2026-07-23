Audit Report

## Title
Unguarded `sweepToken` / `unwrapWETH9` / `refundETH` Allow Any Caller to Drain Router-Held ETH and ERC-20 Balances — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`sweepToken`, `unwrapWETH9`, and `refundETH` in `PeripheryPayments.sol` carry no caller restriction. The router legitimately holds ETH and WETH between multicall steps — excess ETH from WETH-input swaps and WETH output directed to `address(router)` for unwrapping both remain on the contract until explicitly claimed. Any external caller can drain those balances in a follow-up transaction. A secondary vector in `pay`'s ETH-first branch silently consumes leftover ETH from prior users during subsequent legitimate swaps, requiring no sweep call at all.

## Finding Description

`sweepToken` and `unwrapWETH9` are declared `public payable` with no `onlyOwner`, `onlyCaller`, or equivalent guard, and accept a caller-supplied `recipient`: [1](#0-0) [2](#0-1) 

`refundETH` is `external payable` with the same absence of restriction; it sends the full ETH balance to `msg.sender`: [3](#0-2) 

The router is designed to hold assets transiently. The test suite confirms the expected user flow: a user sends 2 ETH for a 1 ETH WETH swap and must explicitly include `refundETH()` as a second multicall step to recover the excess. If that step is omitted, the ETH remains on the router after the transaction settles: [4](#0-3) 

Similarly, the WETH-output-then-unwrap pattern directs swap output to `address(router)` and relies on `unwrapWETH9` being included in the same multicall: [5](#0-4) 

If either step is omitted, the stranded balance is immediately claimable by any caller.

A second, compounding vector exists in `pay`'s WETH branch: it consumes the router's native ETH balance **before** pulling from the payer via `safeTransferFrom`. If the router holds leftover ETH from a prior user, the next WETH swap silently drains it — no explicit sweep call required: [6](#0-5) 

No existing guard in `multicall`, `exactInputSingle`, `exactInput`, or any base contract restricts who may call the sweep/refund functions or tracks per-user deposited amounts: [7](#0-6) 

## Impact Explanation

Direct loss of user principal. A user who sends excess ETH with a multicall and omits `refundETH` loses that ETH to the first caller who notices. WETH output directed to the router for unwrapping can be swept by anyone to any `recipient` via `sweepToken` or `unwrapWETH9`. The `pay` function's ETH-first logic compounds the issue by silently consuming stranded ETH during subsequent legitimate swaps, draining the balance without the attacker needing to call any sweep function. This meets the Critical/High direct loss of user principal threshold.

## Likelihood Explanation

Medium-High. The test suite itself demonstrates the omit-`refundETH` pattern as a realistic user mistake. `sweepToken` and `unwrapWETH9` require no special permissions and are callable by any EOA or contract. MEV bots routinely monitor for unprotected sweep functions on router contracts. The attack is repeatable, requires no setup, and is executable in a single follow-up transaction.

## Recommendation

Restrict `sweepToken`, `unwrapWETH9`, and `refundETH` so they can only be invoked by the originating user. One approach: record `msg.sender` at the start of each top-level `multicall` entry (e.g., in transient storage) and require that the same address calls any sweep/refund within the same transaction. Alternatively, track per-user deposited ETH and only allow recovery by the original depositor. At minimum, `sweepToken` and `unwrapWETH9` should send only to `msg.sender`, not to an arbitrary `recipient`, mirroring the `refundETH` pattern — though this alone does not prevent the `refundETH` theft vector.

## Proof of Concept

```solidity
// User A sends 2 ETH for a 1 ETH WETH swap, forgets refundETH
vm.deal(userA, 2 ether);
vm.prank(userA);
bytes[] memory calls = new bytes[](1); // missing refundETH call
calls[0] = abi.encodeWithSelector(
    router.exactInputSingle.selector,
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool), tokenIn: address(weth), tokenOut: address(token1),
        zeroForOne: true, amountIn: 1 ether, amountOutMinimum: 0,
        recipient: userA, deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
    })
);
router.multicall{value: 2 ether}(calls);
// 1 ETH remains on router — userA's funds

// Attacker steals it with a single permissionless call
uint256 attackerBefore = attacker.balance;
vm.prank(attacker);
router.refundETH();
assertEq(attacker.balance - attackerBefore, 1 ether);

// OR: attacker steals WETH stranded for unwrapping
vm.prank(attacker);
router.sweepToken(address(weth), 0, attacker);
// attacker receives all WETH held by router

// OR: silent drain via pay() ETH-first logic —
// next user's WETH swap consumes userA's leftover ETH with no sweep call
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```
