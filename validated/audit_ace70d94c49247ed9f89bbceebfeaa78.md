### Title
Reentrancy via checks-effects-interactions violation in `withdraw()` allows draining escrowed funds across order tokens - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The external report's core invariant is: low-level `.call()`/`.transfer()` fund transfers to an attacker-controlled recipient must never execute before the corresponding accounting state is finalized, otherwise reentrancy can double-spend accounted balances. The Tron variant of `IntentGatewayV2.withdraw()` reproduces exactly this class of bug: it performs the native/ERC20 `.call()` payout to `beneficiary` *before* decrementing the escrow accounting mapping `_orders[body.commitment][token]`, unlike the equivalent function in the mainline EVM app.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, `withdraw()` is invoked from `onAccept()` for both `RedeemEscrow` and `RefundEscrow` request kinds: [1](#0-0) 

The internal `withdraw()` function pays out escrowed funds to `beneficiary` via a low-level `.call()` and only afterwards decrements the escrow bookkeeping: [2](#0-1) 

The per-token guard is only `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` — it verifies the escrow slot is *non-zero*, not that it has not already been paid out for this iteration, and critically the state write (`_orders[body.commitment][token] -= amount`) happens strictly after the external call completes. If `beneficiary` is a contract, its `receive()`/`fallback()` executes during that `.call{value: amount}("")` while `_orders[body.commitment][token]` still holds its pre-payout value.

This is the exact pattern the external converter report warns about: using a value-forwarding external call ahead of state finalization, breaking checks-effects-interactions. Note that the sibling implementation in the mainline EVM app already does this correctly — it decrements `_orders[body.commitment][token]` *before* calling out to `beneficiary`: [3](#0-2) 

The Tron `IntentGatewayV2.withdraw()` reverses that order, reintroducing the vulnerable pattern.

### Impact Explanation
If an attacker can register/select themselves (or an accomplice) as the `beneficiary` of an order with a malicious contract address, and if any externally reachable function on the contract mutates or reads `_orders[commitment][token]`-adjacent state without being blocked by the `onlyHost` + `authenticate()` gate on `onAccept`, the reentrant call executed during the native-token payout could re-trigger accounting paths while the escrow slot is still un-decremented, leading to double payout / fund loss from the gateway's own token/native balance — a direct "stealing or loss of funds" / "logic attack" impact per the bounty scope. Because `withdraw()` is only reachable through `onAccept`, which is `onlyHost` and calls `authenticate(incoming.request)`, the concretely provable exploit path requires that the reentrant call target another public, non-host-gated function that shares the `_orders` mapping; I was not able to fully enumerate every public entrypoint of this contract (e.g., `fill`, `select`, `cancel`) within the available tool budget to confirm a second callable path exists that would complete a full double-spend during the same call stack.

### Likelihood Explanation
The unsafe ordering itself is proven by direct code comparison against the correct pattern used elsewhere in the same repository (`IntentsBase.sol`), so the checks-effects-interactions violation is a confirmed code defect. However, whether it is *fully* exploitable end-to-end depends on the existence of a second unprotected entrypoint reachable from the beneficiary's fallback that manipulates the same `_orders` state — this could not be conclusively verified within the remaining investigation budget. I flag this with medium-high confidence on the code defect, but lower confidence on a complete, provable end-to-end fund-loss chain without further review of all public functions in `IntentGatewayV2.sol` (Tron variant).

### Recommendation
Reorder `withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` to follow checks-effects-interactions: decrement `_orders[body.commitment][token]` (and the `TRANSACTION_FEES` entry) *before* performing the `.call()`/token transfer, mirroring the pattern already used in `evm/src/apps/intentsv2/IntentsBase.sol` (`escrowed - amount` computed and stored prior to `beneficiary.call{value: amount}("")`). Additionally consider adding a reentrancy guard (`nonReentrant`) to `onAccept` given it performs external value-transferring calls to attacker-influenced `beneficiary` addresses.

### Proof of Concept
Not independently reproduced in this pass — a working PoC would require: (1) confirming a state machine/order flow that lets an attacker become `beneficiary` of an escrow with a malicious contract address, and (2) identifying a second public function on `IntentGatewayV2.sol` (Tron) that reads/writes `_orders[commitment][token]` without an `onlyHost` gate, callable from the malicious `beneficiary`'s `receive()` during the `.call{value: amount}("")` in `withdraw()`. I recommend a background engineer enumerate all public/external functions in `evm/tron/contracts/apps/IntentGatewayV2.sol` touching the `_orders` mapping to complete this proof, then write a Foundry test that (a) escrows funds for a commitment, (b) triggers `onAccept(RedeemEscrow)` with a malicious-beneficiary contract, and (c) has that beneficiary's `receive()` call back into the identified secondary entrypoint to attempt a double withdrawal before `_orders[commitment][token]` is decremented.

### Citations

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-409)
```text
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
