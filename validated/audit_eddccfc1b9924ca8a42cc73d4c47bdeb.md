Audit Report

## Title
Unguarded `sweepToken` Allows Any Caller to Drain Any ERC-20 Balance from the Router — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.sweepToken` is `public payable` with no `msg.sender` restriction and accepts a fully caller-controlled `token` and `recipient`. Any ERC-20 balance that accumulates on `MetricOmmSimpleRouter` or `MetricOmmPoolLiquidityAdder` — whether from a user setting `recipient = address(router)` in a multi-hop swap and sweeping in a second transaction, or from an airdrop/mistaken transfer — can be redirected to an arbitrary attacker-controlled address by any unprivileged caller.

## Finding Description
`sweepToken` at `PeripheryPayments.sol` L48–55 reads the contract's full balance of the caller-supplied `token` and transfers it to the caller-supplied `recipient` with no access control:

```solidity
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) {
        IERC20(token).safeTransfer(recipient, balanceToken);
    }
}
```

There is no `msg.sender == recipient` assertion, no whitelist of valid tokens, and no transient-context guard. The function is inherited unchanged by both `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder`.

The intended usage pattern for multi-hop swaps routes intermediate output to `address(this)` between hops (`MetricOmmSimpleRouter.sol` L106: `i == last ? params.recipient : address(this)`). The intended atomic pattern is `multicall([exactInput(recipient=router), sweepToken(token, min, user)])`. If a user issues these as two separate transactions — a realistic mistake given that `sweepToken` is a standalone public function — any watcher can front-run the second call with `sweepToken(token, 0, attacker)`, redirecting the full balance. Setting `amountMinimum = 0` bypasses the only guard. The same call drains any ERC-20 airdropped or mistakenly transferred to the router.

## Impact Explanation
Direct loss of user principal: a victim who sets `recipient = address(router)` in transaction 1 and sweeps in transaction 2 loses the entire swap output. Airdropped or mistakenly transferred tokens are permanently extractable by the first caller who notices them. Both `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` are affected because both inherit `PeripheryPayments` without overriding `sweepToken`. This is a direct loss of ERC-20 principal held by the router, meeting the Critical/High threshold under the allowed impact gate.

## Likelihood Explanation
The two-transaction race is a realistic user error: `sweepToken`, `unwrapWETH9`, and `refundETH` are all exposed as standalone public functions with no documentation warning that they must be composed atomically via `multicall`. MEV bots routinely monitor known router addresses for stranded ERC-20 balances. Airdrops to widely-used router contracts are common on EVM chains. The attacker requires no special privileges, no capital, and no prior setup — a single call with `amountMinimum = 0` suffices.

## Recommendation
Restrict `sweepToken` (and `unwrapWETH9`) so that only `msg.sender` can be the `recipient`:
```solidity
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    require(recipient == msg.sender, "recipient must be caller");
    ...
}
```
Alternatively, gate the function behind a transient-storage "active multicall" flag so it can only be called from within a `multicall` context. At minimum, document that these helpers must always be composed atomically and never called as standalone transactions.

## Proof of Concept
```
// Block N: victim calls exactInput with recipient = address(router)
//   → router receives 50_000 USDC as swap output

// Block N (same or next): attacker front-runs victim's sweep call
router.sweepToken(
    USDC,       // arbitrary token — no membership check
    0,          // amountMinimum = 0 → always passes
    attacker    // attacker-controlled recipient
);
// Result: attacker receives all 50_000 USDC; victim receives nothing.

// Airdrop scenario:
// Governance airdrops TOKEN to router address
router.sweepToken(TOKEN, 0, attacker);
// All airdropped TOKEN transferred to attacker.
```