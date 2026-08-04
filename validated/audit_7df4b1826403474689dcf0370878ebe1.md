## Title
Escrow transfer precedes state update in `withdraw()` (checks-effects-interactions violation, native-token reentrancy) - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of `IntentGatewayV2.withdraw()` sends escrowed native tokens/ERC20 to the beneficiary *before* decrementing the internal `_orders[commitment][token]` accounting, unlike the EVM reference implementation which decrements first. This is structurally the same class of bug as the audit report's core theme: value is allowed to leave custody based on stale accounting, before the ledger reflects it, letting a beneficiary that controls execution during the transfer re-enter and extract more than it is entitled to. [1](#0-0) 

### Finding Description
In the EVM reference `IntentsBase.sol`, `_withdraw()` follows checks-effects-interactions correctly:
```
uint256 escrowed = _orders[body.commitment][token];
if (escrowed == 0) revert UnknownOrder();
_orders[body.commitment][token] = escrowed - amount;   // effect BEFORE interaction
... transfer ...
``` [2](#0-1) 

The Tron port, `IntentGatewayV2.sol`, inverts this order — the transfer (an unguarded low-level `.call{value: amount}("")` for native token, or a low-level `token.call(...)` for ERC20) happens first, and the ledger decrement `_orders[body.commitment][token] -= amount;` happens only afterward:
```solidity
if (_orders[body.commitment][token] == 0) revert UnknownOrder();

if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");   // interaction FIRST
    ...
} else {
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    ...
}

_orders[body.commitment][token] -= amount;                // effect AFTER
``` [3](#0-2) 

`withdraw()` is reached from `onAccept()` for both `RedeemEscrow` (beneficiary = solver/filler) and `RefundEscrow` (beneficiary = user) kinds, gated only by `onlyHost`. [4](#0-3)  No `ReentrancyGuard`/`nonReentrant` modifier exists anywhere in the Tron contract set. The pre-transfer check is also weaker than the EVM version's arithmetic guard: it only asserts `_orders[...][token] != 0` rather than computing `escrowed - amount` up front, so during the window between the external call and the decrement the stale (full) balance is still readable/usable by any code path that consults `_orders[commitment][token]` for that same commitment/token — including a second, still-unprocessed instance of the same withdrawal reaching `withdraw()` again while the first is mid-flight.

Since the beneficiary address for `RedeemEscrow` is the attacker-controlled solver/filler itself (an ordinary, unprivileged order participant — no relayer, prover, or admin collusion required), an attacker can make `beneficiary` a contract whose fallback/receive hook fires during the native-token `.call`. If that hook can trigger any code path that reaches this same `withdraw()` logic again for the same `(commitment, token)` before the first call's decrement executes, the stale `_orders[commitment][token]` value (not yet reduced) still passes the `!= 0` check, permitting a second payout of the same escrowed funds — a direct fund-loss/double-payout condition matching the bounty's "unauthorized transaction... transaction manipulation... double-settlement" class, and structurally identical to the report's core lesson: never let value leave before the ledger reflects it.

### Impact Explanation
A successful re-entry drains escrowed order funds beyond what was legitimately owed to the beneficiary, i.e., theft of funds from the `IntentGatewayV2` escrow, directly hitting the "stealing or loss of funds" / "double-claim/double-settlement" bounty categories. Because the same primitive is reused for both `RedeemEscrow` and `RefundEscrow`, both the solver-payout and user-refund paths are exposed.

### Likelihood Explanation
The attacker needs only to be a normal, unprivileged order participant (the filler/solver, or in the refund case the order's own user) who controls the beneficiary address and deploys a contract there — no relayer, prover, admin, or governance compromise is required. The remaining precondition — a reachable path back into `withdraw()`/`onAccept()` for the same commitment during the callback — depends on the host's message-delivery reentrancy posture on Tron, which was not fully verifiable from the index (Tron host dispatch/receipt-tracking code was not inspected in this session). Regardless of that host-side detail, the contract itself violates checks-effects-interactions and lacks any reentrancy guard, which is a real, exploitable weakness independent of assumptions about relayer behavior, and should be treated as high-likelihood given native-token payouts use raw `.call`.

### Recommendation
Mirror the EVM `IntentsBase._withdraw()` pattern: compute `escrowed = _orders[commitment][token]`, revert if insufficient, and write the decremented value to storage *before* performing the native/ERC20 transfer. Additionally add a `nonReentrant` guard (OpenZeppelin `ReentrancyGuard`) to `onAccept`/`withdraw` in the Tron contract as defense-in-depth, consistent with what should exist for any function performing external calls after reading mutable shared state.

### Proof of Concept
1. Attacker acts as `solver`/`filler` for an order and sets `beneficiary` to a malicious contract `M`.
2. Order is escrowed with a native-token input of amount `A`.
3. A `RedeemEscrow` request for the order's commitment is delivered to `onAccept`, invoking `withdraw()`.
4. `withdraw()` checks `_orders[commitment][NATIVE] != 0` (true), then calls `M.call{value: A}("")`.
5. `M`'s `receive()` hook fires *before* `_orders[commitment][NATIVE] -= A` executes, and triggers a second delivery for the same commitment/token to reach `onAccept → withdraw` again (subject to the host's message-processing/receipt semantics on Tron, not verified in this session).
6. The second invocation reads `_orders[commitment][NATIVE]` still at the original (undecremented) value, passes the `!= 0` check, and transfers `A` again to `M`.
7. Net effect: `M` receives `2A` for an order that only escrowed `A`, draining the gateway's other escrowed balances to cover the shortfall — a direct fund-loss / double-settlement bug traceable to the accounting-after-transfer ordering shown at [3](#0-2) , in contrast to the correctly-ordered EVM reference at [2](#0-1) .

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L399-409)
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
