Based on my analysis of the repository, here is my finding:

---

### Title
Unrestricted `recipient` in `sweepToken` and `unwrapWETH9` Allows Any Caller to Redirect Router-Held Funds — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`sweepToken` and `unwrapWETH9` are `public payable` functions that accept a caller-controlled `recipient` address with no check that `msg.sender` is the rightful owner of the router-held balance. Any attacker can call either function with `recipient = attacker` and drain the entire router balance of any token or WETH/ETH to themselves.

### Finding Description
`PeripheryPayments.sweepToken` and `PeripheryPayments.unwrapWETH9` both sweep the router's **entire** balance of a given token/WETH to a caller-supplied `recipient`:

```solidity
// PeripheryPayments.sol L37-55
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(recipient, balanceWETH);   // ← recipient is fully attacker-controlled
    }
}

function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) {
        IERC20(token).safeTransfer(recipient, balanceToken); // ← recipient is fully attacker-controlled
    }
}
```

Neither function checks `msg.sender == recipient` or that `msg.sender` deposited the balance being swept. The intended usage pattern is that users route swap output to `address(router)` and then call `unwrapWETH9`/`sweepToken` in the **same multicall** to atomically claim it. However, if a user sends the two steps as separate transactions — or sends tokens directly to the router — the router balance is exposed to theft by any third party who calls these functions first with their own address as `recipient`.

The `multicall` dispatcher itself uses `delegatecall` and reverts atomically on any sub-call failure, so within a single multicall the funds are safe. The vulnerability window opens whenever router-held balances persist across transaction boundaries, which occurs in the following realistic paths:

- A user calls `exactInputSingle` (or `exactInput`) with `recipient = address(router)` as a standalone transaction, intending to follow up with `unwrapWETH9` in a second transaction.
- A user sends tokens directly to the router address before initiating a swap.
- Any other non-multicall flow that leaves a balance on the router. [1](#0-0) 

### Impact Explanation
An attacker who observes a router balance (via mempool monitoring or on-chain state) can call `sweepToken(token, 0, attacker)` or `unwrapWETH9(0, attacker)` and redirect the **entire** router balance to themselves. The victim loses 100% of the funds they deposited to the router. This is a direct loss of user principal with no recovery path.

### Likelihood Explanation
The likelihood is medium. The protocol's recommended pattern (multicall) is safe, but:
1. Nothing in the interface or NatSpec warns users that non-multicall usage is unsafe.
2. The `recipient = address(router)` pattern for WETH unwrapping is explicitly demonstrated in tests (`test_multicall_tokenForWeth_thenUnwrapEth`), and a user who copies this pattern without multicall creates the vulnerability window.
3. MEV bots routinely monitor for profitable router balances and can front-run the victim's follow-up transaction. [2](#0-1) 

### Recommendation
Add a `msg.sender == recipient` guard, or restrict `recipient` to `msg.sender` only:

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    require(recipient == msg.sender, "recipient must be caller");
    // ... rest of function
}

function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    require(recipient == msg.sender, "recipient must be caller");
    // ... rest of function
}
```

Alternatively, remove the `recipient` parameter entirely and always send to `msg.sender`. If sending to a third-party address is required, add an explicit allowlist or authorization mechanism.

### Proof of Concept

```solidity
function testSweepToken_UnauthorizedRecipient() public {
    address alice = address(0xA11CE);
    address bob   = address(0xB0B);

    // Alice swaps token1 → WETH, routing output to the router
    // (simulating a user who sends two separate transactions instead of multicall)
    vm.startPrank(alice);
    token1.approve(address(router), 3_000);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token1),
        tokenOut:        address(weth),
        zeroForOne:      false,
        amountIn:        3_000,
        amountOutMinimum: 0,
        recipient:       address(router),   // ← Alice intends to unwrap in a follow-up tx
        deadline:        block.timestamp + 1,
        priceLimitX64:   type(uint128).max,
        extensionData:   ""
    }));
    vm.stopPrank();

    uint256 routerWeth = weth.balanceOf(address(router));
    assertGt(routerWeth, 0, "router holds Alice's WETH");

    // Bob front-runs Alice's unwrapWETH9 call and redirects funds to himself
    uint256 bobEthBefore = bob.balance;
    vm.prank(bob);
    router.unwrapWETH9(0, bob);   // ← no authorization check; bob steals Alice's WETH

    assertEq(weth.balanceOf(address(router)), 0, "router drained");
    assertGt(bob.balance, bobEthBefore, "bob received Alice's ETH");
}
``` [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L151-188)
```text
  ///      recursively inside `metricOmmSwapCallback`: each callback pays the current hop's input, then (unless on
  ///      the last pool) swaps the next pool for exactly that input amount. The first swap's input delta is total
  ///      `amountIn`.
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );

    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = _getExactOutputAmountIn();
    _clearExpectedCallbackPool();
  }
```
