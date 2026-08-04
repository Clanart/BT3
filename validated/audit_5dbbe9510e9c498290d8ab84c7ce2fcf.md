## Title
`IntentGatewayV2.withdraw()` on Tron permanently reverts (and locks all escrow) for orders containing a legitimately zero-amount input token — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron port of `IntentGatewayV2` dropped a zero-amount guard that exists in the canonical EVM contract (`IntentsBase._withdraw`). Because `placeOrder`'s predispatch branch allows an input token to be escrowed with `amount == 0`, and `withdraw()` unconditionally reverts whenever `_orders[commitment][token] == 0`, any order that legitimately has a zero-balance input token can never be settled: fill payout, refund, and cancellation all call `withdraw()` with the same `body.tokens = order.inputs` list, so the transaction always hits the zero-balance token and reverts. This is the same bug class as the reported perpetual-contract issue: a health/invariant check that treats a legitimate "already at zero" state as an error, permanently blocking the only code path that can resolve the position (there, liquidation; here, escrow withdrawal), and freezing funds.

### Finding Description
`placeOrder` has two escrow paths. The plain path enforces a non-zero amount: [1](#0-0) 

The **predispatch** path (used when an order carries pre-fill calldata/assets) has no such check — an input token can be escrowed with `reducedInputs[i].amount == 0`: [2](#0-1) 

`withdraw()`, which is the single function used for fills (`onAccept` → `RedeemEscrow`), refunds (`onAccept` → `RefundEscrow`), same-chain cancellation, and cross-chain cancellation confirmation (`onGetResponse`), reverts unconditionally on any token whose escrow balance is zero — with no exemption for a legitimately zero `amount`: [3](#0-2) 

Every call site passes `order.inputs` (the full original token list, including any zero-amount tokens) as `body.tokens`:
- `onAccept` for `RedeemEscrow`/`RefundEscrow`: [4](#0-3) 
- Same-chain `cancelOrder`: [5](#0-4) 
- Source-chain `cancelOrder` pre-check (also reverts with `UnknownOrder` on a zero-balance token before even dispatching the proof query): [6](#0-5) 
- `onGetResponse` (destination-proven cancellation): [7](#0-6) 

The canonical EVM implementation guards exactly this case with `if (amount == 0) continue;` before checking `escrowed == 0`, which the Tron contract is missing: [8](#0-7) 

Because `_orders[commitment][token]` for that token is, and always will be, `0` (it was placed at `0` and legitimately never needs redemption), `withdraw()` reverts every single time it is invoked for that order — there is no future state transition that makes the check pass, since nothing ever adds balance to that token slot.

### Impact Explanation
Every settlement path for the affected order is blocked permanently:
- The solver can never receive escrowed payout (`RedeemEscrow` reverts).
- The user can never cancel and recover the other, non-zero escrowed input tokens (`RefundEscrow`, same-chain cancel, and the source-chain proof-based cancel pre-check all revert).
- All non-zero-amount tokens/fees legitimately escrowed for that order become permanently locked in the contract with no recovery path, matching the "toxic position that can never be resolved" impact pattern from the seed report, translated to bridge fund custody (loss/lock of user and solver funds).

### Likelihood Explanation
Any order placed through the predispatch path (calldata + asset prelude) that includes even one token whose `TokenInfo.amount` is `0` — a state the contract itself permits since it only validates non-zero amounts on the non-predispatch path — triggers this deterministically and unconditionally on every subsequent `withdraw()` call. No malicious relayer, prover, or admin is required; a normal user (or one interacting with a dApp/aggregator that builds `Order.inputs` including a zero-amount placeholder entry, e.g., to represent an asset consumed entirely by predispatch and re-added later) can construct this state via the standard, permissionless `placeOrder` entrypoint.

### Recommendation
Add the same zero-amount skip used in the canonical `IntentsBase._withdraw` before checking/decrementing escrow balances in `evm/tron/contracts/apps/IntentGatewayV2.sol::withdraw`:
```solidity
uint256 amount = body.tokens[i].amount;
if (amount == 0) { unchecked { ++i; } continue; }
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
```
Apply the equivalent fix to the source-chain `cancelOrder` pre-check loop (skip the `UnknownOrder` check for tokens whose original `order.inputs[i].amount == 0`).

### Proof of Concept
1. Call `placeOrder` with `order.predispatch.call.length > 0` and `order.predispatch.assets.length > 0`, and `order.inputs` containing at least two tokens: token A with a normal non-zero amount and token B with `amount == 0`.
2. `_orders[commitment][tokenB]` is stored as `0` (line 435, `reducedInputs[i].amount == 0`).
3. Have a solver fill the order cross-chain so the destination dispatches `RedeemEscrow` back to source, or have the user attempt same-chain/cross-chain cancellation.
4. `withdraw()` iterates `body.tokens == order.inputs`; when it reaches token B, `_orders[commitment][tokenB] == 0` triggers `revert UnknownOrder()` — for source-chain cancel, the identical check at line 543 reverts even before the proof round-trip.
5. The transaction reverts every time it is retried, for both the solver's redeem path and the user's refund/cancel paths — token A's escrow (and any fees) can never be released.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L383-440)
```text
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            // Transfer all predispatch assets to the call dispatcher
            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;

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

            // Execute the call dispatcher with predispatch call
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-463)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L519-530)
```text
        if (isSameChain) {
            // Same-chain: validate locally and refund immediately
            // only owner can cancel
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

            // Verify we're on the correct chain
            if (orderSource != currentChain) revert WrongChain();

            WithdrawalRequest memory body =
                WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user});

            withdraw(body, true);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L540-548)
```text
            uint256 inputsLen = order.inputs.length;
            for (uint256 i; i < inputsLen;) {
                // check for order existence
                if (_orders[commitment][address(uint160(uint256(order.inputs[i].token)))] == 0) revert UnknownOrder();

                unchecked {
                    ++i;
                }
            }
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
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
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-401)
```text
        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();
```
