### Title
Any caller can steal ETH or tokens stranded on the router via unrestricted `refundETH`, `sweepToken`, and `unwrapWETH9` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.refundETH()`, `sweepToken()`, and `unwrapWETH9()` are all `public`/`external` with **no caller restriction and no ownership check**. Any address can call them at any time and redirect the router's entire ETH, ERC-20, or WETH balance to an arbitrary recipient. When a user's swap leaves residue on the router between transactions — the most realistic case being excess ETH from an `exactOutputSingle` call with `msg.value > actualAmountIn` — an attacker can immediately drain that residue.

---

### Finding Description

`PeripheryPayments` exposes three settlement helpers that operate on the router's **total** balance with no attribution or caller guard:

```solidity
// PeripheryPayments.sol L37-L63
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override { ... }
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override { ... }
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) { _transferETH(msg.sender, balance); }
}
```

`sweepToken` and `unwrapWETH9` accept a caller-controlled `recipient`; `refundETH` sends to `msg.sender`. None of the three checks who deposited the value or whether the caller is entitled to it.

ETH residue is created by the `pay()` helper whenever `token == WETH` and `msg.value > amountIn`:

```solidity
// PeripheryPayments.sol L73-L77
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    }   // ← excess nativeBalance stays on the router
```

A user calling `exactOutputSingle` with WETH as input must supply `msg.value = amountInMaximum` because the exact cost is unknown before execution. After the swap, `actualAmountIn ≤ amountInMaximum`; the difference remains as raw ETH on the router. The design expects the user to append `refundETH()` inside the same `multicall`, but:

1. `exactOutputSingle` is individually callable without `multicall`.
2. Even inside `multicall`, a user who omits the refund step leaves ETH exposed.
3. `refundETH()` is callable by **any** address in a separate transaction.

The same pattern applies to ERC-20 residue: a user who sets `recipient = address(router)` in `exactInput` or `exactInputSingle` (intending to sweep in a follow-up call) exposes the output tokens to `sweepToken(token, 0, attacker)`.

---

### Impact Explanation

Direct loss of user principal. An attacker who monitors the mempool can call `refundETH()` immediately after a victim's `exactOutputSingle{value: X}` transaction confirms, stealing `X − actualAmountIn` ETH. For `sweepToken`, the attacker can redirect the router's entire token balance — potentially the full swap output — to an arbitrary address. No privileged role is required; any EOA can execute the attack.

---

### Likelihood Explanation

`exactOutputSingle` with native ETH input is a standard user flow (swap ETH for a fixed amount of token). Users routinely supply a round `msg.value` (e.g., 1 ETH) when the actual cost is less. Omitting `refundETH()` from the multicall, or calling `exactOutputSingle` directly, is a realistic mistake. The attack requires only a single public call with no setup, making it trivially repeatable.

---

### Recommendation

Add a caller-attribution guard to all three helpers. The simplest approach is to record `msg.sender` in transient storage at the start of each top-level router entry point and require that the same address calls the settlement helpers:

```solidity
modifier onlyInitiator() {
    require(msg.sender == _tloadAddress(T_SLOT_INITIATOR), "not initiator");
    _;
}

function refundETH() external payable onlyInitiator { ... }
function sweepToken(...) public payable onlyInitiator { ... }
function unwrapWETH9(...) public payable onlyInitiator { ... }
```

Alternatively, remove the `recipient` parameter from `sweepToken` and `unwrapWETH9` and always send to `msg.sender`, matching the behavior of `refundETH`. This eliminates the arbitrary-redirect vector while still requiring the caller-restriction fix for `refundETH`.

---

### Proof of Concept

```
Setup:
  - Router deployed with WETH address.
  - Pool: WETH / Token1, seeded with liquidity.
  - Victim has 2 ETH and wants exactly 1000 Token1.

Step 1 — Victim calls exactOutputSingle directly (no multicall):
  router.exactOutputSingle{value: 2 ether}(ExactOutputSingleParams({
      pool: pool,
      tokenIn: WETH,
      tokenOut: Token1,
      zeroForOne: true,
      amountOut: 1000,
      amountInMaximum: 2 ether,
      recipient: victim,
      deadline: ...,
      priceLimitX64: 0,
      extensionData: ""
  }));
  // Actual cost = 0.8 ETH. Remaining 1.2 ETH sits on the router.

Step 2 — Attacker (in the next block or same block) calls:
  router.refundETH();
  // Attacker receives 1.2 ETH. Victim's excess is gone.

Alternative (token theft):
  // Victim calls exactInputSingle with recipient = address(router) by mistake.
  // Attacker calls sweepToken(Token1, 0, attacker) and receives all Token1.
```