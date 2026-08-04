### Title
`surplusShareBps` is read live at fill-time instead of being locked into the order commitment, letting a live parameter change retroactively redirect already-escrowed order proceeds - ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol], [File: evm/src/apps/intentsv2/ExtrinsicIntents.sol])

### Summary
This mirrors the auction-fee report's core defect: a value that determines how funds are split at settlement is mutable *after* the user has already committed funds, and nothing pins the value used at settlement to the value that was in effect (or disclosed) when the order was created. In `IntentGatewayV2`, `protocolFeeBps` is properly hashed into the order commitment at `placeOrder` time (fee is fixed forever for that order), but `surplusShareBps` is not — it is read fresh from mutable storage (`_params.surplusShareBps`) at `fillOrder` time, arbitrarily far in the future relative to when the user escrowed funds and agreed to the order terms.

### Finding Description
`placeOrder` computes the protocol fee once and bakes the *reduced* input amount into `commitment = keccak256(abi.encode(order))`, so `protocolFeeBps` is effectively locked at order-creation time: [1](#0-0) 

However, the surplus split that determines how much of a solver's overpayment goes to the user vs. the protocol is computed at fill time using the *current* value of `_params.surplusShareBps`, not any value captured when the order was placed: [2](#0-1) [3](#0-2) 

`_params` (including `surplusShareBps`, bounded only by `<= 10_000` i.e. up to 100%) is overwritten wholesale by `_updateParams`/`onAccept(UpdateParams)`, with no reference to which in-flight orders exist or what split they were placed under: [4](#0-3) 

An order can sit escrowed for up to `order.deadline` (measured in blocks), which can be an arbitrarily long window. During that window the operative split parameter can be pushed to `10_000` (100% of surplus to protocol) or back down, and it applies retroactively to every order that is filled after the change — including orders that were placed and priced by the user under the old split. This is exactly the auction analog: "the fee/split may be changed during the [order's] lifetime," and the value that ultimately governs settlement is not the value the counterparty (the user/beneficiary) relied on when committing funds.

### Impact Explanation
When a solver overpays (deliberately or due to slippage/rounding), the surplus split silently uses whatever `surplusShareBps` is live at fill time rather than what was in effect at placement. If the split is later set to 100% to the protocol, beneficiaries lose 100% of any surplus they would otherwise have received on outstanding orders, with no mechanism to opt out or re-price. Because `protocolFeeBps` *is* pinned into the commitment but `surplusShareBps` is not, the codebase's own design shows the fix (bind the parameter into the commitment) was applied inconsistently — this is a genuine logic defect, not a hypothetical "malicious governance" scenario: normal, routine fee-tuning by the protocol operator has an unintended retroactive effect on already-escrowed user funds.

### Likelihood Explanation
Any order that is not filled immediately (long-`deadline` limit orders, orders awaiting solver competition, or orders that are legitimately slow to fill on the destination chain) is exposed for its entire open window. No attacker action or malicious relayer/prover is required to trigger the divergence — only a normal governance parameter update landing between `placeOrder` and `fillOrder`, which is an expected and periodic operational event (the docs describe both fee levers as governance-tunable). The bug is deterministic and always present given that timing window, not probabilistic or peer-dependent.

### Recommendation
Snapshot `surplusShareBps` (and the resolved destination fee, if not already effectively fixed) into the order at `placeOrder` time the same way `protocolFeeBps` already is, and hash it into `commitment`, so `fillOrder` always applies the split rate the user agreed to when escrowing funds rather than whatever rate happens to be live when a solver eventually fills the order.

### Proof of Concept
1. Governance initializes `IntentGatewayV2` with `surplusShareBps = 0` (100% of surplus to the beneficiary) — see `Params` in [5](#0-4) /`IntentsBase.sol`.
2. User calls `placeOrder`, escrowing input tokens; commitment hashes `protocolFeeBps` (locked) but not `surplusShareBps`.
3. Before the solver fills, governance calls `onAccept(UpdateParams)` (a normal operational fee update) setting `surplusShareBps = 10_000` (100% to protocol): [6](#0-5) .
4. Solver fills the order and deliberately overpays (or overpays incidentally); `_fillSameChain`/`_fillCrossChain` computes `protocolShare = (dust * _params.surplusShareBps) / 10_000` using the *new* 100% rate: [7](#0-6) .
5. The beneficiary receives none of the surplus they would have received under the rate in effect when they placed the order, even though nothing about their own order or commitment changed.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L0-0)
```text

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L108-119)
```text
            uint256 dust = solverAmount - totalRequired;
            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;

            if (dust > 0) {
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L532-568)
```text
    function _validateParams(Params memory p) internal view {
        if (p.host == address(0) || p.host.code.length == 0) revert InvalidInput();
        if (p.dispatcher == address(0) || p.dispatcher.code.length == 0) revert InvalidInput();
        if (p.surplusShareBps > 10_000) revert InvalidInput();
        if (p.protocolFeeBps >= 10_000) revert InvalidInput();
        if (p.priceOracle != address(0) && p.priceOracle.code.length == 0) revert InvalidInput();
    }

    /**
     * @dev Updates the gateway's configuration parameters and per-destination protocol fees.
     * Called by Hyperbridge governance to modify fee settings, host address, dispatcher,
     * price oracle, and other operational parameters.
     *
     * Validates all params before applying. Emits ParamsUpdated with the old and new params,
     * then iterates over any destination-specific fee overrides and applies them to
     * `_destinationProtocolFees`.
     *
     * @param update The parameter update containing new params and destination fee overrides.
     */
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
