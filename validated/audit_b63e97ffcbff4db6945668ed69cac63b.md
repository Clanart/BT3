All four claims check out against the production code:

1. **Payable swap functions** — `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` are all `payable`. [1](#0-0) [2](#0-1) 

2. **`pay()` wraps only the exact requested amount** — when `nativeBalance >= value`, it deposits exactly `value` wei and leaves the surplus in the contract. [3](#0-2) 

3. **No post-swap refund** — `exactOutputSingle` (and all other swap functions) return after `_clearExpectedCallbackPool()` with no ETH refund. [4](#0-3) 

4. **`refundETH()` is permissionless** — sends the entire ETH balance to `msg.sender` with no access control. [5](#0-4) 

5. **`receive()` does not block the attack** — it only reverts ETH sent directly to the contract (not via WETH), but `msg.value` from a swap call bypasses it entirely. [6](#0-5) 

The exploit path is fully reachable with no special privileges. The finding is valid.

---

Audit Report

## Title
Excess Native ETH Sent for WETH-Input Swaps Is Not Automatically Refunded and Can Be Stolen by Any Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`, `metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

## Summary
Every swap entry-point in `MetricOmmSimpleRouter` is `payable`, but after the pool callback settles, no ETH refund is issued. The `pay()` helper wraps only the exact amount the pool requests, leaving any surplus ETH silently stranded in the router. The public, permissionless `refundETH()` function sends the entire contract ETH balance to `msg.sender`, allowing any third party to steal the stranded ETH.

## Finding Description
**Step 1 – ETH enters the router.**
`exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` are all `payable`, so a caller can send more ETH than the pool will ultimately charge (the canonical case being `exactOutputSingle`, where the exact input cost is unknown before execution).

**Step 2 – `pay()` wraps only the exact pool-requested amount.**
Inside `_justPayCallback`, `pay(tokenIn, payer, pool, amountToPay)` is called. When `token == WETH` and `payer != address(this)`, the branch at `PeripheryPayments.sol:74-77` reads `address(this).balance`, deposits exactly `value` wei via `IWETH9.deposit`, and transfers that WETH to the pool. Any `msg.value` above `value` remains as raw ETH in the contract.

**Step 3 – No refund after the swap.**
`exactOutputSingle` (lines 130–147) returns after `_clearExpectedCallbackPool()` with no ETH refund. The same is true for all other swap functions.

**Step 4 – `refundETH()` is permissionless.**
`PeripheryPayments.refundETH()` (lines 58–63) has no access control and sends `address(this).balance` to `msg.sender`. Any address — including a MEV bot watching the mempool — can call it in a subsequent transaction and receive the victim's excess ETH.

**Why existing guards are insufficient.**
The `receive()` fallback reverts for non-WETH senders, but this does not prevent ETH from entering via `msg.value` in a direct function call. The `multicall` pattern could mitigate this if users always bundle swaps with `refundETH()`, but there is no enforcement or documentation of this requirement, and the `refundETH()` function itself still sends to `msg.sender` rather than the original depositor.

## Impact Explanation
Direct loss of user principal. A user calling `exactOutputSingle` (or any swap) directly — without composing a `multicall` that appends `refundETH()` — and sending more ETH than the pool charges will permanently lose the difference (`msg.value − actualAmountIn`). For exact-output swaps this equals `amountInMaximum − actualAmountIn`. Any unprivileged address can steal the stranded ETH by calling `refundETH()`. This is a High-severity direct loss of user funds with a permissionless theft vector.

## Likelihood Explanation
`exactOutputSingle` is the canonical use-case for sending excess ETH: the caller cannot know the exact input cost before execution and must supply up to `amountInMaximum`. Users calling the function directly from a smart-contract integration, a script, or a wallet that does not compose a multicall will routinely leave excess ETH in the router. The theft requires only a single permissionless call with no special privileges, capital, or timing constraints beyond observing the mempool or a prior block.

## Recommendation
Add an automatic ETH refund at the end of every `payable` swap function:

```solidity
// at the end of exactOutputSingle / exactOutput / exactInputSingle / exactInput
uint256 excess = address(this).balance;
if (excess > 0) {
    _transferETH(msg.sender, excess);
}
```

Additionally, restrict `refundETH()` to send only to a `recipient` address recorded at swap entry (e.g., in transient storage), preventing third-party theft even if excess ETH is temporarily stranded. If the multicall-bundle pattern is intentionally relied upon, enforce it at the contract level and document it prominently.

## Proof of Concept
1. Alice calls:
   ```solidity
   router.exactOutputSingle{value: 1.1 ether}(ExactOutputSingleParams({
       pool: pool,
       recipient: alice,
       zeroForOne: true,
       tokenIn: WETH,
       amountOut: 1000e6,
       amountInMaximum: 1.1 ether,
       ...
   }));
   ```
2. The pool charges 1.0 ETH. `pay()` wraps 1.0 ETH and transfers WETH to the pool. 0.1 ETH remains in the router (`PeripheryPayments.sol:75-77`).
3. `exactOutputSingle` returns at line 147 with no refund.
4. Bob calls `router.refundETH()` (`PeripheryPayments.sol:58-63`) and receives Alice's 0.1 ETH.

Alice loses 0.1 ETH with no recourse. The attack is repeatable for every direct (non-multicall) exact-output swap with excess ETH.

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-130)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L145-147)
```text
    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```
