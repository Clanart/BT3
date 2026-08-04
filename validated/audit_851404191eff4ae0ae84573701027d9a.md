## Analysis

**Core broken invariant (from the report):** closing a position triggers a call into external/hook logic; if that external call always reverts, the user's principal is push-transferred and gets stuck with no alternate exit path.

**Local analog found:** the Intent Gateway's escrow-release path (`_withdraw` in `IntentsBase.sol`, and its Tron mirror `withdraw` in `evm/tron/contracts/apps/IntentGatewayV2.sol`) settles escrowed funds with a single push transfer to a fixed `beneficiary` and provides no pull-based or redirectable fallback. [1](#0-0) 

### Title
Escrowed funds permanently locked when beneficiary transfer reverts (ERC20 blacklist/blocked receiver) with no emergency exit - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`_withdraw()` is the sole exit path for escrowed order funds, invoked both from `ExtrinsicIntents.onAccept()` (RedeemEscrow/RefundEscrow) and `onGetResponse()` (source-chain cancellation). It performs a direct native-ETH `call` or `IERC20.safeTransfer` to `beneficiary` and reverts the whole withdrawal if that transfer fails. There is no alternative "pull" mechanism, no ability to redirect to a different address, and no admin/emergency escape hatch for stuck escrow, unlike the protocol-dust `_sweepDust` path which only governs accumulated fees, not user principal. [2](#0-1) [3](#0-2) 

### Finding Description
When an order is filled or cancelled, `onAccept`/`onGetResponse` decode a `WithdrawalRequest` and call `_withdraw(body, isRefund, true)`, which transfers `order.inputs`/output tokens directly to `beneficiary` (typically `order.user`): [1](#0-0) 

If `token` is an ERC20 with transfer-blocking semantics (e.g., a USDC-style blacklist, a paused/upgraded token, or a token whose `transfer` reverts under certain conditions) and the `beneficiary` address becomes blocked *after* the order was placed (or the beneficiary is a contract without a payable `receive()` for the native-token case), every call to `_withdraw` for that commitment reverts. Because `dispatchIncoming` in `EvmHost.sol` treats a reverting `onAccept` as "retry later" (it deletes the receipt and returns rather than propagating funds elsewhere): [4](#0-3) 

the message can be resubmitted indefinitely but will *always* fail in the exact same way, since the failure is deterministic (blocked address / non-payable contract), not transient. There is no code path in `IntentsBase.sol`/`ExtrinsicIntents.sol` (or the Tron `IntentGatewayV2.sol` analog) that lets the rightful owner specify a different beneficiary, pull funds separately per-token, or invoke any admin/emergency rescue for their own escrowed principal — mirroring exactly the reported class of bug: a downstream hook/call that can permanently block user fund recovery with no exit valve. [5](#0-4) 

### Impact Explanation
This is a loss-of-funds condition: escrowed user principal (order inputs on refund, or filler proceeds on redemption) becomes permanently unrecoverable once the beneficiary's transfer path is deterministically blocked, with no on-chain mechanism to redirect or rescue it. This falls under the accepted impact category of "loss of funds" since it is a structural gap in the settlement logic itself, not a relayer/prover/admin trust assumption.

### Likelihood Explanation
Likelihood is moderate: it requires the beneficiary token/address to become non-receiving after order placement (e.g., regulatory blacklist action on a stablecoin, or the user's own smart-contract wallet lacking a payable fallback). This is a realistic real-world event for centralized stablecoins and does not require a malicious peer, relayer, or governance actor — it is purely a function of the deployed escrow-release code path having only one all-or-nothing transfer attempt per token.

### Recommendation
Add a per-token pull-based fallback in `_withdraw` (e.g., on transfer failure, credit an internal claimable balance keyed by `(commitment, token)` rather than reverting), and/or allow the legitimate order owner to redirect withdrawal to an alternate address via a signed message, analogous to the "emergency exit" recommended in the source report.

### Proof of Concept
1. User places a cross-chain order with `order.user` = address `U`, escrowing USDC as `inputs`.
2. Before the order is filled/cancelled, `U` is added to USDC's blacklist (or `U` is a contract without ERC20 handling needs, or in the native-ETH path `U` is a contract with no payable `receive()`).
3. Order is cancelled cross-chain; `RefundEscrow` message arrives at `onAccept()` → `_withdraw()` attempts `IERC20(token).safeTransfer(U, amount)` (or `U.call{value: amount}("")` for native), which reverts.
4. `EvmHost.dispatchIncoming` catches the revert, deletes the receipt, and allows resubmission — but every resubmission fails identically since the underlying block/non-payable condition is permanent.
5. The escrowed `amount` in `_orders[commitment][token]` remains locked forever; no function in `IntentsBase.sol`/`ExtrinsicIntents.sol` allows redirecting the beneficiary or force-releasing to a claimable balance. [6](#0-5)

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

**File:** evm/src/core/EvmHost.sol (L809-817)
```text
        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
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
