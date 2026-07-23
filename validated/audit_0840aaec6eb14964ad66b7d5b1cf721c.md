The vulnerability is real. Here is the complete trace and finding.

---

### Title
Excess ETH stranded on `MetricOmmSimpleRouter` after a WETH swap is claimable by any caller via `refundETH()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` sends the entire ETH balance of the router to `msg.sender` with no access control. When a user calls `exactInputSingle` (or any payable swap) with more ETH than `amountIn`, the `pay()` function wraps only `amountIn` worth of ETH into WETH and leaves the remainder on the contract. If the user does not include `refundETH` as a subsequent step in the same `multicall`, that excess ETH persists on the router across transaction boundaries and can be drained by any address that calls `refundETH()` next.

---

### Finding Description

**`pay()` wraps only the exact swap amount, leaving excess ETH on the contract.** [1](#0-0) 

When `token == WETH` and `nativeBalance >= value`, exactly `value` wei is deposited into WETH and forwarded to the pool. Any `nativeBalance - value` remainder stays as raw ETH on the router.

**`refundETH()` has no access control and sends to `msg.sender`.** [2](#0-1) 

The function is `external payable` with no `onlyOwner`, no deadline, and no recipient parameter — it unconditionally transfers the full ETH balance to whoever calls it.

**`multicall` does not auto-refund excess ETH at the end.** [3](#0-2) 

The loop simply delegates each call and returns. There is no post-loop ETH sweep, so any ETH not consumed by the swap steps persists on the contract after the multicall transaction completes.

**`receive()` does not prevent ETH accumulation via payable calls.** [4](#0-3) 

The `NotWETH` guard only blocks plain ETH transfers; it does not affect `msg.value` attached to payable function calls like `exactInputSingle` or `multicall`.

---

### Impact Explanation

A user who sends `2 ether` with `amountIn = 1 ether` (a common pattern for slippage headroom) and omits `refundETH` from their multicall permanently loses the 1 ether excess to the first attacker who calls `refundETH()` in a subsequent transaction. The attacker pays only gas. The loss is direct and immediate — no further preconditions are needed once the excess ETH is on the contract.

---

### Likelihood Explanation

- Sending slightly more ETH than `amountIn` is a standard user pattern for slippage tolerance.
- Forgetting to append `refundETH` to a multicall is a realistic omission, especially for integrators or front-ends that construct calldata programmatically.
- MEV bots routinely monitor contract ETH balances and can call `refundETH()` in the same block as the victim's transaction.

Severity: **Medium** (direct loss of user principal; requires one user-side omission but no attacker preconditions beyond calling a public function).

---

### Recommendation

Change `refundETH()` to accept an explicit `recipient` parameter instead of using `msg.sender`, or restrict it so it can only be called within a `multicall` context (e.g., via a transient reentrancy flag set at multicall entry). The simplest fix matching the pattern used by `unwrapWETH9` and `sweepToken` is:

```solidity
function refundETH(address recipient) external payable {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(recipient, balance);
    }
}
```

This ensures the caller must explicitly name the beneficiary, preventing a third party from redirecting stranded ETH to themselves.

---

### Proof of Concept

```
1. User calls multicall([exactInputSingle_calldata]){value: 2 ether}
   - params.tokenIn = WETH, params.amountIn = 1 ether
   - pay() branch: nativeBalance(2e18) >= value(1e18)
     → deposit 1 ether into WETH, transfer WETH to pool
     → 1 ether remains as ETH on the router
   - multicall returns; 1 ether is stranded on the contract

2. Attacker (separate EOA, separate tx) calls:
   MetricOmmSimpleRouter.refundETH()
   - address(this).balance == 1 ether
   - _transferETH(msg.sender, 1 ether) → attacker receives 1 ether

3. Assert: attacker.balance increased by 1 ether; user's excess ETH is gone.
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-77)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
