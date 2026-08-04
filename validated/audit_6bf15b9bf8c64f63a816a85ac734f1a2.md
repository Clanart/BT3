Confirmed: `placeOrder` accepts arbitrary token addresses for `order.inputs` with no whitelist, matching the exact "arbitrary ERC20 implementation" primitive from the source report.

### Title
Malicious input-token ERC20 chosen by an order creator can permanently trap a solver's real assets on cross-chain fills - (File: evm/src/apps/intentsv2/ExtrinsicIntents.sol)

### Summary
`IntentGatewayV2.placeOrder()` lets any user escrow an arbitrary ERC20 as `order.inputs[i].token` with no whitelist check [1](#0-0) . On a cross-chain fill, the solver delivers real output tokens directly to the beneficiary on the destination chain, and only afterward does the source chain attempt to pay the solver by transferring the escrowed input tokens via `_withdraw` [2](#0-1) [3](#0-2) . If the user escrows a malicious ERC20 that unconditionally reverts on `transfer`, the `RedeemEscrow` message's `onAccept` → `_withdraw` call always reverts, so the solver's payment can never be collected — while the solver already gave away real value on the destination chain in the same fill.

### Finding Description
This is the same broken invariant as the external report: an unprivileged actor supplies an attacker-controlled ERC20 implementation as the payment/settlement asset, and the payout logic assumes `transfer`/`transferFrom` behave per the standard (return success or revert only on legitimate failure). In Hyperbridge's Intent Gateway:

1. `placeOrder` escrows `order.inputs[i].token` for any `address` cast from `bytes32`, with zero validation that it is a well-behaved ERC20 [4](#0-3) .
2. For a cross-chain order, `fillOrder` → `_fillCrossChain` first pays the beneficiary with the solver's real `output` tokens (`safeTransferFrom` from `msg.sender` to `beneficiary`), then dispatches a `RedeemEscrow` `PostRequest` back to the source chain naming the solver as the intended recipient of the escrowed `order.inputs` [5](#0-4) .
3. When that message lands on the source chain, `onAccept` calls `_withdraw(body, false, true)` which does `IERC20(token).safeTransfer(beneficiary, amount)` for every escrowed input token, with no failure isolation per token [6](#0-5) [7](#0-6) .
4. Because the malicious token always reverts `transfer`, this call — and therefore the entire delivery of the `RedeemEscrow` message — reverts every time it is attempted, with no alternate recovery path for the solver to claim by other means. There is no per-token skip/quarantine, and no mechanism (as recommended in the original report) for a party to force settlement or void the withdrawal request without touching the malicious token's `transfer`.

The order creator fully controls `order.inputs`, and the destination-side value transfer to the beneficiary happens unconditionally and irreversibly before the source-side payout is even attempted — there is no atomicity between "solver pays real value" and "solver gets paid."

### Impact Explanation
This is a direct loss-of-funds vector for solvers, an unprivileged, non-relayer/non-admin party: a malicious user places a cross-chain order with real desired output tokens but a poisoned ERC20 as the escrowed input. A solver, seeing a legitimate-looking order (correct amounts, standard-looking token address), fills it and irrevocably sends real assets to the beneficiary on the destination chain. The solver's compensation call on the source chain (`_withdraw` via `onAccept`) then permanently reverts, so the solver never recovers anything, and the malicious escrow sits stuck in the contract forever (also a permanent fund lock on the input side). This matches the bounty's "stealing or loss of funds" and "bridged assets ... must move exactly once and only to the rightful beneficiary and amount" criteria, since here the intended beneficiary (the solver) never receives their compensation at all, causing an asymmetric, uncompensated fund flow to the malicious order creator.

### Likelihood Explanation
Likelihood is high for opportunistic attackers: creating a malicious ERC20 that reverts on `transfer` is trivial and cheap, and `placeOrder` performs no allow-listing or safety probing of the input token before accepting it into escrow. The only defense against this is solver due diligence (verifying token bytecode/behavior off-chain before filling), which is not enforced by the protocol and is exactly the class of "generic ERC20 trust" issue the original report calls out. No relayer collusion, prover compromise, or governance action is needed — a single malicious `placeOrder` call plus a solver willing to fill it is sufficient.

### Recommendation
- Short term: Decouple output delivery from input payout ordering risk by allowing a solver (or a permissioned relayer of the solver) to reclaim/void a `RedeemEscrow`/`WithdrawalRequest` that has proven undeliverable, without depending on a successful call into the malicious token, e.g., an alternate "mark abandoned, sweep to a claims registry" path keyed by commitment, mirroring the original recommendation to let a party close out a stuck settlement without calling the malicious `_currency`/token contract.
- Consider isolating token transfers inside `_withdraw`'s loop with try/catch (or a pull-based claim per token) so one poisoned token cannot block payout of the other, well-behaved tokens in the same order, and so a stuck token doesn't block finalization/marking of `_filled`.
- Long term: require or optionally support a per-deployment ERC20 allow-list (or a lightweight standardness probe) for `order.inputs` tokens, especially given the destination-side payment to the beneficiary is irreversible and precedes the source-side solver payout.

### Proof of Concept
1. Attacker deploys `EvilERC20` implementing `IERC20` where `transfer` always `revert()`s (or reverts only when the caller is not the deployer), but `transferFrom`/`balanceOf` behave normally so the token passes `placeOrder`'s escrow accounting [8](#0-7) .
2. Attacker calls `placeOrder` on the source chain with `order.inputs = [{token: EvilERC20, amount: X}]` and `order.output.assets` set to a legitimate, valuable token (e.g., USDC) on the destination chain, with a normal deadline.
3. A solver observes the order, calls `fillOrder` on the destination chain; `_fillCrossChain` transfers real USDC to the beneficiary and dispatches `RedeemEscrow` back to source [5](#0-4) .
4. When the `RedeemEscrow` message is delivered and `onAccept` executes `_withdraw`, the `IERC20(EvilERC20).safeTransfer(solver, X)` call reverts every time, so the solver's compensation transaction always fails [9](#0-8) .
5. Result: the solver has permanently given away real USDC and can never claim the escrowed `EvilERC20` compensation; the escrow remains locked in the source contract indefinitely.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L162-298)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
        if (order.inputs.length == 0) revert InvalidInput();

        // Reject duplicate output tokens 
        uint256 outputsLen_ = order.output.assets.length;
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                if tload(token) {
                    mstore(0, 0xb4fa3fb3) // InvalidInput.selector
                    revert(0x1c, 0x04)
                }
                tstore(token, 1)
            }
            unchecked {
                ++i;
            }
        }
        // Clean up transient storage so repeated placeOrder calls in the same tx don't false-positive.
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                tstore(token, 0)
            }
            unchecked {
                ++i;
            }
        }

        address hostAddr = host();
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;

        uint256 inputsLen = order.inputs.length;

        // Phase 1: Transfer tokens and record actual received amounts.
        // For fee-on-transfer tokens, the gateway receives less than the requested amount.
        // We mutate order.inputs to reflect actual received so the commitment and escrow
        // are consistent with what the gateway holds.
        uint256 msgValue = msg.value;
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;
                if (amount == 0) revert InvalidInput();

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Build sweep calls and snapshot gateway balances before the sweep.
            Call[] memory transferCalls = new Call[](inputsLen);
            uint256[] memory balancesBefore = new uint256[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;

                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
                } else {
                    uint256 balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    balancesBefore[i] = IERC20(token).balanceOf(address(this));
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));

            // Measure actual received, emit dust for excess, update order.inputs.
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 received;
                if (token == address(0)) {
                    received = address(this).balance - balancesBefore[i];
                } else {
                    received = IERC20(token).balanceOf(address(this)) - balancesBefore[i];
                }

                if (received > order.inputs[i].amount) {
                    uint256 dust = received - order.inputs[i].amount;
                    emit DustCollected(token, dust);
                } else {
                    order.inputs[i].amount = received;
                }

                unchecked {
                    ++i;
                }
            }
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L121-155)
```text
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
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
```
