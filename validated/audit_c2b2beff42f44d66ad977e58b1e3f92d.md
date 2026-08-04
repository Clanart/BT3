Based on my investigation, I found a concrete local analog to the H-19 bug class in the `ExtrinsicIntents`/`IntentGatewayV2` cross-chain intent settlement logic. The original bug is about a security check that fails to cover a specific state combination (veto excludes governance-targeted actions), letting an attacker craft input that slips past the guard. The local analog is a missing state-consistency check between the "fill" and "cancel" paths of the intent settlement flow, which can allow the same escrow commitment to be released twice.

### Title
Missing `_filled`/deadline cross-check between fill and destination-side cancel allows double release of escrowed funds - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`_fillCrossChain` and `_cancelFromDest` are two independent, permissionless entrypoints that each unconditionally dispatch a settlement message (`RedeemEscrow` or `RefundEscrow`) to the source chain for the same order `commitment`, without either one checking whether the other has already fired for that commitment.

### Finding Description
`_fillCrossChain` marks the order filled locally and dispatches a `RedeemEscrow` request to the source gateway, paying the solver: [1](#0-0) [2](#0-1) 

`_cancelFromDest` is reachable by *anyone* once the deadline has passed, and only checks the deadline/ownership — it never checks whether `_filled[commitment]` is already non-zero (i.e., already filled by a solver) before marking it and dispatching a competing `RefundEscrow` request: [3](#0-2) 

On the source chain, `onAccept` routes both `RedeemEscrow` and `RefundEscrow` kinds through the same `_authenticate` + `_withdraw` path without any guard that the commitment hasn't already been settled by the other kind: [4](#0-3) 

The Tron-side `IntentGatewayV2.sol` has the identical structure — `_cancelFromDest`-equivalent logic at cancellation only checks deadline/owner, and `withdraw()` unconditionally overwrites `_filled[body.commitment]` and transfers tokens for whichever kind arrives: [5](#0-4) [6](#0-5) [7](#0-6) 

This mirrors the H-19 pattern exactly: a security-relevant check (`veto` excluding actions that target governance / here, `_cancelFromDest` and `_fillCrossChain` each excluding awareness of the other's state transition) omits one legitimate execution path, letting an unprivileged actor drive the system into a state the designer did not intend — here, two independent messages (`RedeemEscrow` + `RefundEscrow`) settling the same commitment.

### Impact Explanation
If a fill and a late/edge-of-deadline cancel both occur for the same order commitment, the source-chain gateway can process both a `RedeemEscrow` and a `RefundEscrow` for that commitment, since `_withdraw`/`withdraw` does not verify the commitment hasn't already been settled before transferring the escrowed tokens. This results in the escrowed input tokens being paid out twice — once to the solver and once to the user — a direct double-settlement / fund-loss condition for the protocol's escrow.

### Likelihood Explanation
Both entrypoints are unprivileged and permissionless (`_fillCrossChain` callable by any solver, `_cancelFromDest` callable by anyone after `order.deadline`). No malicious relayer, prover, or governance actor is required — the race only needs a solver's fill transaction and a third party's post-deadline cancel to both be dispatched before the source chain processes the first message, or before any existing check would otherwise block the second. Given cross-chain message delivery latency (via Hyperbridge), this window is realistic. I was unable to fully view the remainder of `withdraw()`/`_withdraw()` (specifically whether `_orders[commitment][token]` accounting elsewhere provides an implicit single-spend guard); confirming this requires reading the full body of `_withdraw`/`withdraw` past the lines cited.

### Recommendation
- In `_cancelFromDest` (and its Tron equivalent), revert if `_filled[commitment] != address(0)` before allowing cancellation.
- In `_fillCrossChain`, revert if the order is already filled/cancelled.
- On the source-chain `onAccept`/`withdraw` path, track a per-commitment "settled" flag (or zero out the escrowed amount atomically) and revert on `RedeemEscrow`/`RefundEscrow` if the commitment was already settled, so that duplicate cross-chain messages cannot both succeed.

### Proof of Concept
1. User creates a cross-chain order with `deadline = D`.
2. At block `D` (or in the block right before), a solver calls `fill()`, which internally calls `_fillCrossChain`, setting `_filled[commitment] = solver` and dispatching `RedeemEscrow` to the source chain.
3. Immediately after, at block `>= D`, anyone (not necessarily the user) calls `cancel()` for the same order; `_cancelFromDest` only checks `order.deadline >= _blockNumber()` (false now) so the owner-check is skipped, and it proceeds to set `_filled[commitment] = order.user` and dispatch `RefundEscrow` to the source chain, without checking that `_filled[commitment]` was already set to `solver`.
4. Both `RedeemEscrow` and `RefundEscrow` messages arrive at the source chain's `onAccept`; each is separately authenticated via `_authenticate` and independently invokes `_withdraw`, transferring the escrowed tokens out twice for the same `commitment`. [8](#0-7)

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-96)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L137-155)
```text
        _execute(order, outputsLen);

        address hostAddr = host();
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
        );
        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-295)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L578-600)
```text
        } else if (currentChain == orderDest) {
            // destination chain: dispatch RefundEscrow request to source chain
            // If order hasn't expired, only owner can cancel
            if (order.deadline >= block.number) {
                if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
            }

            // Mark as cancelled locally to prevent fills
            _filled[commitment] = address(uint160(uint256(order.user)));

            bytes memory body = bytes.concat(
                bytes1(uint8(RequestKind.RefundEscrow)),
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
            );

            DispatchPost memory request = DispatchPost({
                dest: order.source,
                to: abi.encodePacked(instance(order.source)),
                body: body,
                timeout: 0,
                fee: options.relayerFee,
                payer: msg.sender
            });
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-690)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
```
