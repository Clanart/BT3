## Title
Tron `IntentGatewayV2.withdraw()` transfers escrowed tokens before debiting `_orders`, allowing reentrant double-withdrawal of the same escrow - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron fork of `IntentGatewayV2` implements its own internal `withdraw()` function that, unlike the canonical `IntentsBase._withdraw()` used by the main EVM contract, performs the external token transfer **before** decrementing the per-commitment escrow balance and does not validate that `amount <= escrowed`. Combined with the fact that `withdraw()` is reachable via `onAccept()` for both `RedeemEscrow` and `RefundEscrow` message kinds, a token that can trigger a callback during `transfer`/native ETH send (fee-on-transfer/ERC777-style tokens, or native ETH to a contract beneficiary) can re-enter and drain more than what was escrowed for a single commitment, since the state (`_orders[commitment][token]`) has not yet been reduced when the callback fires.

### Finding Description
Compare the two withdrawal implementations in the repo:

Canonical (`evm/src/apps/intentsv2/IntentsBase.sol`, `_withdraw`, lines 390-410) follows checks-effects-interactions: [1](#0-0) 
```
uint256 escrowed = _orders[body.commitment][token];
if (escrowed == 0) revert UnknownOrder();
_orders[body.commitment][token] = escrowed - amount;   // effect BEFORE interaction
if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    ...
} else {
    IERC20(token).safeTransfer(beneficiary, amount);
}
```

Tron fork (`evm/tron/contracts/apps/IntentGatewayV2.sol`, `withdraw`, lines 682-721) reverses the order: [2](#0-1) 
```
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;

    uint256 len = body.tokens.length;
    for (uint256 i; i < len;) {
        address token = address(uint160(uint256(body.tokens[i].token)));
        uint256 amount = body.tokens[i].amount;
        if (_orders[body.commitment][token] == 0) revert UnknownOrder();

        if (token == address(0)) {
            (bool sent,) = beneficiary.call{value: amount}("");   // interaction FIRST
            if (!sent) revert InsufficientNativeToken();
        } else {
            (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
            if (!success) revert TransferFailed();
        }

        _orders[body.commitment][token] -= amount;   // effect happens AFTER interaction
        unchecked { ++i; }
    }
    ...
```

Two problems compound here:
1. **Order of operations**: the external call (`beneficiary.call{value: amount}("")` for native tokens, or `token.call(transfer(...))` for ERC20-like tokens) happens before `_orders[body.commitment][token]` is decremented. If the beneficiary (for native transfers) or the token contract (for a malicious/ERC777-style token) can execute code during that call, it can re-enter `onAccept` is not directly reachable by an attacker (guarded by `onlyHost`), but the beneficiary itself is not privileged — a contract beneficiary receiving the native-token payout can reenter any other publicly-reachable function of the gateway (e.g. `fillOrder`, `cancelOrder`, or another already-in-flight `withdraw` triggered by a second delivered request for the same commitment) while `_orders[commitment][token]` still reflects the pre-payout balance.
2. **No amount vs. escrowed-balance check**: the loop only checks `_orders[body.commitment][token] == 0` (i.e., "is there *any* escrow left"), not `amount <= _orders[body.commitment][token]`. Combined with unchecked subtraction risk being avoided only by Solidity 0.8's automatic revert-on-underflow, this still permits over-payout up to whatever the reentrant path can trigger before the balance is finally reduced.

By contrast, the main EVM path in `evm/src/apps/intentsv2/IntentsBase.sol` reads `escrowed`, decrements it immediately, and only then performs the transfer, closing this window entirely.

### Impact Explanation
This directly matches the required impact classes: **loss of funds / unauthorized double-settlement of escrowed bridge funds**. A single delivered `RedeemEscrow`/`RefundEscrow` message on the Tron deployment could be leveraged by a malicious beneficiary contract to drain more of the escrowed input tokens than were legitimately owed for that commitment, since the escrow ledger (`_orders[commitment][token]`) is stale for the duration of the external call. This is a state-corruption/fund-loss bug in the bridge's own custody accounting on the destination side of the settlement, not a relayer/prover trust issue — the attacker only needs to be a normal user who places an order with a malicious contract as `beneficiary`/`order.user`, which is the standard input surface for `placeOrder`/`cancelOrder`.

### Likelihood Explanation
The attacker fully controls the beneficiary address (it's just `order.user` for refunds or `msg.sender`/solver address for redemptions), so triggering the reentrant callback path requires no relayer, prover, or governance collusion — only a deployed malicious contract as beneficiary and native-token (or callback-capable token) payout. The `onlyHost` guard on `onAccept` prevents an attacker from calling `withdraw` directly, but does not prevent the beneficiary contract from reentering during the payout itself, which is the classic reentrancy vector this code order enables.

### Recommendation
Mirror the canonical `IntentsBase._withdraw()` pattern: read `escrowed = _orders[body.commitment][token]`, validate `amount <= escrowed`, write the decremented balance to storage, and only then perform the external transfer/call. Add a standard reentrancy guard (`nonReentrant`) to `onAccept`/`withdraw` for defense in depth, and require `amount <= _orders[body.commitment][token]` explicitly rather than only checking for non-zero balance.

### Proof of Concept
Conceptual PoC (cannot be executed without a live Tron deployment/foundry harness for this specific file, which was not available in the indexed test suite):
1. Attacker places a same-chain or cross-chain order with `order.user` (refund beneficiary) set to a malicious contract `Evil`.
2. `Evil` implements a `receive()` (for native token escrow) that, upon receiving the first payout `call`, re-enters the gateway (e.g., calls `cancelOrder`/triggers a second in-flight `withdraw` for the same commitment via a duplicate destination proof, or interacts with another function that reads `_orders[commitment][token]` before it has been decremented).
3. Because `withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` sends the native/token payout before subtracting from `_orders[body.commitment][token]`, the reentrant call observes the pre-decrement escrow balance and can trigger a second payout for the same escrowed amount.
4. Net effect: `Evil` receives more tokens than were legitimately escrowed for the commitment, at the expense of the gateway's other escrow holders.

Note: I was not able to locate a Tron-specific Foundry/JS test harness in the indexed codebase to run this end-to-end, so the PoC above is derived directly from the code-level ordering difference versus the audited canonical implementation. If further validation is needed, a Devin session with full repo/test access could build a fork test exercising `withdraw()` with a reentrant beneficiary/token to confirm the double-payout empirically.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-409)
```text
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
