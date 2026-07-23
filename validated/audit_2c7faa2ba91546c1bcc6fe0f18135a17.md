Audit Report

## Title
Router Native ETH Balance Consumed by Unrelated WETH Swaps, Draining Stranded User Funds - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
`PeripheryPayments.pay()` reads `address(this).balance` — the router's global native ETH balance — when settling a WETH-input swap. This balance is not scoped to the current caller or transaction. ETH stranded on the router from a prior user's `multicall` (where `refundETH()` was omitted) is silently consumed by any subsequent WETH-input swap, causing direct, unprivileged theft of the stranded funds.

## Finding Description
In `PeripheryPayments.sol` lines 73–84, the WETH branch of `pay()` reads `address(this).balance` and uses it to partially or fully satisfy the current swap's WETH obligation:

```solidity
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
}
```

`multicall` is `payable` (line 39 of `MetricOmmSimpleRouter.sol`), so any `msg.value` sent with a multicall lands on the router. The `receive()` guard (lines 32–34 of `PeripheryPayments.sol`) only blocks direct ETH pushes from non-WETH addresses; it does not prevent `msg.value` from accumulating across separate transactions. When a user calls `exactInputSingle` or `exactInput` with `tokenIn = WETH`, the pool callback fires `_justPayCallback` (lines 192–199), which calls `pay(WETH, payer, pool, value)`. At that point, `address(this).balance` may include ETH left by a different user in a prior transaction, reducing the `transferFrom` pull from the actual payer and consuming the prior user's funds.

The existing test `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` (line 106 of `MetricOmmSimpleRouter.native.t.sol`) demonstrates the correct pattern — including `refundETH()` — but there is no on-chain enforcement requiring it.

## Impact Explanation
Direct loss of user principal. User A sends `multicall{value: X}` with a WETH swap consuming only Y < X ETH and omits `refundETH()`. The remaining X−Y ETH is stranded on the router. Any subsequent caller (User B) executing a WETH-input swap of size ≥ X−Y has the stranded ETH applied toward their payment obligation, consuming User A's funds. User A loses X−Y ETH; User B pays proportionally less WETH from their own wallet. The loss is bounded only by the stranded amount, which can be arbitrarily large. This is a direct loss of user principal meeting High severity thresholds.

## Likelihood Explanation
The trigger is fully unprivileged: any address calling `exactInputSingle`, `exactInput`, or `exactOutputSingle` with `tokenIn = WETH` will consume whatever native ETH is on the router. Users routinely omit `refundETH()` from multicalls — the existing test suite explicitly demonstrates the correct pattern but the contract provides no on-chain enforcement. No special permissions, setup, or coordination are required; the attacker simply executes a normal WETH-input swap.

## Recommendation
1. **Track per-call ETH attribution**: record `address(this).balance` at the start of each top-level entry point (or at the start of `multicall`) and restrict `pay()` to use only the delta accrued during the current call, not the total balance.
2. **Alternatively**, remove implicit native-ETH-to-WETH conversion from `pay()` entirely and require callers to wrap ETH explicitly (e.g., via a dedicated `wrapETH` multicall step) before swapping, making attribution unambiguous.
3. **At minimum**, document that any `msg.value` not consumed by a WETH swap in the same multicall is permanently at risk of being claimed by the next WETH swap, and enforce `refundETH()` inclusion via off-chain tooling.

## Proof of Concept
```solidity
// Setup: router has 0 ETH balance initially.

// Step 1 — User A strands ETH on the router:
router.multicall{value: 1000}([
    abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
        pool: pool,
        tokenIn: WETH,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 500,          // only 500 of the 1000 ETH is used
        amountOutMinimum: 0,
        recipient: userA,
        deadline: ...,
        priceLimitX64: 0,
        extensionData: ""
    })))
    // NOTE: no refundETH() call — 500 ETH remains on router
]);
// router.balance == 500 ETH (User A's funds, stranded)

// Step 2 — User B calls a WETH swap with no msg.value:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: WETH,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: userB,
    deadline: ...,
    priceLimitX64: 0,
    extensionData: ""
}));
// Inside pay(WETH, userB, pool, 1000):
//   nativeBalance = 500  (User A's stranded ETH)
//   → deposits 500 ETH as WETH, transfers to pool
//   → pulls only 500 WETH from userB via transferFrom
// Result: pool receives 1000 WETH; userB pays 500 WETH instead of 1000.
// User A loses 500 ETH. User B gains a 500 WETH subsidy.
```

The corrupted value is `address(this).balance` read at line 74 of `PeripheryPayments.sol`, which is global router state not scoped to the current caller, allowing any prior transaction's unreclaimed ETH to alter the payment split of a subsequent unrelated swap.