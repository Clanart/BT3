### Title
`IntentGatewayV2`/`IntentsBase#_withdraw()` re-resolves `feeToken()` at redemption time, letting a host/params update strand or mis-pay escrowed relayer fees - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
The escrowed relayer-fee amount recorded per order (`_orders[commitment][TRANSACTION_FEES]`) is a bare `uint256` with no token identity attached. It is escrowed at order-placement time in whatever token `IDispatcher(host()).feeToken()` returns at that moment, but at redemption time `_withdraw()` looks the fee token up again dynamically via `IDispatcher(host()).feeToken()`. If Hyperbridge governance legitimately rotates `_params.host` (or the target `EvmHost`'s `feeToken`) between order placement and order settlement/refund, the amount recorded against the old fee token gets paid out in whatever the new `feeToken()` happens to be — this is the exact same "amount recorded now, resolved-by-address later" pattern as `InsuranceFund#syncDeps()`.

### Finding Description
`_withdraw()` in `evm/src/apps/intentsv2/IntentsBase.sol` (lines 390-425) handles both fills and refunds/cancellations: [1](#0-0) 

```solidity
if (finalize) {
    uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
    if (fees > 0) {
        delete _orders[body.commitment][TRANSACTION_FEES];
        IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
    }
```

`host()` returns `_params.host` [2](#0-1) , and `_params` (including `host`) is fully governance-mutable via `_updateParams()`, which is dispatched cross-chain by the `intents-coprocessor` pallet's `UpdateParams` extrinsic path and only validates that the new host is a non-zero contract address — it never checks continuity of `feeToken()` with the previously configured host: [3](#0-2) [4](#0-3) 

Orders can remain open for extended periods (cross-chain fill windows, timeout/cancellation flows, partial fills), so there is a real time window between when fees are escrowed (fee amount recorded, tokens pulled in the *old* fee token) and when `_withdraw` is finally invoked to release/refund them (fee amount paid in the *now-current* fee token).

Note that `EvmHost.updateHostParams` does have a guard, `CannotChangeFeeToken`, but it only protects the `EvmHost` contract's own balance of its own fee token [5](#0-4) . It provides no protection at all for `IntentGatewayV2`, which escrows tokens in its own contract, independent of the `EvmHost`. Additionally, `_params.host` in `IntentGatewayV2` can be repointed entirely to a *different* `EvmHost` instance with an unrelated `feeToken`, which sidesteps that guard altogether since it's a different host contract, not an update to the same one.

### Impact Explanation
When the fee token is rotated (either by updating `_params.host` to a new host, or the referenced host's own `feeToken` changes over its lifetime) while orders are outstanding:
- The gateway attempts `safeTransfer` of the *old* fee amount using the *new* token's address. If the gateway holds no such new-token balance (most likely, since it never received/escrowed that token for this order), the transfer reverts, and since `_withdraw` is called atomically inside fill/refund/cancel flows, the entire settlement or refund transaction reverts — permanently blocking release of the user's/solver's principal tokens escrowed in the same call, or at minimum forcing repeated failed settlement attempts.
- If the gateway happens to hold some balance of the new fee token (e.g., contributed by unrelated orders), the transfer silently succeeds and pays the beneficiary out of funds that rightfully belong to other users/orders — a wrong-beneficiary/wrong-asset fund loss, directly matching the `syncDeps()` "user gets 0 VUSDv2 instead of VUSD" pattern.
- The user's originally-escrowed old fee token remains inaccessible (never refunded), constituting a fund loss/lock for the affected order.

### Likelihood Explanation
This does not require a malicious or compromised governance actor — exactly like the original report, it only requires a normal, expected governance parameter update (e.g., migrating to a new `EvmHost` deployment, or the underlying host performing a legitimate fee-token migration) occurring while intents are in flight, which is a routine and anticipated operational event for a live cross-chain protocol with open, possibly cross-chain, settlement windows.

### Recommendation
Snapshot the fee token address (not just the amount) into `_orders` at the time fees are escrowed (e.g., store the `feeToken` address alongside the amount, or key `_orders[commitment][feeTokenAtEscrowTime]` the same way other token balances are tracked), and use that snapshot — not a live `host()`/`feeToken()` lookup — when releasing or refunding fees in `_withdraw()`. Alternatively, mirror the `EvmHost.CannotChangeFeeToken` pattern inside `IntentGatewayV2`: block `_updateParams` from changing to a host with a different `feeToken()` while the gateway holds any nonzero balance of the current fee token.

### Proof of Concept
1. Order is placed on `IntentGatewayV2`; `IDispatcher(host()).feeToken()` currently returns `TokenA`. The order's relayer fee amount `F` is escrowed and pulled in `TokenA`, recorded as `_orders[commitment][TRANSACTION_FEES] = F` (no token identity stored).
2. Before the order is filled/cancelled, Hyperbridge governance dispatches a legitimate `UpdateParams` request that updates `_params.host` to a new `EvmHost` deployment whose `feeToken()` returns `TokenB` (e.g., as part of a host migration). `_validateParams` only checks the new host is a contract; it accepts the change.
3. The order is later filled (or times out and is refunded), invoking `_withdraw()` with `finalize = true`. `_withdraw()` reads `fees = F` and calls `IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, F)` — now resolving to `TokenB`.
4. Since the gateway never escrowed `TokenB` for this order, the `safeTransfer` reverts (fund lock, blocking release of the user's principal escrowed in the same transaction), or, if the gateway holds an unrelated `TokenB` balance from other orders, the transfer succeeds and pays out `F` units of `TokenB` belonging to other users' funds instead of the `TokenA` originally owed — reproducing the `InsuranceFund#syncDeps()` loss pattern exactly.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L412-417)
```text
        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L532-538)
```text
    function _validateParams(Params memory p) internal view {
        if (p.host == address(0) || p.host.code.length == 0) revert InvalidInput();
        if (p.dispatcher == address(0) || p.dispatcher.code.length == 0) revert InvalidInput();
        if (p.surplusShareBps > 10_000) revert InvalidInput();
        if (p.protocolFeeBps >= 10_000) revert InvalidInput();
        if (p.priceOracle != address(0) && p.priceOracle.code.length == 0) revert InvalidInput();
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L551-556)
```text
    function _updateParams(ParamsUpdate memory update) internal {
        _validateParams(update.params);

        emit ParamsUpdated({previous: _params, current: update.params});
        _params = update.params;

```

**File:** evm/src/apps/IntentGatewayV2.sol (L90-92)
```text
    function host() public view override(IntentsBase, ExtrinsicIntents) returns (address) {
        return _params.host;
    }
```

**File:** evm/src/core/EvmHost.sol (L617-621)
```text
        address oldFeeToken = feeToken();
        if (oldFeeToken != address(0) && oldFeeToken != params.feeToken) {
            uint256 balance = IERC20(oldFeeToken).balanceOf(address(this));
            if (balance != 0) revert CannotChangeFeeToken();
        }
```
