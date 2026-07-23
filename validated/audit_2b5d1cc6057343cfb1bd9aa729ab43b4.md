### Title
Unguarded `unwrapWETH9` and `sweepToken` Allow Any Caller to Drain Router's Entire WETH/Token Balance to Arbitrary Recipient — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`unwrapWETH9` and `sweepToken` are `public payable` with no caller restriction and accept a fully attacker-controlled `recipient`. Any address can call either function at any time, draining the router's entire WETH or ERC-20 balance to an arbitrary address.

---

### Finding Description

`unwrapWETH9` reads the router's full WETH balance, withdraws it, and forwards the ETH to the caller-supplied `recipient` with zero access control: [1](#0-0) 

The only guard is `balanceWETH < amountMinimum`, which is trivially bypassed by passing `amountMinimum = 0`. [2](#0-1) 

`sweepToken` has the identical structure: [3](#0-2) 

WETH lands on the router in the documented, intended usage pattern: a user sets `recipient: address(router)` in `exactInput` / `exactInputSingle` and then calls `unwrapWETH9` in a subsequent multicall step. The test suite confirms this pattern explicitly: [4](#0-3) 

When the swap and the unwrap are issued as two separate transactions (rather than a single atomic `multicall`), WETH is stranded on the router between blocks. An attacker monitoring the mempool can front-run the victim's `unwrapWETH9` call — or simply call it independently at any time — and redirect 100% of the balance to themselves.

The `multicall` implementation propagates reverts atomically, so a partial failure within one multicall does not strand funds. The realistic stranding paths are:

1. **Non-atomic usage**: user calls `exactInput(recipient=router)` in one transaction and `unwrapWETH9` in a separate transaction.
2. **Direct WETH transfer**: user or integrator sends WETH directly to the router address.
3. **Integrator/contract wallets**: any caller that issues the swap and the unwrap as separate calls (e.g., a smart-contract wallet batching them across blocks).

---

### Impact Explanation

Any WETH (or ERC-20 token via `sweepToken`) that resides on the router — even transiently between two user transactions — can be stolen in full by any unprivileged address. The victim receives nothing; the attacker receives 100% of the stranded balance as ETH. This is direct, complete loss of user principal with no recovery path.

---

### Likelihood Explanation

The intended usage pattern (set `recipient=router`, then unwrap) is documented and tested. Users, integrators, and smart-contract wallets that do not bundle both steps into a single `multicall` are silently exposed. Front-running is straightforward: watch for `exactInput`/`exactInputSingle` with `recipient=address(router)` and immediately call `unwrapWETH9(0, attacker)` in the next block or via a sandwich.

---

### Recommendation

Restrict `unwrapWETH9` and `sweepToken` so that only `msg.sender` can designate themselves as recipient, or add a `msg.sender == recipient` check:

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    require(recipient == msg.sender, "recipient must be caller");
    ...
}
```

Alternatively, remove the `recipient` parameter entirely and always send to `msg.sender`, matching the behavior of `refundETH`. [5](#0-4) 

---

### Proof of Concept

```
1. Victim calls exactInput({..., recipient: address(router)}) — WETH lands on router.
2. Attacker observes the pending or confirmed transaction.
3. Attacker calls router.unwrapWETH9(0, attacker).
   - balanceWETH = victim's full WETH amount
   - amountMinimum = 0  →  check passes
   - WETH is withdrawn and ETH is sent to attacker
4. Victim calls unwrapWETH9(amount, victim) → reverts with InsufficientWETH(amount, 0).
5. Victim has lost their full swap output; attacker has received it as ETH.
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L219-235)
```text
    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInput.selector,
      IMetricOmmSimpleRouter.ExactInputParams({
        tokens: tokens,
        pools: pools,
        extensionDatas: extensionDatas,
        zeroForOneBitMap: 0,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: address(router),
        deadline: _deadline()
      })
    );
    calls[1] = abi.encodeWithSelector(router.unwrapWETH9.selector, uint256(0), recipient);
    router.multicall(calls);
```
