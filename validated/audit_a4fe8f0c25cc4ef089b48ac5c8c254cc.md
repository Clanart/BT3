### Title
Unvalidated `protocolFeeBps` / `destinationFeeBps` in `IntentGatewayV2.onAccept` (Tron variant) can brick order settlement or over-charge users - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron fork of `IntentGatewayV2` applies cross-chain `UpdateParams` messages directly to `_params` and `_destinationProtocolFees` without any bounds validation, unlike the canonical EVM implementation (`IntentsBase._updateParams`) which enforces `feeBps < 10_000` and `surplusShareBps <= 10_000` via `_validateParams`. This mirrors the reported `PwIporTokenInternal.setWithdrawalFee` bug class: an unchecked config parameter that, once out of range, breaks a downstream fee-deduction invariant and can brick a core user-facing function (`placeOrder`/settlement), or in the worst case skew fee accounting.

### Finding Description
In the reference implementation, `_validateParams` and `_updateParams` in `evm/src/apps/intentsv2/IntentsBase.sol` explicitly guard fee parameters: [1](#0-0) [2](#0-1) 

`p.protocolFeeBps >= 10_000` and each `destinationFees[i].destinationFeeBps >= 10_000` both revert with `InvalidInput`, preventing a fee rate at or above 100% from ever being persisted.

The Tron variant of the same contract (`evm/tron/contracts/apps/IntentGatewayV2.sol`) implements `onAccept` for `RequestKind.UpdateParams` with none of these checks: [3](#0-2) 

`_params = update.params;` is assigned wholesale, and `_destinationProtocolFees[stateMachineId] = feeBps;` is written per-entry with no upper bound check on `feeBps`. Compare directly to the destination-fee validation present in the primary implementation at line 560 (`if (feeBps >= 10_000) revert InvalidInput();`), which is absent here.

Since `onAccept` is only reachable through a genuine Hyperbridge-authenticated post request (gated by `onlyHost` and the `source == hyperbridge()` check at line 629), this still requires the request to originate from the governance/hyperbridge module — the same trust model as the original "owner-only" `setWithdrawalFee` in the audit report. The point of the analog is that **even though the caller is meant to be trusted, the missing sanity check on the numeric parameter allows an unintended value (e.g. `protocolFeeBps` or a `destinationFeeBps` ≥ 10000, i.e. ≥100%) to be persisted**, which then corrupts the fee-deduction arithmetic used everywhere `protocolFeeBps`/`_destinationProtocolFees` are read for `placeOrder` accounting (subtracting `amount * feeBps / 10000` from escrowed inputs, mirrored in the reference tests, e.g. `evm/tests/foundry/IntentGatewayV2Test.sol:3155-3170`).

### Impact Explanation
If a `feeBps` value ≥ 10000 is ever propagated through `UpdateParams` on the Tron deployment (e.g. due to an off-by-one/unit mismatch in the governance dispatch pipeline, or a malformed cross-chain payload that is otherwise correctly authenticated), the fee subtraction (`amount - amount*feeBps/10000`) can underflow and revert, permanently bricking `placeOrder` for every order routed to that destination/chain — a direct availability/fund-lock failure of the intent-settlement path, analogous to `unstake` being permanently unusable in the audit report. Depending on how the fee is applied elsewhere (surplus split, destination-specific overrides), a value in the 100%+ range could also result in escrowed user funds being fully consumed as "fee" rather than reaching the intended beneficiary/solver, i.e. wrong-amount fund movement in the intent settlement flow.

### Likelihood Explanation
The path requires a `UpdateParams` post request from Hyperbridge's governance/module identity — this is a privileged but legitimate protocol pathway, not an attacker-controlled one. However, since the invariant break comes purely from a missing sanity check (not from a malicious/compromised actor), and the parallel EVM implementation demonstrably considers this validation important enough to enforce (with dedicated tests such as `testRevert_UpdateParams_DestinationFeeBpsTooHigh` in `evm/tests/foundry/IntentGatewayV2Test.sol:3625-3657`), its absence in the Tron variant is a genuine drift/regression bug rather than a hardened design choice — matching the "Medium" severity classification of the original report.

### Recommendation
Add the same `_validateParams`-equivalent checks to the Tron `IntentGatewayV2.onAccept` `UpdateParams` branch before assigning `_params` and before writing each `_destinationProtocolFees[stateMachineId]`:
- revert if `update.params.protocolFeeBps >= 10_000`
- revert if `update.params.surplusShareBps > 10_000`
- revert if any `update.destinationFees[i].destinationFeeBps >= 10_000`

This brings the Tron contract in line with `evm/src/apps/intentsv2/IntentsBase.sol` and prevents an out-of-range fee parameter from bricking or corrupting order settlement.

### Proof of Concept
1. Hyperbridge (the authenticated `hyperbridge()` source) dispatches a `UpdateParams` post request to the Tron `IntentGatewayV2` with `update.params.protocolFeeBps = 10000` (or higher) — no local check stops this, unlike the mainline EVM contract which would revert with `InvalidInput`.
2. `onAccept` executes `_params = update.params;` at line 639, persisting the invalid 100%+ fee.
3. A user calls `placeOrder` with USDC input `amount`; the fee-deduction arithmetic `amount - (amount * protocolFeeBps) / 10000` now computes `amount - amount` or underflows for `feeBps > 10000`, either zeroing out the user's escrowed input (full fund loss to "fee") or causing a revert that permanently blocks `placeOrder` for that gateway instance — mirroring the original `unstake`-bricking bug from the audit report.

Note: I was not able to fully confirm within the available context whether a downstream guard elsewhere in the Tron contract's `placeOrder`/fee-deduction code additionally clamps `protocolFeeBps`/`destinationFeeBps` before use (the relevant `placeOrder` fee-arithmetic section of `evm/tron/contracts/apps/IntentGatewayV2.sol` was not fully retrievable in this session); this should be verified directly against the file before treating the impact as certain.

### Citations

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L551-567)
```text
    function _updateParams(ParamsUpdate memory update) internal {
        _validateParams(update.params);

        emit ParamsUpdated({previous: _params, current: update.params});
        _params = update.params;

        for (uint256 i; i < update.destinationFees.length;) {
            bytes memory chain = update.destinationFees[i].chain;
            uint256 feeBps = update.destinationFees[i].destinationFeeBps;
            if (feeBps >= 10_000) revert InvalidInput();
            _destinationProtocolFees[keccak256(chain)] = feeBps;

            unchecked {
                ++i;
            }
            emit DestinationProtocolFeeUpdated(string(chain), feeBps);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L635-651)
```text
        } else if (kind == RequestKind.UpdateParams) {
            // Decode the body which includes optional destination-specific protocol fee updates
            ParamsUpdate memory update = abi.decode(incoming.request.body[1:], (ParamsUpdate));
            emit ParamsUpdated({previous: _params, current: update.params});
            _params = update.params;

            // Update destination-specific protocol fees if provided
            for (uint256 i; i < update.destinationFees.length;) {
                bytes32 stateMachineId = update.destinationFees[i].stateMachineId;
                uint256 feeBps = update.destinationFees[i].destinationFeeBps;
                _destinationProtocolFees[stateMachineId] = feeBps;

                unchecked {
                    ++i;
                }
                emit DestinationProtocolFeeUpdated(stateMachineId, feeBps);
            }
```
