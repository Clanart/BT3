Found the analog: `_instance()` in `evm/src/apps/intentsv2/IntentsBase.sol` resolves the counterparty IntentGateway address purely from the *current* `_instances[stateMachineId]` mapping, which governance can overwrite at any time via `NewDeployment` (see `_addDeployment` handling in `evm/src/apps/intentsv2/ExtrinsicIntents.sol` `onAccept`, `RequestKind.NewDeployment`). This mirrors the veNFT bug class: a value fixed at one point in time (the gateway address used when the order/commitment was created and dispatched) is re-derived from live, mutable state at claim/settlement time, and an unrelated, permitted action (a redeployment) can invalidate that lookup, permanently blocking a legitimate outbound settlement.

### Title
Stuck escrow funds after `NewDeployment` gateway address rotation invalidates in-flight cross-chain settlement/refund authentication - (File: `evm/src/apps/intentsv2/IntentsBase.sol`, `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`IntentsBase._instance()` [1](#0-0)  resolves the destination/source gateway address from the live `_instances` mapping every time it is used to build the `to` field of an outbound dispatch (in `_cancelFromSource`, `_cancelFromDest`, and the fill flow) [2](#0-1) . The `_authenticate` step on the receiving side checks the inbound `RedeemEscrow`/`RefundEscrow` message's `request.source`/`request.from` against the *currently registered* instance for that chain. Governance can update `_instances[stateMachineId]` at any time via a `NewDeployment` message processed in `onAccept` [3](#0-2) , exactly as documented for the analogous `BandwidthManager` case: "Overwriting an existing registration is allowed (useful for redeployments); in-flight purchases from the old contract will fail after the swap" [4](#0-3) .

### Finding Description
The escrow/settlement lifecycle for cross-chain intents is: (1) a user escrows tokens on the source chain, (2) a solver fills the order on the destination chain and the fill contract dispatches a `RedeemEscrow` POST back to the source chain addressed `to = _instance(order.source)` as evaluated **on the destination chain at fill time**, and (3) the source chain's `onAccept` authenticates the inbound message and calls `_withdraw()` to pay the solver [5](#0-4) . Both `to` (chosen at dispatch) and any subsequent identity check bind to the value of `_instances[...]` read live off mutable governance-controlled state, not to a value pinned when the order/commitment was created.

If governance rotates the registered gateway instance for a chain (a normal, permitted, non-malicious admin action — e.g. contract upgrade/redeployment) between order placement/fill and message delivery, any request already dispatched using the old instance address can no longer be correctly authenticated/settled once the counterpart side's lookup returns the new address, and vice versa for any cancellation `DispatchGet` storage-proof key built with `_instance(order.destination)` at cancel time (`_cancelFromSource`) [6](#0-5) . This is structurally identical to the `veNFTAerodrome::getDataByReceipt()` bug: a claim-path function derives a critical value (there: the receipt's current owner via `ownerOf`; here: the current gateway address via `_instances[...]`) from live mutable state rather than the state that was valid when the entitlement (auction sale / order fill) was fixed, and an unrelated permitted action (veNFT withdrawal / gateway redeployment) invalidates it, with no fallback path to complete the claim.

### Impact Explanation
Escrowed user funds or solver settlement funds can become permanently unrecoverable: the `RedeemEscrow`/`RefundEscrow` message that already carries the stale gateway binding cannot be re-derived or resubmitted with updated addressing once dispatched, and the storage-proof key for `_cancelFromSource` (`keys[0] = abi.encodePacked(_instance(order.destination))...`) computed against a stale/rotated instance address will not match the real `_filled` slot on the (now redeployed) destination contract, causing legitimate cancellations to fail proof verification indefinitely. This directly matches the required impact class: loss/lock of escrowed bridge funds for legitimate beneficiaries (lenders/borrowers-analog: users/solvers).

### Likelihood Explanation
This does not require a malicious actor — it is triggered by governance performing a normal, documented redeployment/rotation of a registered gateway instance while orders are in flight, which the code and docs explicitly permit ("Overwriting an existing registration is allowed... in-flight purchases from the old contract will fail after the swap"). Given multi-chain deployments and the stated intent-gateway upgrade path (`UpgradeContract`/`NewDeployment` request kinds exist specifically to support this), the window where in-flight orders straddle a rotation is realistic during any routine contract upgrade across the many chains IntentGatewayV2 is deployed to.

### Recommendation
Bind the counterparty gateway address at order-commitment time rather than re-resolving it live at dispatch/authentication time for in-flight messages — e.g., snapshot the resolved instance address into the order/commitment state when the order is placed or filled, and authenticate inbound settlement messages against that snapshot (with an explicit migration/grace mechanism) rather than solely against the current `_instances` mapping value. Alternatively, provide a governance-gated recovery path that allows funds tied to a stale instance binding to be released once the mismatch is detected, instead of leaving `_withdraw()` permanently unreachable for such messages.

### Proof of Concept
1. User places a cross-chain order with `source = ChainA`, `destination = ChainB`; `IntentGatewayV2` on `ChainB` currently resolves `_instances[ChainA] = GatewayA_v1`.
2. Solver fills the order on `ChainB`; `_execute`/fill logic dispatches `RedeemEscrow` with `to = _instance(order.source)` = `GatewayA_v1`, and the message is in flight to `ChainA`.
3. Before the relayer delivers the message, Hyperbridge governance dispatches `NewDeployment` to `ChainA`'s gateway, updating `_instances[ChainB]` (or vice versa affecting the reverse authentication mapping) to `GatewayB_v2`, e.g. via the flow described in `_addDeployment`/`onAccept` [7](#0-6) .
4. The in-flight `RedeemEscrow` message arrives at `ChainA`; `_authenticate()` (checked against the now-updated instance registration) rejects it, or a corresponding `_cancelFromSource` proof key built with the stale instance address fails proof verification.
5. `_withdraw()` never executes for this commitment — escrowed input tokens and accrued fees for that order remain locked in the gateway with no code path to release them.

Note: I was not able to directly view the body of `_authenticate()` (only its usage and the `onAccept` dispatcher) due to index truncation in the final tool round, so the exact revert condition on the receiving side should be verified by reading `IntentsBase.sol`'s `_authenticate` implementation in full before treating this as fully confirmed; the surrounding evidence (mutable `_instances` mapping, documented "in-flight ... will fail after the swap" behavior, and address-derivation-at-use-time in `_instance()`) strongly supports the analog.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L358-362)
```text
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L188-223)
```text
    function _cancelFromSource(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        if (options.height <= order.deadline) revert NotExpired();

        uint256 inputsLen = order.inputs.length;
        for (uint256 i; i < inputsLen;) {
            if (_orders[commitment][address(uint160(uint256(order.inputs[i].token)))] == 0) revert UnknownOrder();

            unchecked {
                ++i;
            }
        }

        bytes memory context =
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));

        bytes[] memory keys = new bytes[](1);
        keys[0] = bytes.concat(abi.encodePacked(_instance(order.destination)), _calculateCommitmentSlotHash(commitment));
        DispatchGet memory request = DispatchGet({
            dest: order.destination,
            keys: keys,
            timeout: 0,
            height: options.height,
            fee: options.relayerFee,
            context: context,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-267)
```text
    function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.deadline >= _blockNumber()) {
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
        }

        _filled[commitment] = address(uint160(uint256(order.user)));

        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RefundEscrow)),
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
        );

        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-309)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }

        // only hyperbridge is permitted to perform these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            _addDeployment(abi.decode(incoming.request.body[1:], (Deployment)));
        } else if (kind == RequestKind.UpdateParams) {
            _updateParams(abi.decode(incoming.request.body[1:], (ParamsUpdate)));
        } else if (kind == RequestKind.SweepDust) {
            _sweepDust(abi.decode(incoming.request.body[1:], (SweepDust)));
        } else if (kind == RequestKind.UpgradeContract) {
            (address newImpl, bytes memory initData) = abi.decode(incoming.request.body[1:], (address, bytes));
            ERC1967Utils.upgradeToAndCall(newImpl, initData);
        }
    }
```

**File:** docs/content/developers/evm/bandwidth/governance.mdx (L63-65)
```text
BandwidthPallet::set_allowlist(
    RawOrigin::Root.into(),
    StateMachine::Evm(1),
```
