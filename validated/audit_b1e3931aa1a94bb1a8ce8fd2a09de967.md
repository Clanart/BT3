The vulnerability is real. The `pay` function's WETH branch in `PeripheryPayments.sol` contains a confirmed PAYER_EXCLUSIVITY violation.

---

### Title
Stranded ETH in Router Consumed to Fund a Different User's WETH Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
The `pay` function's partial-ETH branch uses **any** native ETH held by the router contract to partially fund a WETH payment, without verifying that the ETH belongs to the designated payer. ETH stranded from a prior call (e.g., a `payable` function invocation that did not consume all `msg.value`) is silently consumed to subsidize a different user's swap, causing permanent loss of the stranded ETH.

### Finding Description

The vulnerable logic is at lines 78–81 of `PeripheryPayments.sol`:

```solidity
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
}
``` [1](#0-0) 

When `token == WETH` and `0 < address(this).balance < value`, the router:
1. Wraps **all** native ETH currently held by the contract (regardless of origin) into WETH and sends it to the recipient.
2. Pulls only `value - nativeBalance` WETH from the designated `payer` via `transferFrom`.

The contract's `receive()` guard (line 33) only blocks direct ETH transfers from non-WETH addresses; it does **not** prevent ETH from accumulating via any `payable` function call (e.g., `unwrapWETH9`, `sweepToken`, `refundETH`, or any router entry point that is `payable`). [2](#0-1) 

The existence of `refundETH()` confirms the design acknowledges ETH can be stranded; the bug is that no guard prevents that stranded ETH from being silently consumed by a subsequent unrelated payer's swap. [3](#0-2) 

### Impact Explanation
- **userB** sends ETH via any `payable` router function and omits `refundETH`. B ETH is stranded in the router.
- **userA** calls a WETH-input swap for amount V (with V > B). The router enters the partial branch, consumes userB's B ETH, and only pulls V−B WETH from userA.
- userB **permanently loses B ETH** — it is wrapped and transferred to the pool as part of userA's settlement.
- userA receives a discount of B ETH on their swap cost, funded entirely by userB.

This is a direct, irreversible loss of user principal with no recovery path. Severity: **Critical**.

### Likelihood Explanation
Any `payable` router function that accepts ETH but does not fully consume it can strand ETH. The pattern of sending ETH with a multicall-style batch and omitting `refundETH` is common in Uniswap-style router usage. No special permissions or malicious setup are required — two ordinary users interacting with the public router in sequence is sufficient.

### Recommendation
Remove the partial-ETH hybrid branch entirely. The router should either:
- Use **only** native ETH (full `msg.value` covers `value`), or
- Use **only** `transferFrom` on the designated payer.

Never mix the two sources. A safe implementation:

```solidity
} else if (token == WETH) {
    // Only use native ETH if it fully covers the payment.
    if (address(this).balance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

### Proof of Concept
1. userB calls `refundETH{value: 0.5 ether}()` (or any payable router function) and omits a subsequent `refundETH` call. Router now holds 0.5 ETH.
2. userA calls `exactInputSingle` with `tokenIn=WETH`, `amountIn=1 ether`, having approved only 0.5 WETH.
3. Router calls `pay(WETH, userA, pool, 1e18)`. `nativeBalance = 0.5e18 < 1e18` → partial branch fires.
4. Router wraps userB's 0.5 ETH → sends 0.5 WETH to pool; pulls 0.5 WETH from userA → sends to pool.
5. Pool receives full 1 WETH; userA's swap succeeds. userB's 0.5 ETH is gone permanently.

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
