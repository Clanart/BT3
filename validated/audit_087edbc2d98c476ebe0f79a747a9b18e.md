This is a confirmed local analog. The `FeeDistributor` bug class (a beneficiary that can't accept ether permanently blocks all future withdrawals from the shared pool) maps directly onto `IntentsBase._withdraw` in the `IntentGatewayV2` app, where escrow release/refund uses a raw `.call{value: amount}("")` to `order.user` (the order creator, an address the *attacker fully controls* when they create the order) with no fallback path.

### Title
Permanent escrow lock via non-accepting `beneficiary` in `IntentsBase._withdraw` - (File: evm/src/apps/intentsv2/IntentsBase.sol)

### Summary
`IntentsBase._withdraw` releases escrowed native-token funds to `beneficiary` with `beneficiary.call{value: amount}("")` and reverts the entire transaction (`InsufficientNativeToken`) if the call fails. [1](#0-0) 
Because `order.user` (the future refund `beneficiary`) is chosen by the same account that creates the order, and cancellation flows (`_cancelFromDest`, `onGetResponse`) always send the refund to `order.user`, a user can create an order from a contract address that reverts on receiving ETH.

### Finding Description
- On cross-chain cancel, `_cancelFromDest` builds a `RefundEscrow` message with `beneficiary: order.user` and dispatches it to the source chain gateway; the destination `onAccept` handler calls `_withdraw(body, true, true)` unconditionally. [2](#0-1) [3](#0-2) 
- On source-initiated cancel, `_cancelFromSource` performs a GET request and `onGetResponse` calls `_withdraw(body, true, true)` with `beneficiary: order.user` once the storage proof confirms the order was never filled. [4](#0-3) 
- `_withdraw` decrements the `_orders[commitment][token]` escrow accounting *before* the external call, then reverts the whole transaction if the native transfer fails — this is the exact broken invariant from the `FeeDistributor` report: a single non-accepting beneficiary blocks the whole withdrawal path, and because the revert unwinds state, the escrow is never actually released or re-attempted successfully. [5](#0-4) 
- There is no fallback re-wrap path here (unlike `WrappedHyperFungibleToken.onAccept`, which retries via WETH wrapping when the native push fails) [6](#0-5) , and no alternate beneficiary or pull-based claim mechanism exists in `IntentGatewayV2`/`IntentsBase`.

### Impact Explanation
Any order whose `order.user` is set to a contract with no `receive()`/payable `fallback()` (or one that intentionally reverts/griefs on `call`) will permanently lock its native-token escrow inside the `IntentGatewayV2` instance once a cancellation or refund path is triggered: `RefundEscrow`, `_cancelFromSource`→`onGetResponse`, and `_cancelFromDest` all route to the same `_withdraw` function with no alternative delivery mechanism. Funds become permanently unrecoverable (loss of funds) for that specific commitment, since every retry hits the same non-payable beneficiary and reverts identically.

### Likelihood Explanation
High feasibility for an unprivileged attacker: `order.user` is attacker-supplied at order-creation time (no privileged actor, relayer, or prover assumption required), and simply setting it to a minimal reverting contract is sufficient to trigger the lock on that order's escrow whenever a refund/cancel path executes. No malicious peer, governance, or leaked-key requirement.

### Recommendation
Do not let a failed native transfer revert loss of escrow accounting for the whole order: either (a) mirror the `WrappedHyperFungibleToken` pattern by wrapping stuck ETH (e.g. to WETH) and delivering ERC-20 on native-send failure, or (b) switch to a pull-based escrow-claim pattern (credit an internal balance mapping on failure, let the beneficiary/user call a separate `claim()` later), so a single non-accepting beneficiary cannot lock the associated escrow indefinitely.

### Proof of Concept
1. Attacker deploys `EvilBeneficiary` with `receive() external payable { revert(); }`.
2. Attacker creates a cross-chain order via `IntentGatewayV2` with `order.user = address(EvilBeneficiary)` and native-token `inputs`.
3. Attacker (or anyone after deadline) calls `_cancelFromDest`/`_cancelFromSource`, driving a `RefundEscrow` message or GET response back to the source gateway.
4. `onAccept`/`onGetResponse` calls `_withdraw(body, true, true)`, which attempts `beneficiary.call{value: amount}("")` to `EvilBeneficiary`; the call always reverts, so `_withdraw` reverts with `InsufficientNativeToken` every time it is invoked. [7](#0-6) 
5. The escrowed native tokens for that commitment remain locked in the gateway contract permanently — no other function path can release them.

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-250)
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L319-324)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L309-321)
```text
        if (_isWeth) {
            // Try a native-ETH push first (cheap for EOAs and payable contracts);
            // if the recipient cannot accept native value (no `receive()` / `fallback()
            // payable`), re-wrap the withdrawn ETH and deliver the underlying WETH as
            // an ERC-20 transfer instead. This mirrors the deposit-side flexibility of
            // `send()` (which accepts WETH from non-payable callers via `safeTransferFrom`)
            // so the refund path doesn't permanently lock funds for the same caller class.
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = beneficiary.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(beneficiary, message.amount);
            }
```
