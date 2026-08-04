## Analysis

The Perennial `BalancedVault` bug is fundamentally about **atomicity across independently-failing components**: a vault pools capital across N markets, and if *any one* market becomes permanently unable to settle, the *entire* withdrawal path reverts, freezing funds in all the *other*, perfectly healthy markets too — with no way to cut losses and pull out the good positions.

Hyperbridge's `IntentGatewayV2` intents system has the same shape at the token level: an `Order` can escrow **multiple input tokens** under a single `commitment`, and the only redemption path (`_withdraw`) transfers every escrowed token in one atomic loop with no per-token isolation or try/catch. If any single token in that order becomes permanently un-transferable (blacklist, pause, revert-on-transfer), the whole order — including all other healthy tokens escrowed under the same commitment — is frozen forever, exactly mirroring "one broken market poisons the whole vault."

### Title
Multi-token order escrow is fully frozen if a single input token permanently reverts on transfer - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`_withdraw()` in `IntentsBase.sol` is the sole redemption path for both `RedeemEscrow` (solver payout) and `RefundEscrow` (user refund/cancel) flows, and it is also used directly by same-chain cancellation. For orders with multiple `inputs` tokens, it iterates over all tokens in a single loop and calls `IERC20.safeTransfer` (or a raw ETH `.call`) for each, with no isolation between tokens. If any one token's transfer permanently reverts, the entire transaction reverts — including the transfers/decrements for every *other*, healthy token in that same order — and there is no alternate, per-token withdrawal function to recover the unaffected assets.

### Finding Description [1](#0-0) 

```solidity
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
    ...
}
```

There is no `try/catch` around either the native-ETH `.call` or the `IERC20.safeTransfer`. A single bad token in `body.tokens` (e.g. a stablecoin that blacklists the beneficiary, a token contract that gets paused, or a token whose logic later reverts unconditionally) causes the whole loop — and thus the whole transaction — to revert. Because Solidity reverts unwind all state changes, this also **rolls back the decrement of `_orders[commitment][token]` for every token already processed in the loop**, so healthy tokens are not just "still stuck", they are put right back into limbo alongside the broken one.

This function is invoked from every redemption entrypoint with no fallback:
- Cross-chain settlement/refund via `onAccept`: [2](#0-1) 
- Same-chain cancellation: [3](#0-2) 
- The Tron variant of the gateway has the identical unguarded pattern: [4](#0-3) 

None of these call sites wrap `_withdraw`/`withdraw` in a try/catch, and there is no separate per-token or partial withdrawal function anywhere in the gateway. `SweepDust` only moves *protocol* dust, not a user's own stuck order escrow.

### Impact Explanation
An order with multiple input tokens escrows real user funds under one commitment. If any single one of those tokens becomes permanently untransferable (issuer blacklist/pause/rug, or any revert-on-transfer condition), the order can never be finalized — not via fill, not via refund, not via cancel — because every redemption path funnels through the same all-or-nothing `_withdraw` loop. All other tokens escrowed in that same order, which have nothing wrong with them, are permanently locked in the gateway contract along with the broken one. This is a direct fund-loss/fund-lock condition reachable by an ordinary user simply placing a multi-input order that includes one token that later becomes non-transferable — no relayer, prover, or admin compromise required.

### Likelihood Explanation
Multi-input orders are an explicitly supported feature of the gateway (the `WithdrawalRequest.tokens` array and the loop logic exist specifically to support them, and are exercised in `IntentGatewayV2Test.sol`/`IntentGatewayV2SameChainTest.sol`). Stablecoins with blacklist functionality (USDC/USDT) are common order inputs. Any order that pairs such a token with another asset is exposed. This doesn't require an attacker at all — it's a latent design flaw that fires whenever an included token's transferability is later revoked, which is a realistic and externally-triggerable event for exactly the kind of assets these gateways are built to move.

### Recommendation
Isolate per-token transfer failures inside `_withdraw` (e.g. wrap each transfer in `try/catch`, credit an internal "claimable" balance for tokens whose transfer fails, and let the beneficiary retry that specific token later) so that a single broken token cannot block redemption of the other escrowed assets in the same order. Alternatively, expose a per-token partial-withdrawal entrypoint that lets a beneficiary pull out the healthy tokens from an order even when one token is stuck, and document/emit a distinct event when a token is skipped due to persistent transfer failure rather than reverting the entire settlement.

### Proof of Concept
1. User places a same-chain order with `inputs = [USDC: 1000, TOKEN_X: 1000]`, both escrowed under `commitment`.
2. Issuer of `TOKEN_X` blacklists this gateway contract's address (or pauses transfers) — a condition entirely outside the protocol's control, analogous to an oracle/market going fatally offline in the Perennial report.
3. User calls `cancelOrder` → `_cancelSameChain` → `_withdraw(body, true, true)`.
4. The loop successfully decrements and transfers the healthy USDC amount, then reaches `TOKEN_X`, whose `safeTransfer` reverts.
5. The entire transaction reverts, undoing the USDC decrement/transfer as well. `_orders[commitment][USDC]` and `_orders[commitment][TOKEN_X]` remain populated forever; no other function can withdraw just the USDC leg.
6. The user's USDC — a completely healthy asset — is now permanently locked in the gateway solely because of `TOKEN_X`'s unrelated failure, matching the exact "M-16" fund-loss pattern from the source report.

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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L161-180)
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
