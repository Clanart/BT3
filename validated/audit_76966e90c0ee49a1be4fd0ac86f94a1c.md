## Analysis

The external report's core broken invariant: **an unvalidated custody asset that a third party can freeze causes escrowed funds to become permanently stuck**, because the settlement path has no fallback around a blocked transfer.

Hyperbridge's `IntentGatewayV2`/`IntentsBase` contracts reproduce the exact same broken invariant for arbitrary ERC-20 order inputs.

### Title
Unvalidated ERC-20 Input Tokens Allow Permanent Escrow Lock via Atomic `_withdraw` Loop - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`placeOrder` accepts any ERC-20 address as an order input with no restriction on token behavior (blacklist/pause-capable tokens like USDC/USDT are fully permitted). Escrow release for a whole order is processed by `_withdraw`, which iterates every token in the withdrawal batch and calls `IERC20(token).safeTransfer(beneficiary, amount)` in a single atomic loop with no per-token isolation. If any single token transfer reverts — e.g., because a centralized issuer blacklists the beneficiary address or pauses the token after the order was placed — the entire `onAccept`/`onGetResponse` call reverts, permanently blocking release of *every* token escrowed for that order, not just the problematic one.

### Finding Description
`_withdraw` in [1](#0-0)  loops over `body.tokens` and performs `IERC20(token).safeTransfer(beneficiary, amount)` for each entry with no try/catch or per-token failure isolation. This function is invoked exclusively from `onAccept` (for `RedeemEscrow`/`RefundEscrow`) and `onGetResponse`, both gated by `onlyHost`, as shown in [2](#0-1)  and [3](#0-2) .

For a cross-chain fill, `_fillCrossChain` sets `_filled[commitment] = msg.sender` on the **destination** chain before dispatching `RedeemEscrow` back to the **source** chain, as seen in [4](#0-3) . If the `RedeemEscrow` message's `onAccept` call on the source chain reverts because one escrowed input token refuses to transfer to the solver (blacklist/pause), the source-chain escrow is never released and `_filled` on the source chain is never set.

Crucially, the user's alternate recovery path, `_cancelFromSource`, dispatches a GET request that checks the destination chain's `_filled` slot for the commitment: [5](#0-4) . Since the destination already marked the order filled (`_filled[commitment] = msg.sender` was set at fill time), `onGetResponse` sees a non-empty slot and reverts with `Filled()`: [3](#0-2) . There is no other function that releases order-scoped escrow outside `_withdraw`; `_sweepDust` only handles protocol-owned dust, not order escrow, per [6](#0-5) .

This directly parallels the ONFT report: an unvalidated asset property (freeze/blacklist authority controlled by a third party outside the protocol) combines with an all-or-nothing transfer path to permanently lock protocol-custodied funds, with no admin/governance recovery route reachable through the contract's own logic.

### Impact Explanation
Once a cross-chain order includes an input token capable of blacklisting/pausing a specific address, and that address (the eventual solver beneficiary) is later blacklisted or the token is paused before settlement completes:
- The solver has already delivered output tokens to the user on the destination chain (irreversible).
- The source-chain escrow release (`RedeemEscrow`) permanently reverts on `onAccept`.
- The cancellation path is blocked because the destination `_filled` slot is already non-empty (`Filled()`).
- All tokens escrowed for that order — not only the blacklist-affected token — are permanently stuck, since `_withdraw` reverts atomically for the whole batch.

This is a direct loss/permanent lock of bridged custody funds with no recovery path, matching the bounty's "stealing or loss of funds" / logic-attack impact category.

### Likelihood Explanation
No malicious relayer, prover, governance actor, or leaked key is required. All that's needed is:
1. A user places an order using a token whose issuer can restrict specific addresses or pause transfers (common with major stablecoins).
2. That solver/beneficiary address becomes restricted by the *token issuer* (an ordinary, expected real-world event, not a Hyperbridge-controlled actor) between fill and settlement.

Given intent-based swaps are explicitly designed to support arbitrary tokens and open solver participation, this scenario is realistic and requires no protocol-level compromise.

### Recommendation
- Isolate per-token transfer failures in `_withdraw` (e.g., wrap each transfer so a single failing token doesn't block release of the others in the same batch), or process withdrawals per-token rather than as one atomic call.
- Provide a governance/admin recovery mechanism for orders whose settlement is permanently stuck due to an individual token's transfer failure, so unaffected tokens/escrow are not held hostage.
- Consider flagging or restricting known blacklist/pause-capable tokens at `placeOrder` time, or documenting this risk explicitly for integrators, echoing the original report's recommendation to allowlist/vet tokens with third-party freeze capability.

### Proof of Concept
1. User places a cross-chain order (`placeOrder`) on chain A with `order.inputs = [TokenA (normal ERC20), TokenB (blacklist-capable, e.g., USDC-like)]`, output on chain B.
2. Solver fills the order on chain B via `_fillCrossChain`; `_filled[commitment] = solver` is set on chain B, and `RedeemEscrow` is dispatched to chain A.
3. Before the `RedeemEscrow` message is delivered/processed on chain A, TokenB's issuer blacklists the solver's address (or pauses the token) — an action entirely outside Hyperbridge's control.
4. Relayer submits the `RedeemEscrow` proof; `onAccept` on chain A calls `_withdraw`, which reverts when transferring TokenB to the blacklisted solver, reverting the entire call (including the TokenA release).
5. User attempts `_cancelFromSource`; the GET response check finds `_filled` on chain B already non-empty and reverts with `Filled()`.
6. Both TokenA and TokenB remain permanently escrowed on chain A with no function in `IntentsBase`/`ExtrinsicIntents`/`IntrinsicIntents` able to release them.

### Citations

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L579-597)
```text
    function _sweepDust(SweepDust memory req) internal {
        uint256 outputsLen = req.outputs.length;
        for (uint256 i; i < outputsLen;) {
            TokenInfo memory info = req.outputs[i];
            address token = address(uint160(uint256(info.token)));
            uint256 amount = info.amount;

            if (token == address(0)) {
                (bool sent,) = req.beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(req.beneficiary, amount);
            }
            unchecked {
                ++i;
            }
            emit DustSwept(token, amount, req.beneficiary);
        }
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-96)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L319-324)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
```
