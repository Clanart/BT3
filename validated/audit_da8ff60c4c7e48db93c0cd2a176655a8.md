Audit Report

## Title
ETH Residue on Router Consumed by Subsequent WETH Swaps, Stealing Prior User's Funds — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay` helper in `PeripheryPayments` contains a partial-ETH branch (lines 78–81) that silently consumes any native ETH sitting on the router to subsidize a later user's WETH swap. Because the router is `payable` and users may send excess `msg.value` without calling `refundETH`, an unprivileged attacker can trigger a WETH swap that drains stranded ETH while paying only the shortfall — a direct theft of the prior user's principal.

## Finding Description
`PeripheryPayments.pay` (lines 73–84) handles the WETH leg of a swap callback with three branches. The vulnerable branch at lines 78–81 fires when `0 < address(this).balance < value`:

```solidity
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
}
```

The router has no per-user accounting of its ETH balance; it treats every wei on the contract as freely available to satisfy the current callback. ETH accumulates on the router whenever a caller passes `msg.value` to any payable entry-point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) and the swap does not consume the full amount. The `receive()` guard (lines 32–34) only blocks direct ETH pushes from non-WETH addresses; it does not prevent `msg.value` accumulation through the router's own payable functions. `refundETH()` (lines 58–63) exists to recover surplus but is never called automatically.

The callback path is: `exactInputSingle` → pool.`swap` → `metricOmmSwapCallback` → `_justPayCallback` → `pay`. At line 71, `msg.sender` (the original caller) is stored as payer in transient storage. In `_justPayCallback` (lines 192–199), `_getPayer()` returns the attacker, but the ETH consumed from `address(this).balance` belongs to the victim. The pool receives the correct token amount and is unaffected; the victim's ETH is irreversibly transferred to the pool on the attacker's behalf.

Existing guards are insufficient: the callback caller check (`_requireExpectedCallbackCaller`) only validates that the pool is the expected one, not that the ETH being used belongs to the current payer.

## Impact Explanation
Direct loss of user principal. A victim who sends excess `msg.value` and omits `refundETH` loses every wei of that surplus to the next caller who executes a WETH swap. The attacker's effective WETH cost is reduced by the stolen ETH amount. Loss magnitude equals the victim's unrecovered ETH balance on the router, which can be up to the full `msg.value` of their prior transaction.

## Likelihood Explanation
The attack requires no special role, no flash loan, and no privileged access. Any address can call `exactInputSingle` with `tokenIn = WETH` and `msg.value = 0` to trigger the vulnerable branch whenever the router holds ETH. The router's ETH balance is publicly readable via `address(router).balance`, making the opportunity trivially detectable on-chain. Users routinely over-send ETH to cover slippage, and `refundETH` is a separate manually composed call that many integrations omit.

## Recommendation
Replace the partial-ETH branch with the Uniswap v3 pattern: use native ETH only when the full `value` is covered; otherwise fall through to a pure `safeTransferFrom`:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else {
        // Do NOT use partial native balance — it may belong to a different user.
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

This eliminates cross-user ETH consumption. Users who wish to pay with ETH must ensure they send at least `value` wei; any surplus is recovered via `refundETH`.

## Proof of Concept
**Setup:** Router deployed with `WETH = W`. Pool `P` has `token0 = W`.

**Step 1 — Victim strands ETH:**
```
victim.call{value: 200e18}(router.exactInputSingle({
    pool: P, tokenIn: W, amountIn: 100e18, zeroForOne: true,
    amountOutMinimum: 0, recipient: victim, deadline: ...,
    priceLimitX64: 0, extensionData: ""
}))
```
Pool P calls `metricOmmSwapCallback(100e18, -Y, "")`.
`pay(W, victim, P, 100e18)`: `nativeBalance = 200e18 >= 100e18` → wraps 100 ETH, sends to P.
**100 ETH remains on router.** Victim does not call `refundETH`.

**Step 2 — Attacker exploits:**
```
attacker.call{value: 0}(router.exactInputSingle({
    pool: P, tokenIn: W, amountIn: 150e18, zeroForOne: true,
    amountOutMinimum: 0, recipient: attacker, deadline: ...,
    priceLimitX64: 0, extensionData: ""
}))
```
Pool P calls `metricOmmSwapCallback(150e18, -Z, "")`.
`pay(W, attacker, P, 150e18)`: `nativeBalance = 100e18 > 0` and `100e18 < 150e18` →
- Wraps 100 ETH (victim's), sends to P
- Pulls 50 WETH from attacker, sends to P

**Result:** Attacker paid 50 WETH for a 150 WETH swap. Victim lost 100 ETH. Pool received the correct 150 WETH equivalent and is unaffected.