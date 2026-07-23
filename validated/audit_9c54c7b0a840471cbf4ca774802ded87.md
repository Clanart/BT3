The `unwrapWETH9` function in `PeripheryPayments.sol` is confirmed to have no access control. Let me verify the full picture before rendering a verdict.

The code is fully readable. Here is my determination:

---

### Title
Unguarded `unwrapWETH9` Drains Any WETH Stranded on the Router to an Attacker-Controlled Recipient — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`unwrapWETH9` is `public payable` with no caller check. It reads the router's **entire** WETH balance and forwards it as ETH to an arbitrary `recipient` supplied by the caller. Any WETH that lands on the router between transactions — including WETH output from a swap where the user set `recipient = address(router)` — can be stolen by any third party before the victim calls `unwrapWETH9`.

### Finding Description [1](#0-0) 

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
}
```

Three properties combine to create the vulnerability:

1. **No access control.** The function is `public`; any EOA or contract can call it at any time.
2. **Whole-balance sweep.** It reads `IERC20(WETH).balanceOf(address(this))` — the full router balance — not an amount attributed to the caller.
3. **Caller-chosen recipient.** The ETH is forwarded to an arbitrary `recipient` argument, not to `msg.sender`.

The intended usage pattern (shown in the test suite) is to combine the swap and the unwrap atomically inside a single `multicall`: [2](#0-1) 

When a user instead issues two separate transactions — `exactInputSingle(…, recipient=router)` followed by `unwrapWETH9(0, victim)` — there is a window between the two transactions during which any attacker can call `unwrapWETH9(0, attacker)` and receive the victim's ETH. The `amountMinimum = 0` bypass means the guard on line 39 is trivially satisfied even when the balance is dust.

The same issue applies to `sweepToken`: [3](#0-2) 

Any ERC-20 stranded on the router between transactions can be swept to an attacker-chosen address.

### Impact Explanation

Direct theft of user ETH (or ERC-20) output. A victim who routes a token→WETH swap with `recipient = address(router)` and then calls `unwrapWETH9` in a separate transaction loses the full swap output to a front-running attacker. Impact is **High**: complete loss of the user's swap proceeds with no recovery path.

### Likelihood Explanation

The attack requires only that WETH sits on the router between two transactions. This happens whenever a user does not use `multicall` to atomically combine the swap and the unwrap — a common mistake for users interacting directly with the router (e.g., via Etherscan, a custom script, or a wallet that does not compose multicalls). A mempool-watching bot can automate the front-run with zero cost beyond gas.

### Recommendation

Restrict `unwrapWETH9` (and `sweepToken`) so that only `msg.sender` can be the `recipient`, or add a `checkDeadline`/`onlyMsgSender` guard. The simplest fix is to remove the `recipient` parameter and always send to `msg.sender`:

```solidity
function unwrapWETH9(uint256 amountMinimum) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(msg.sender, balanceWETH);
    }
}
```

This preserves the `multicall` pattern (the caller is the `multicall` dispatcher, which is the user's EOA via `delegatecall`) while eliminating the arbitrary-recipient theft vector.

### Proof of Concept

```solidity
// Foundry test sketch
function test_attacker_steals_victim_weth_output() public {
    // 1. Victim swaps token1 → WETH, output goes to router
    vm.prank(victim);
    router.exactInputSingle(ExactInputSingleParams({
        ...,
        tokenOut: address(weth),
        recipient: address(router),   // WETH lands on router
        ...
    }));

    uint256 routerWeth = weth.balanceOf(address(router));
    assertGt(routerWeth, 0);

    // 2. Attacker front-runs victim's unwrapWETH9 call
    uint256 attackerEthBefore = attacker.balance;
    vm.prank(attacker);
    router.unwrapWETH9(0, attacker);   // amountMinimum=0 bypasses the guard

    // 3. Attacker receives victim's ETH; victim gets nothing
    assertEq(attacker.balance - attackerEthBefore, routerWeth);
    assertEq(weth.balanceOf(address(router)), 0);
}
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
