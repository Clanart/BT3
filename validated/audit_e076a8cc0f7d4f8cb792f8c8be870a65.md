### Title
Reentrancy via check-effects-interactions violation in `withdraw()` allows double-redemption of escrowed order funds - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The TRON variant of `IntentGatewayV2` still contains an interaction-before-effects `withdraw()` implementation that transfers escrowed tokens/native currency to the beneficiary **before** decrementing the corresponding `_orders[commitment][token]` escrow balance. The mainline EVM contract already fixed this exact pattern in `IntentsBase._withdraw()` by decrementing escrow first, but the TRON copy was not updated to match, leaving a reentrancy window that lets a malicious beneficiary re-enter and redeem the same escrow entry multiple times — directly analogous to the reported `add_tokens` bug, where a stale/incorrect validation on a value that controls fund movement let an attacker draw more value out of the system than was ever legitimately backed.

### Finding Description
`withdraw()` in the TRON contract processes each escrowed token for a `WithdrawalRequest` by sending funds out first, then updating accounting state: [1](#0-0) 

Specifically:
- `_filled[body.commitment] = beneficiary;` is set once, up front, with no re-entrancy guard inside `withdraw()` itself.
- For each token in the loop, the balance check only verifies `_orders[body.commitment][token] == 0` (i.e., *some* escrow exists for that token), not that the escrowed amount covers `amount`.
- The token transfer (native `.call{value: amount}` or ERC20 `.call(...transfer...)`) executes **before** `_orders[body.commitment][token] -= amount;` runs.

This is the same class of bug as the `add_tokens` report: a value that should gate the amount of funds released (the pool/reserve balance in the original report; the per-commitment escrow balance here) is checked/updated too loosely or too late, letting an attacker extract value the guard was supposed to prevent. Here, because the state decrement happens *after* the external call, a beneficiary that is (or controls) a token contract with a transfer hook — or that simply receives native currency via `.call` — can re-enter `withdraw()`/`onAccept()`/`cancelOrder()` for the same commitment while `_orders[body.commitment][token]` still reflects the pre-payout balance, and redeem it again.

Contrast with the already-hardened mainline contract, which decrements escrow *before* transferring: [2](#0-1) 

The TRON copy was evidently not updated to carry this fix forward.

### Impact Explanation
This falls squarely within the bounty's accepted impact set: "stealing or loss of funds" and "replay/double-claim/double-settlement." A successful reentrant beneficiary can drain escrowed input tokens/native currency belonging to legitimate order placers beyond what was ever deposited for that commitment, directly stealing bridged/escrowed funds from the `IntentGatewayV2` contract on the TRON deployment.

### Likelihood Explanation
The attacker fully controls the token contract used as an order's input asset (`order.inputs[i].token` is an arbitrary, user-chosen ERC20 address at `placeOrder` time), and controls the `beneficiary` value returned in cross-chain settlement (it is the solver/filler address). No relayer collusion, governance action, or leaked key is required — only a malicious ERC-20/native-receiving contract deployed by the attacker and a normal order-fill/settlement flow through the existing, unprivileged `fillOrder` → `onAccept`/`withdraw` path.

### Recommendation
Apply the same check-effects-interactions ordering used in `IntentsBase._withdraw()` to the TRON `withdraw()` implementation: decrement `_orders[body.commitment][token]` (and validate `amount <= escrowed`, not just `escrowed != 0`) before performing any external call or native transfer. Additionally, add a reentrancy guard around `withdraw()`'s external entry points (`onAccept`, `cancelOrder`) if the TRON EVM's account model permits reentrant calls similarly to standard EVM.

### Proof of Concept
1. Attacker deploys `EvilToken`, an ERC20 whose `transfer()` callback (or, for the native-currency path, whose `receive()`/fallback) re-enters the `IntentGatewayV2` contract.
2. Attacker places an order using `EvilToken` (or native currency with a malicious beneficiary contract) as `order.inputs[0].token`, escrowing `X` tokens.
3. Order is filled normally; the cross-chain settlement message arrives and `onAccept()` calls `withdraw()` with `beneficiary` = attacker's malicious contract.
4. Inside the token transfer at [3](#0-2) , the malicious beneficiary's hook fires and re-enters `withdraw()` (or a path leading to it) for the same `commitment`/`token` before line 701's decrement executes.
5. Because `_orders[body.commitment][token]` still shows the pre-payout balance, the reentrant call passes the `== 0` check and transfers `amount` again, doubling (or further multiplying, with deeper reentrancy) the payout for the same escrow entry.

### Citations

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
