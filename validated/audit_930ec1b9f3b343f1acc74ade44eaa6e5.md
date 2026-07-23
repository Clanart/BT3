Audit Report

## Title
Stranded ETH from Non-WETH Swaps Silently Consumed by Subsequent WETH Swaps, Causing Permanent Loss of User Funds — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

The `pay()` function in `PeripheryPayments.sol` reads `address(this).balance` — the router's entire ETH balance — when deciding how much native ETH to wrap for a WETH payment. Because all swap and liquidity entry-points are `payable` with no guard rejecting ETH when `tokenIn != WETH`, a user who accidentally sends ETH alongside a non-WETH swap leaves that ETH stranded in the router. Any subsequent WETH swap by any caller will silently consume the stranded ETH, permanently draining the original sender's funds with no recourse.

## Finding Description

**Root cause — `pay()` uses total contract balance, not `msg.value`:**

In `PeripheryPayments.sol` lines 73–84, when `token == WETH`, the function reads `uint256 nativeBalance = address(this).balance` and uses that entire balance to wrap ETH. It does not track which ETH arrived in the current transaction:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // total balance, not msg.value
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

**ETH stranding — all entry-points are `payable` with no guard:**

`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` (and the liquidity adder functions) are all declared `external payable` with no check that `msg.value == 0` when `params.tokenIn != WETH`. When a user sends ETH with a non-WETH swap, `pay()` takes the final `else` branch (`safeTransferFrom`) and the ETH is never consumed — it accumulates in the router.

**The `receive()` guard is irrelevant:**

`receive()` (lines 32–34) only blocks plain ETH transfers with no calldata. ETH sent alongside a function call is accepted by the `payable` modifier before `receive()` is ever consulted.

**Exploit flow:**

1. User A calls `exactInputSingle({tokenIn: USDC, ...})` with `msg.value = 1 ether`. USDC is pulled via `safeTransferFrom`; 1 ETH sits in the router.
2. User B calls `exactInputSingle({tokenIn: WETH, amountIn: 1e18, ...})` with `msg.value = 0`. The callback fires `pay(WETH, userB, pool, 1e18)`. `address(this).balance == 1e18` (User A's ETH), so `nativeBalance >= value` fires: User A's ETH is wrapped and sent to the pool. User B's WETH is never touched.
3. User A calls `refundETH()` — balance is 0; nothing returned. User A has permanently lost 1 ETH.

## Impact Explanation

Direct, permanent loss of user principal (ETH). User A loses their entire accidentally-sent ETH balance with no recourse once User B's WETH swap executes. This matches the "Critical/High direct loss of user principal" allowed impact. The `refundETH()` escape hatch is ineffective because a MEV searcher monitoring the router's ETH balance can front-run it with a WETH swap to atomically consume the stranded balance.

## Likelihood Explanation

Sending ETH with a non-WETH swap is a realistic user error — common among users migrating from native-ETH DEX UIs or reusing scripts. Exploitation requires no privileged access: any ordinary WETH swap passively drains the stranded balance. MEV bots can monitor the router's ETH balance on-chain and exploit this atomically in the same block, making the `refundETH()` escape hatch practically useless.

## Recommendation

Add an input guard in every `payable` swap and liquidity entry-point:

```solidity
if (msg.value > 0 && params.tokenIn != WETH) revert ETHNotAcceptedForNonWETHSwap();
```

Alternatively, restrict `pay()` to use only the ETH that arrived in the current transaction by passing `msg.value` explicitly rather than reading `address(this).balance`. This requires threading `msg.value` through the callback context (e.g., storing it in transient storage alongside `payer` and `tokenToPay`).

## Proof of Concept

```
Setup: Router deployed with WETH address. User A has USDC; User B has WETH approved to router.

Step 1:
  User A calls exactInputSingle({tokenIn: USDC, amountIn: 1000e6, ...})
  with msg.value = 1 ether.
  → _setNextCallbackContext sets payer=UserA, tokenToPay=USDC
  → Pool callback fires: pay(USDC, UserA, pool, 1000e6)
  → Takes the `else` branch: safeTransferFrom(UserA, pool, 1000e6)
  → 1 ETH remains in router (address(this).balance == 1e18)

Step 2:
  User B calls exactInputSingle({tokenIn: WETH, amountIn: 1e18, ...})
  with msg.value = 0.
  → _setNextCallbackContext sets payer=UserB, tokenToPay=WETH
  → Pool callback fires: pay(WETH, UserB, pool, 1e18)
  → nativeBalance = address(this).balance = 1e18 (User A's ETH)
  → nativeBalance >= value → WETH.deposit{value: 1e18}(); transfer WETH to pool
  → User B's WETH never touched; User B gets a free swap

Step 3:
  User A calls refundETH()
  → address(this).balance == 0
  → Nothing returned; User A has permanently lost 1 ETH
```