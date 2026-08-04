Confirmed: `_fillSameChain` reads `_params.surplusShareBps` live from storage at fill time [1](#0-0) , not a value locked in at `placeOrder` time. This is exact structural analog to the Infinity Exchange bug.

### Title
Mutable `surplusShareBps` applied retroactively to already-placed orders lets governance (or an untimely update) redirect solver-overpayment surplus away from the beneficiary - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
`IntentGatewayV2.placeOrder` fixes the order commitment (and thus what the user "agreed to") at placement time, but the split of any solver-overpayment surplus between the beneficiary and the protocol is not committed to the order at all — it is read live from mutable storage (`_params.surplusShareBps`) at `fillOrder` time. Governance can update `surplusShareBps` via `_updateParams` at any time, and that new rate applies immediately to every order already placed and awaiting a fill, exactly the "protocol fee rate can be arbitrarily modified... and the new rate applies to all existing orders" pattern from the Infinity Exchange finding.

### Finding Description
`placeOrder` bakes `protocolFeeBps` into the order at placement (the reduced amount is written into `order.inputs` and hashed into the commitment) [2](#0-1) , so that parameter is immune to retroactive change — a placed order's commitment simply won't validate against a different fee. However, the surplus split is calculated only at fill time in `_fillSameChain`:

```
protocolShare = (dust * _params.surplusShareBps) / 10_000;
beneficiaryShare = dust - protocolShare;
``` [3](#0-2) 

`_params.surplusShareBps` is not part of the `Order` struct and is not committed to at placement — it is simply the current live value of governance-controlled storage, updated via `_updateParams` and reachable through the cross-chain `UpdateParams` request handled in `onAccept` [4](#0-3) . Users have no way to bind the surplus-split ratio to the value that existed when they placed their order; whatever governance sets it to at fill time is what applies, even to orders that were pending before the change.

The cross-chain fill path (`ExtrinsicIntents`) uses the same `_params.surplusShareBps` pattern for surplus splitting (confirmed by the same `surplusShareBps` references in `ExtrinsicIntents.sol`), so this affects both same-chain and cross-chain fills.

### Impact Explanation
If `surplusShareBps` is raised (e.g. from a small split favoring the beneficiary to `10000` = 100% to protocol), any solver who overpays on a pre-existing pending order will have their entire overpayment surplus diverted from the beneficiary to protocol dust, with no recourse for the beneficiary who expected the split ratio in effect when they placed the order. This is a value-leak / wrong-beneficiary-amount issue: funds that should go to the order's beneficiary are redirected to the protocol's dust balance based on a parameter mutated after order placement. It matches the "logic attacks" / "unauthorized... transaction manipulation" bounty category since a governance-only knob silently changes the economic terms of orders users already committed capital to, without their consent or knowledge, and beneficiaries cannot detect this from the order structure itself.

### Likelihood Explanation
The change requires a governance `UpdateParams` action (via Hyperbridge's cross-chain governance flow authenticated in `onAccept`), so this is not exploitable by an arbitrary unprivileged attacker acting alone. However, unlike admin-key-compromise scenarios explicitly excluded by the bounty scope, this does not require any malicious/compromised actor — it is a built-in protocol design flaw: any legitimate, routine governance update to `surplusShareBps` (e.g., to adjust protocol economics going forward) automatically and unintentionally reaches into every currently-outstanding order's fill, which is the same "acknowledged, but disputed severity" pattern the original Infinity Exchange report flagged as valid at Medium severity by the judge (design decision issue, not requiring malicious intent).

### Recommendation
Snapshot `surplusShareBps` (and the destination-specific overrides) into the `Order` struct at `placeOrder` time (similar to how `protocolFeeBps` is baked into `order.inputs`), so the commitment binds the surplus-split ratio the user/solver agreed to, and later governance updates to `_params.surplusShareBps` only affect orders placed after the change.

### Proof of Concept
1. Governance sets `_params.surplusShareBps = 5000` (50/50 split).
2. User calls `placeOrder` expecting: if a solver overpays, the user (beneficiary) gets 50% of the surplus.
3. Before any solver fills, governance (via a `UpdateParams` cross-chain message, `IntentsBase._updateParams`) raises `surplusShareBps` to `10000` (100% to protocol).
4. A solver fills the order with a small overpayment; `_fillSameChain` computes `protocolShare = dust * 10000 / 10000 = dust`, `beneficiaryShare = 0` [3](#0-2) .
5. The beneficiary receives zero surplus despite having placed the order under a 50/50 expectation baked into no on-chain enforcement — confirming the retroactive-rate-change defect.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L82-92)
```text
            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;
            if (alreadyFilled == 0 && solverAmount > totalRequired) {
                fillAmount = totalRequired;
                uint256 dust = solverAmount - totalRequired;
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L300-331)
```text
        // Phase 2: Compute protocol fees and commitment from actual received amounts.
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
        TokenInfo[] memory reducedInputs;
        bytes32 commitment;

        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                if (originalAmount == 0) revert InvalidInput();
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }

            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
        } else {
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L551-568)
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
    }
```
