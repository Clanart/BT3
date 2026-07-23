Audit Report

## Title
`pay()` Consumes Router-Held Native ETH to Settle WETH Obligations Regardless of Payer Identity, Enabling Cross-User ETH Theft — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` prioritizes `address(this).balance` over pulling from the recorded `payer` when settling WETH obligations. ETH stranded on the router from a prior user's `exactOutputSingle` call (the surplus between `msg.value` and actual `amountIn`) can be consumed by a subsequent attacker's `exactInputSingle` call at zero cost to the attacker, causing direct loss of the victim's ETH.

## Finding Description
In `PeripheryPayments.pay()` (L73–84), when `token == WETH` and `payer != address(this)`:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);   // payer never charged
} else if (nativeBalance > 0) { ... }
else { IERC20(WETH).safeTransferFrom(payer, recipient, value); }
```

When `nativeBalance >= value`, the function deposits and forwards the router's native ETH and **never calls `safeTransferFrom(payer, …)`**. The `payer` stored in transient storage is silently ignored.

**ETH stranding path:** `exactOutputSingle` (L130) is `payable` and sets `payer = msg.sender` (L135). A user sends `msg.value = amountInMaximum`. During the swap callback, `_justPayCallback` (L192–199) calls `pay()` with `value = amountIn < amountInMaximum`. Since `nativeBalance >= value`, only `amountIn` is deposited; the surplus `amountInMaximum − amountIn` remains as native ETH on the router. If the user omits `refundETH()`, this ETH persists across transactions.

**Attack path:** The attacker calls `exactInputSingle{value: 0}(tokenIn=WETH, amountIn=strandedAmount)`. The router sets `payer = attacker` in transient storage (L71). The pool fires the swap callback; `_justPayCallback` calls `pay(WETH, attacker, pool, strandedAmount)`. Since `address(this).balance == strandedAmount >= strandedAmount`, the router deposits the victim's ETH as WETH and transfers it to the pool. The attacker's address is never charged. The attacker receives the full swap output at zero cost.

**Existing guards are insufficient:**
- `_requireExpectedCallbackCaller` (L49, L82–85 in `MetricOmmSwapRouterBase`) only validates that the callback comes from a factory-registered pool; it does not prevent the ETH substitution.
- `receive()` (L32–34) restricts direct ETH sends to WETH only, but does not affect ETH received via `msg.value` in payable functions like `exactOutputSingle` and `exactInputSingle`.
- The `amountOutMinimum` check only protects the attacker's output, not the victim's ETH.

The same substitution applies in `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback` (L172–177) when either pool token is WETH.

## Impact Explanation
Direct loss of user principal. The victim loses the full stranded ETH amount (up to `amountInMaximum − amountIn` per exact-output swap). The attacker receives the corresponding swap output tokens at zero cost. The loss is exact and deterministic, requires no oracle condition or slippage, and is repeatable for every stranded-ETH event. This meets the contest threshold for High severity direct loss of user funds.

## Likelihood Explanation
The precondition — ETH stranded on the router — arises naturally from the documented `exactOutputSingle + refundETH` multicall pattern whenever a user omits the `refundETH` step, sends excess `msg.value`, or experiences a partial multicall revert after ETH is deposited. An attacker can monitor the router's ETH balance on-chain and execute the theft in the next block with a single `exactInputSingle` call carrying `msg.value = 0`. No special role or privilege is required.

## Recommendation
Remove the native-ETH shortcut from the external-payer branch. When `payer != address(this)`, always pull WETH directly from the payer:

```solidity
} else if (token == WETH) {
    if (payer == address(this)) {
        IERC20(WETH).safeTransfer(recipient, value);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

If native-ETH input must be supported, deposit `msg.value` to WETH at the entry point of `exactInputSingle`/`exactOutputSingle` before setting the callback context, then set `payer = address(this)` for the WETH leg so the router's own WETH balance is used — attributing the ETH to the current caller before any callback fires.

## Proof of Concept
```
Setup:
  - Router deployed with WETH address.
  - Pool(WETH, TOKEN) exists with liquidity.
  - Victim has 1 ETH.

Step 1 — Victim strands ETH:
  victim.exactOutputSingle{value: 1 ether}(
      pool=Pool(WETH,TOKEN), tokenIn=WETH, tokenOut=TOKEN,
      amountOut=X, amountInMaximum=1 ether, recipient=victim
  )
  // Pool requests amountIn = 0.6 ETH in callback.
  // pay() sees nativeBalance=1 ETH >= 0.6 ETH.
  // Deposits 0.6 ETH as WETH, transfers to pool. Payer never charged.
  // 0.4 ETH remains on router. Victim omits refundETH().

Step 2 — Attacker steals:
  attacker.exactInputSingle{value: 0}(
      pool=Pool(WETH,TOKEN), tokenIn=WETH, tokenOut=TOKEN,
      amountIn=0.4 ETH, amountOutMinimum=0, recipient=attacker
  )
  // Router sets payer=attacker in transient storage.
  // Pool fires callback requesting 0.4 ETH worth of WETH.
  // pay() sees nativeBalance=0.4 ETH >= 0.4 ETH.
  // Deposits victim's 0.4 ETH as WETH, transfers to pool.
  // Attacker's payer address is never charged.
  // Attacker receives TOKEN output; victim loses 0.4 ETH.

Assert:
  attacker ETH/WETH spent: 0
  victim ETH lost: 0.4 ETH
  router ETH balance after: 0
```