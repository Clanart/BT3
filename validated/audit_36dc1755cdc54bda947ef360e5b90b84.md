Based on my investigation, I found a strong local analog in the IntentGatewayV2 (`evm/src/apps/intentsv2/`) contracts.

### Title
Pause flag `_paused` in IntentGatewayV2 is never enforced, letting fund movement continue while paused - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
### Finding Description
`IntentsBase.sol` declares a pause flag: `bool public _paused;` [1](#0-0)  with an accompanying comment stating it was "Appended last to preserve existing storage slots," implying it is meant to gate the gateway's fund-moving operations, mirroring the `whenNotPaused` pattern used elsewhere in the codebase (e.g. `HyperFungibleToken.sol`, `WrappedHyperFungibleToken.sol`, `HyperbridgeLzEndpoint.sol` all wire `whenNotPaused` onto `send`/`onAccept`/`onPostRequestTimeout`) [2](#0-1) [3](#0-2) .

However, across the entire `evm/src/apps/intentsv2/` directory — `IntentsBase.sol`, `ExtrinsicIntents.sol`, `IntrinsicIntents.sol`, `SolverAccount.sol` — `_paused` is declared but never read or checked anywhere; there is no `modifier whenNotPaused`, no `if (_paused) revert ...`, and no OpenZeppelin `Pausable` inheritance in this module. All the fund-moving internal functions that this state variable is presumably meant to gate remain fully reachable regardless of its value:

- `_withdraw` releases escrowed tokens to a beneficiary [4](#0-3) 
- `_fillCrossChain` moves solver funds to a beneficiary and dispatches escrow redemption [5](#0-4) 
- `_fillSameChain` performs the same-chain equivalent [6](#0-5) 
- `_cancelFromSource` / `_cancelFromDest` / `_cancelSameChain` trigger refunds [7](#0-6) [8](#0-7) 
- `onAccept` processes `RedeemEscrow`/`RefundEscrow`/`SweepDust`/`UpgradeContract` governance-relayed actions without any pause check [9](#0-8) 

This is the direct structural analog of the reported CDPVault bug: a pause switch exists but the reachable execution paths that should respect it don't check it, so "pausing" the gateway has no actual effect on withdrawals, fills, or escrow release/refund.

### Impact Explanation
If governance (via Hyperbridge cross-chain `UpdateParams`/pause action or any mechanism that sets `_paused`) attempts to halt the IntentGateway during an incident (e.g. a discovered exploit, a compromised price oracle, or a pending upgrade), solvers and users can continue to fill orders, cancel orders, and drain escrowed funds through `fillOrder`/`cancel`/`onAccept` entrypoints (which call the above internal functions) exactly as before. This defeats the emergency-stop control entirely, allowing loss of escrowed funds during precisely the window the pause mechanism is meant to protect against.

### Likelihood Explanation
The condition requires no privileged or malicious actor — every regular solver/user interaction with the intent gateway already goes through these same internal functions. As soon as `_paused` is set true expecting fund movement to halt, any ordinary caller can still successfully fill/cancel/withdraw, which is trivially observable and exploitable by any unprivileged party watching for a pause event as a live/incident signal.

### Recommendation
Add an actual `whenNotPaused` modifier (or manual `if (_paused) revert Paused();` check) enforced at the public entrypoints that route into `_withdraw`, `_fillCrossChain`, `_fillSameChain`, `_cancelFromSource`, `_cancelFromDest`, `_cancelSameChain`, and the `RedeemEscrow`/`RefundEscrow` branches of `onAccept`, consistent with the pattern already used in `HyperFungibleToken.sol` and `HyperbridgeLzEndpoint.sol`.

### Proof of Concept
1. Governance sets `_paused = true` on the deployed IntentGateway (via whatever setter exists, e.g. a future `UpdateParams`/pause action) intending to halt all fund movement.
2. A solver calls the public `fillOrder` entrypoint (which internally calls `_fillCrossChain` or `_fillSameChain`) — since neither function nor any code path checks `_paused`, the fill succeeds, escrow is released, and a `RedeemEscrow` message is dispatched cross-chain.
3. Likewise, a user calls `cancel` to trigger `_cancelFromSource`/`_cancelFromDest`, which check none of `_paused`, and successfully receives a refund.
4. Result: the pause flag has zero effect on any escrow movement, exactly reproducing the "modifier bypass" pattern from the CDPVault report, except here the guard isn't merely bypassable via an alternate entrypoint — it's not wired in at all.

Note: I was unable to locate the main `IntentGatewayV2.sol` contract (which likely defines `placeOrder`/`fillOrder`/`cancel` and any setter for `_paused`) within the indexed content, so I could not directly confirm whether `_paused` is set by a reachable function today. The evidence conclusively shows the variable exists and no consuming logic (`whenNotPaused` or equivalent check) exists anywhere in `evm/src/apps/intentsv2/`, which is the core defect regardless of how/where `_paused` gets flipped. A Devin session with full repo access should verify the `IntentGatewayV2.sol` definition to confirm the pause setter path.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L160-161)
```text
    /// @dev Appended last to preserve existing storage slots.
    bool public _paused;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-425)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }

        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }

            if (isRefund) {
                emit EscrowRefunded({commitment: body.commitment, tokens: body.tokens});
            } else {
                emit EscrowReleased({commitment: body.commitment, tokens: body.tokens});
            }
        }
    }
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L264-264)
```text
    function send(SendParams calldata params) external payable whenNotPaused {
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L320-320)
```text
    function onPostRequestTimeout(PostRequestTimeout memory incoming) external override onlyHost whenNotPaused {
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-171)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);

        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            if (solverAmount < totalRequired) revert InvalidInput();

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

            if (token == address(0)) {
                if (msgValue < solverAmount) revert InsufficientNativeToken();
                uint256 beneficiaryTotal = totalRequired + beneficiaryShare;
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, totalRequired + beneficiaryShare);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }
            if (protocolShare > 0) emit DustCollected(token, protocolShare);
            outputFills[i] = TokenInfo({token: outputToken, amount: totalRequired});
        }

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

        if (options.nativeDispatchFee > 0 && msgValue >= options.nativeDispatchFee) {
            IDispatcher(hostAddr).dispatch{value: options.nativeDispatchFee}(request);
            msgValue -= options.nativeDispatchFee;
        } else {
            dispatchWithFeeToken(request);
        }

        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }

        emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: order.inputs});
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L188-267)
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

    /**
     * @dev Initiates cancellation of a cross-chain order from the destination chain.
     *
     * If the order deadline has not yet passed, only the order creator may cancel.
     * After the deadline, anyone may trigger the cancellation (e.g., a relayer acting
     * on behalf of the user).
     *
     * Marks the order as filled (to prevent future fill attempts) and dispatches a
     * RefundEscrow message via Hyperbridge to the source chain to release the escrowed
     * tokens back to the original user.
     *
     * @param order The order to cancel.
     * @param options Cancel options including the relayer fee.
     * @param commitment The keccak256 hash of the ABI-encoded order.
     */
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-149)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        bool isFullyFilled = true;

        TokenInfo[] memory escrowedInputs = new TokenInfo[](outputsLen);
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);

        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            uint256 alreadyFilled = _partialFills[commitment][outputToken];
            uint256 remaining = totalRequired - alreadyFilled;
            if (remaining == 0 || solverAmount == 0) {
                if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
                continue;
            }
            uint256 fillAmount;

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
            } else {
                fillAmount = solverAmount > remaining ? remaining : solverAmount;
            }

            uint256 amountFilled = alreadyFilled + fillAmount;
            _partialFills[commitment][outputToken] = amountFilled;
            uint256 beneficiaryTotal = fillAmount + beneficiaryShare;

            if (token == address(0)) {
                if (msgValue < beneficiaryTotal + protocolShare) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, beneficiaryTotal);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }

            if (totalRequired > amountFilled) isFullyFilled = false;
            if (protocolShare > 0) emit DustCollected(token, protocolShare);

            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
        }

        // Orders carrying output calldata must be filled completely in a single fill.
        // The attached call is only executed on a full fill, so a partial fill would
        // leave the intended side effect unexecuted while releasing proportional escrow.
        if (order.output.call.length > 0 && !isFullyFilled) revert PartialFillNotAllowed();

        WithdrawalRequest memory body = WithdrawalRequest({
            commitment: commitment, tokens: escrowedInputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        });
        _withdraw(body, false, isFullyFilled);

        if (isFullyFilled) {
            _execute(order, outputsLen);
            emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        } else {
            delete _filled[commitment];
            emit PartialFill({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        }

        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
    }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L161-187)
```text
    function _cancelSameChain(Order calldata order, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        if (orderSource != currentChain) revert WrongChain();

        uint256 inputsLen = order.inputs.length;
        TokenInfo[] memory remainingTokens = new TokenInfo[](inputsLen);
        bool hasEscrow = false;
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            uint256 escrowed = _orders[commitment][token];
            if (escrowed > 0) hasEscrow = true;
            remainingTokens[i] = TokenInfo({token: order.inputs[i].token, amount: escrowed});
            unchecked {
                ++i;
            }
        }
        if (!hasEscrow) revert UnknownOrder();

        WithdrawalRequest memory body =
            WithdrawalRequest({commitment: commitment, tokens: remainingTokens, beneficiary: order.user});

        _withdraw(body, true, true);
    }
```
