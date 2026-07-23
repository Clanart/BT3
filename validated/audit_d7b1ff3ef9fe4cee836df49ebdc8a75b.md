Audit Report

## Title
`refundETH()` hardcodes `msg.sender` as recipient, enabling third-party theft of excess ETH from contract callers — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` unconditionally sends the router's full native ETH balance to `msg.sender` with no `recipient` parameter. When a contract without a `receive()` function sends excess `msg.value` for a WETH-in swap, the unspent ETH remains in the router and cannot be recovered by the original caller — because `_transferETH` reverts on failure. Any third party can then call `refundETH()` and receive the full stranded balance.

## Finding Description

**Root cause — `pay()` leaves excess ETH in the router.**

In `PeripheryPayments.sol` L73–77, when `nativeBalance >= value`, only `value` wei is wrapped and forwarded to the pool; the remainder (`nativeBalance − value`) stays as raw native ETH in the router:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```

**Recovery path — `refundETH()` hardcodes `msg.sender`.**

`refundETH()` at L58–63 sends the entire balance to `msg.sender` with no `recipient` override:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

`_transferETH` at L90–92 uses a raw `call` and reverts with `ETHTransferFailed()` on failure:

```solidity
function _transferETH(address to, uint256 value) internal {
    (bool ok,) = to.call{value: value}("");
    if (!ok) revert ETHTransferFailed();
}
```

If `msg.sender` is a contract without `receive()` or `fallback()`, the call fails and the entire `refundETH()` call reverts. The ETH remains in the router.

**Asymmetry with sibling helpers.**

`unwrapWETH9` (L37) and `sweepToken` (L48) both accept an explicit `recipient` parameter, allowing contract callers to redirect funds to a payable address. `refundETH()` is the only payment helper that lacks this parameter.

**Permissionless drain.**

Because `refundETH()` has no access control and sends `address(this).balance` to whoever calls it, any third-party observer can call it after the original depositor's call reverts and receive the full stranded balance.

**Multicall does not help.**

`multicall` at L39–44 uses `delegatecall`, preserving `msg.sender`. If a contract caller batches `exactInputSingle` + `refundETH()`, the `refundETH()` leg still targets the calling contract. If that contract has no `receive()`, the entire multicall reverts — rolling back the swap as well. The contract caller cannot complete any WETH swap with a safety buffer.

## Impact Explanation

Direct loss of user ETH principal. Any contract without a `receive()` function (aggregators, vaults, on-chain bots, non-payable multisig modules) that calls a payable swap function with `msg.value` exceeding the exact WETH cost will have the excess ETH permanently inaccessible to them. A permissionless frontrunner can call `refundETH()` and steal the full stranded balance. There is no admin recovery path; `sweepToken` and `unwrapWETH9` do not cover native ETH.

## Likelihood Explanation

Medium. Contracts interacting with DeFi routers commonly lack `receive()` functions. Sending a `msg.value` safety buffer larger than the exact swap cost is the standard defensive pattern for callers who cannot predict the exact WETH cost at submission time. The trigger condition is reachable by any unprivileged contract caller on the first WETH-in swap.

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

1. `ContractA` (no `receive()`) calls `exactInputSingle` with `msg.value = 1 ETH` for a WETH-in swap costing `0.5 ETH`.
2. Inside `_justPayCallback` → `pay(WETH, ContractA, pool, 0.5 ETH)`: `nativeBalance = 1 ETH >= value = 0.5 ETH`; router wraps `0.5 ETH` → WETH → pool; `0.5 ETH` remains as native ETH in the router.
3. Swap completes; `ContractA` receives the output token.
4. `ContractA` calls `refundETH()`: `_transferETH(ContractA, 0.5 ETH)` → low-level call to `ContractA` with no `receive()` → `ok = false` → reverts with `ETHTransferFailed()`.
5. Bob (frontrunner) calls `refundETH()`: `_transferETH(Bob, 0.5 ETH)` → succeeds; Bob receives `0.5 ETH` belonging to `ContractA`.
6. `ContractA` has permanently lost `0.5 ETH` with no recourse.