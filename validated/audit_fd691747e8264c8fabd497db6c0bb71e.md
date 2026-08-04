### Title
`IntentGatewayV2.withdraw()` releases escrow via raw low-level `.call()` without verifying token code/return-value, letting an attacker plant a self-destructing/never-deployed token to steal a solver's collateral - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The original report's core broken invariant is: a "successful" token transfer bookkeeping event can be recorded even though no value ever moved, because the transfer primitive used (solmate's `SafeTransferLib`) does not check that the token address has contract code, and a call to an address with no code always returns `success = true`. `IntentGatewayV2.sol`'s escrow-release path reproduces the exact same primitive: it uses a raw `address.call(...)` and only checks the boolean `success`, never checking that `token` has code and never decoding/validating the ERC20 return value.

### Finding Description
`withdraw()` releases escrowed order inputs and transaction fees using: [1](#0-0) 

```solidity
if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    if (!sent) revert InsufficientNativeToken();
} else {
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();
}
```

and identically for `TRANSACTION_FEES`: [2](#0-1) 

Unlike the OZ `SafeERC20`/solmate `SafeTransferLib` used everywhere else in `placeOrder` (`IERC20(token).safeTransferFrom(...)`), this path does not use `SafeERC20`. A raw `.call()` to an address with **no contract code** always returns `(true, "")` in the EVM regardless of the calldata supplied — this is precisely the qBridge-style primitive described in the source report. The function then proceeds as if the transfer succeeded: [3](#0-2) 

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;
    ...
    _orders[body.commitment][token] -= amount;
```

`_filled` is set and `_orders[...]` is decremented unconditionally once `success == true`, even when the token has no code at call time and no value ever moved.

The `token` address used here is the one an ordinary user supplied as `order.inputs[i].token` in `placeOrder`. At deposit time `safeTransferFrom` does confirm the token had code (OZ's `SafeERC20` reverts on empty-code targets), but that only proves the token had code **at deposit time**. `withdraw()` is invoked later, after a cross-chain round trip (fill on destination, challenge period, `RedeemEscrow` proof delivery back to source) — a window during which an attacker who deployed the input token themselves can trivially remove its code (e.g. `SELFDESTRUCT` from a controlled implementation, or route through a proxy whose implementation is swapped to a codeless/no-op contract). When the `RedeemEscrow` request finally executes `withdraw()`, `token.call(...)` against the now-codeless address returns `success = true` with empty returndata, the function marks the order filled and decrements escrow accounting, but the solver/filler beneficiary receives nothing.

The same unguarded raw-call pattern also appears in the `SweepDust` handler: [4](#0-3) 

### Impact Explanation
This directly reproduces the "false success acceptance leading to loss of funds" impact class the bounty targets:
- A solver/filler who fronted real assets on the destination chain to fill an order is the rightful beneficiary of the escrowed input tokens on the source chain. If the escrowed token is a self-destructible contract controlled by the malicious order-placer, the filler's payout silently evaporates: `withdraw()` reports success (`EscrowReleased` event, `_filled` set, `_orders` decremented) while no tokens are actually transferred.
- Because `_filled[commitment]` is set and `_orders[commitment][token]` is zeroed on the first "successful" call, the failure is unrecoverable — there is no retry path once the internal state believes the escrow was released.
- This is an unprivileged-attacker path: the attacker only needs to deploy their own ERC20 (or destructible proxy) as the input token in a normal `placeOrder` call — no relayer, prover, or admin compromise is required.

### Likelihood Explanation
Likelihood is credible but not trivial: it requires the attacker to (a) control the ERC20 implementation used as an order input (fully permitted — `IntentGatewayV2` performs no allow-listing of input tokens), and (b) destroy/neuter that contract's code between order placement and the `RedeemEscrow` settlement, which happens after the destination fill and its challenge/proof period — giving the attacker a deterministic window to trigger the self-destruct. This mirrors the "credibility of attack path" reasoning that led the original report to be rated Medium rather than High.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns in `withdraw()` and the `SweepDust` handler with OpenZeppelin's `SafeERC20.safeTransfer`, which is already imported and used elsewhere in this same contract (`placeOrder`'s `safeTransferFrom` calls). `SafeERC20` verifies both that the target has code and that the call, if it returns data, returns `true`, closing the exact primitive exploited here.

### Proof of Concept
1. Attacker deploys `EvilToken`, a standard-looking ERC20 with an owner-gated `kill()` function that self-destructs the contract.
2. Attacker calls `placeOrder` with `order.inputs[0].token = EvilToken`, depositing real balance so `safeTransferFrom` succeeds and `_orders[commitment][EvilToken] = amount` is recorded.
3. A solver fills the order on the destination chain, sending real assets to the attacker's beneficiary there.
4. Before the `RedeemEscrow` post request (carrying the `WithdrawalRequest` for the solver's beneficiary) is delivered back to the source chain, the attacker calls `EvilToken.kill()`.
5. When `onAccept` → `withdraw()` executes on the source chain: `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` targets a codeless address and returns `(true, "")`.
6. `withdraw()` proceeds as success: `_filled[commitment] = beneficiary`, `_orders[commitment][token] -= amount`, `EscrowReleased` emitted — but the solver never receives the escrowed tokens, and no further claim path exists.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-667)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-714)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }
```
