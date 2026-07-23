Audit Report

## Title
`refundETH()` hardcodes `msg.sender` as recipient, permanently trapping excess native ETH for contract callers without `receive()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` unconditionally sends the router's entire native ETH balance to `msg.sender` with no `recipient` parameter. When a contract without a `receive()` function sends `msg.value` exceeding the exact WETH swap cost, the excess ETH is left in the router after `pay()` wraps only the required amount. The original depositor cannot recover the excess via `refundETH()` because the low-level ETH transfer reverts, and any third party can subsequently call `refundETH()` to drain the full stranded balance.

## Finding Description

**Root cause — `pay()` wraps only `value`, leaving the remainder as raw ETH:**

In `PeripheryPayments.sol` L74–77, when `nativeBalance >= value`, only `value` wei is wrapped and forwarded to the pool; the remainder (`nativeBalance − value`) stays as native ETH inside the router:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
```

**Recovery path — `refundETH()` hardcodes `msg.sender`:**

`refundETH()` at L58–63 sends the full balance to `msg.sender` with no `recipient` parameter:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

`_transferETH` at L90–92 uses a raw `call{value: value}("")` and reverts with `ETHTransferFailed()` on failure:

```solidity
(bool ok,) = to.call{value: value}("");
if (!ok) revert ETHTransferFailed();
```

If `msg.sender` is a contract without a `receive()` or `fallback()` function, the call fails and `refundETH()` reverts, leaving the ETH in the router indefinitely.

**Permissionless drain — any third party can steal the stranded ETH:**

Because `refundETH()` is `external` with no access control and sends `address(this).balance` to whoever calls it, once the original depositor's call reverts, any observer can call `refundETH()` and receive the full trapped balance.

**`multicall` escape hatch does not help:**

`multicall` at L39–44 uses `delegatecall`, preserving `msg.sender`. If the contract caller batches `exactInputSingle` + `refundETH()`, the `refundETH()` leg still targets `msg.sender` (the calling contract). If that contract has no `receive()`, the entire multicall reverts, rolling back the swap too. The contract caller cannot complete a WETH swap with any `msg.value` safety buffer.

**Asymmetry with sibling helpers:**

`unwrapWETH9(uint256 amountMinimum, address recipient)` at L37 and `sweepToken(address token, uint256 amountMinimum, address recipient)` at L48 both accept an explicit `recipient`, making them safe for contract callers. `refundETH()` is the only payment helper that lacks this parameter.

## Impact Explanation

Direct loss of user principal. Any contract without a `receive()` function (custom aggregators, vaults, non-payable multisig modules, on-chain bots) that calls a payable swap function with `msg.value > exact WETH cost` will have the excess ETH permanently inaccessible. A third-party frontrunner can call `refundETH()` and steal the full stranded balance. There is no admin recovery path; `sweepToken` and `unwrapWETH9` do not cover native ETH.

## Likelihood Explanation

Medium. Contracts interacting with DeFi routers commonly lack `receive()` functions. The trigger condition — sending any `msg.value` larger than the exact WETH cost — is the normal defensive pattern for callers who cannot predict the exact swap cost at submission time. The stranded ETH is immediately visible on-chain, making frontrunning straightforward and repeatable.

## Recommendation

Add a `recipient` parameter to `refundETH()`, consistent with `unwrapWETH9` and `sweepToken`:

```solidity
function refundETH(address recipient) external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(recipient, balance);
    }
}
```

This allows contract callers to specify an EOA or a payable address as the refund destination, eliminating both the trap and the theft vector.

## Proof of Concept

1. `ContractA` (no `receive()`) calls `exactInputSingle` with `msg.value = 1 ETH` for a WETH-in swap costing exactly `0.5 ETH`.
2. Inside `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, ContractA, pool, 0.5 ETH)`:
   - `nativeBalance = 1 ETH >= value = 0.5 ETH`
   - Router wraps `0.5 ETH` → WETH → pool.
   - `0.5 ETH` remains as native ETH in the router.
3. Swap completes; `ContractA` receives the output token.
4. `ContractA` calls `refundETH()`:
   - `_transferETH(ContractA, 0.5 ETH)` → low-level call to `ContractA` with no `receive()` → `ok = false` → reverts with `ETHTransferFailed()`.
5. Bob (frontrunner) calls `refundETH()`:
   - `_transferETH(Bob, 0.5 ETH)` → succeeds.
   - Bob receives `0.5 ETH` belonging to `ContractA`.
6. `ContractA` has permanently lost `0.5 ETH` with no recourse.