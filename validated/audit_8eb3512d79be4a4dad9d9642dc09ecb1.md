### Title
Escrowed order funds can be permanently locked with no rescue path if the input/fee token blacklists the beneficiary - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
`IntentsBase._withdraw` (used by both `RedeemEscrow`/`RefundEscrow` settlement in `ExtrinsicIntents.onAccept` and by same-chain fills in `IntrinsicIntents`) unconditionally calls `IERC20(token).safeTransfer(beneficiary, amount)` for every escrowed token, with no fallback if the transfer reverts. Any ERC20 with blacklist functionality (e.g. USDC/USDT-style tokens, explicitly the kind flagged in the referenced report) escrowed as an order input can cause this transfer to permanently revert if the intended beneficiary (solver on fill, or user on refund/cancel) is later blacklisted. Because there is no emergency/governance withdraw path for escrowed order funds (`_orders[commitment][token]`) — only `SweepDust`/`DustSwept` exists, and that only drains separately-accounted protocol fee dust, not order escrow — the tokens become permanently stuck in the `IntentGateway` contract.

### Finding Description
`_withdraw` in `IntentsBase.sol` is the single code path used to release escrowed order funds, both for successful fills (`RedeemEscrow`) and cancellations/refunds (`RefundEscrow`): [1](#0-0) 

The `beneficiary` here is attacker/user controlled at order-placement/fill time — the user sets `order.output.beneficiary` and the solver's address is whoever calls `fillOrder`. If the escrowed token is one with blacklist capability, and the beneficiary account is later blacklisted by the token issuer for unrelated reasons, `IERC20(token).safeTransfer(beneficiary, amount)` reverts every time it's invoked.

This function is invoked from `onAccept`, which is the ISMP callback gated only by `onlyHost` and address authentication of the source gateway instance — it is not gated by any admin/governance role: [2](#0-1) 

Because the revert happens inside the mandatory cross-chain settlement callback, the entire message delivery reverts. There is no alternate beneficiary, no partial-settlement fallback, and — critically — no emergency/admin withdraw function for the `_orders[commitment][token]` escrow. The only sweep mechanism in the contract, `SweepDust`, operates on a disjoint accounting bucket (protocol fee "dust" collected at `placeOrder`/`_execute`, not the escrowed order balance), so it cannot rescue these funds: [3](#0-2) 

The same unconditional-transfer pattern with no rescue path exists in the same-chain fill flow: [4](#0-3) 

### Impact Explanation
This causes permanent loss/lockup of user or solver escrowed funds inside `IntentGatewayV2`/`IntentsBase`, matching the "stealing or loss of funds" impact category. Once the beneficiary is blacklisted on the escrowed token, the corresponding `_orders[commitment][token]` balance can never be released — not by the user, not by the solver, and not by governance, since no privileged withdraw exists for order escrow (unlike `EvmHost.withdraw`/`IHostManager.withdraw` for bridge revenue, or `BandwidthManager`'s governance `Withdraw` action, both of which do have this recovery mechanism).

### Likelihood Explanation
This does not require a malicious relayer, prover, or admin — only a token with standard, widely-deployed blacklist functionality (e.g. USDC, USDT) being used as an order input/output asset, and the relevant beneficiary address being blacklisted for reasons unrelated to Hyperbridge (sanctions, exchange hack, compliance action, etc.), which is a realistic real-world occurrence for such stablecoins. Any solver or user address can become blacklisted at any time between order placement and settlement.

### Recommendation
Add a governance-gated emergency/rescue path for escrowed order funds analogous to `EvmHost.IHostManager.withdraw` or `BandwidthManager`'s `Withdraw` governance action — e.g. a `RequestKind` that lets Hyperbridge governance redirect or force-release a specific commitment's stuck escrow to an alternate address after some dispute/timeout window, and/or wrap the `safeTransfer` in `_withdraw` with a try/catch that credits a pull-based "stuck funds" balance instead of reverting the whole settlement when the transfer fails.

### Proof of Concept
1. User places a cross-chain order escrowing `USDC` (blacklist-capable) as `order.inputs[0]`, `IntentGatewayV2.placeOrder`.
2. A solver fills the order on the destination chain; `RedeemEscrow` settlement request is dispatched back to source.
3. Before the settlement message is delivered, the solver's address (the beneficiary of the redeem) is blacklisted by USDC's issuer for an unrelated reason.
4. Relayer delivers the `RedeemEscrow` post request; `ExtrinsicIntents.onAccept` calls `_withdraw`, which calls `IERC20(USDC).safeTransfer(solver, amount)` — this reverts because `solver` is blacklisted.
5. The entire `onAccept` call reverts; the message can never be successfully delivered (retrying produces the same revert). The escrowed USDC remains locked in the `IntentGatewayV2` contract's `_orders[commitment][USDC]` balance indefinitely, with no `SweepDust`/admin call able to touch order-escrow accounting.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-409)
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-674)
```text
        } else if (kind == RequestKind.SweepDust) {
            SweepDust memory req = abi.decode(incoming.request.body[1:], (SweepDust));

            uint256 outputsLen = req.outputs.length;
            for (uint256 i; i < outputsLen;) {
                TokenInfo memory info = req.outputs[i];
                address token = address(uint160(uint256(info.token)));
                uint256 amount = info.amount;

                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
                unchecked {
                    ++i;
                }
                emit DustSwept(token, amount, req.beneficiary);
            }
        }
    }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L101-111)
```text
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
```
