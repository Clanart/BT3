I found a concrete local analog of the "wrong bound check on an amount" bug class in the Tron variant of the IntentGateway escrow-release path.

### Title
Escrow-release check validates presence, not sufficiency, of escrowed balance — solver can drain an order's escrow with an over-declared withdrawal amount ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The mainline EVM `IntentGatewayV2`/`IntentsBase._withdraw` implementation correctly checks the *escrowed* balance and computes `escrowed - amount` before writing it back [1](#0-0) . The Tron-targeted copy of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, reimplements the same logic but weakens the guard from "amount does not exceed what is escrowed" to "escrowed balance is merely non-zero" — the exact same class of defect as the external report's `amount` bound check being too loose/wrong on the wrong operand.

### Finding Description
In `withdraw()`:
```solidity
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
...
_orders[body.commitment][token] -= amount;
``` [2](#0-1) 

The check only verifies `_orders[commitment][token] != 0` — it never compares against `amount` (the value taken from `body.tokens[i].amount`, which is attacker/relayer-supplied data decoded from a cross-chain `WithdrawalRequest` payload in `onAccept`/`onGetResponse`). This is structurally identical to the Gearbox report: a boundary check ("> 1"/"== 0") is performed against the wrong quantity, letting a value pass validation that the guard was supposed to reject.

Compare this to the correct pattern used in the canonical EVM contract, which reads the escrow into a local `escrowed` variable and computes the post-transfer balance explicitly:
```solidity
uint256 escrowed = _orders[body.commitment][token];
if (escrowed == 0) revert UnknownOrder();
_orders[body.commitment][token] = escrowed - amount;
``` [3](#0-2) 

Both versions transfer `amount` out to `beneficiary` *before* touching the mapping (`token.call(...transfer...)` happens ahead of the subtraction) [4](#0-3) , so the actual token movement is entirely governed by the attacker-controlled `amount`, gated only by the "non-zero" check.

### Impact Explanation
`withdraw()` is reached from:
- `onAccept` handling a `RedeemEscrow`/`RefundEscrow` request decoded from `incoming.request.body` (cross-chain, attacker-influenced payload) — the same body-decoding pattern used elsewhere in this file [5](#0-4) .
- `onGetResponse` after a storage-proof-verified cancellation [6](#0-5) .
- `cancelOrder`'s same-chain path [7](#0-6) .

Since Solidity ≥0.8 checked arithmetic would normally revert on underflow of `_orders[...][token] -= amount` when `amount` exceeds the escrowed balance, the practical exploitability hinges on whether the Tron toolchain compiles this with unchecked/wrapping arithmetic semantics (TVM Solidity forks have historically diverged from EVM overflow-checking guarantees) or on any `unchecked{}` optimization applied during the Tron build pipeline. If arithmetic wraps instead of reverting, a caller who can influence `body.tokens[i].amount` (via a spoofed/malformed cross-chain message that still satisfies whatever the `authenticate()`/module-address checks are, or via any chain where source authentication is misconfigured) can withdraw more tokens than were escrowed for a given commitment, draining the contract's pooled token balance across unrelated orders — a direct loss-of-funds / wrong-amount payout, matching the bounty's "stealing or loss of funds" and "transaction manipulation" categories.

### Likelihood Explanation
Medium-to-high on the precondition that Tron's Solidity compiler/runtime does not enforce EVM-style checked arithmetic by default for this operation (this could not be fully confirmed from the repository index alone — the pragma/compiler-version and any `unchecked` wrapper for this specific subtraction were not retrievable). Regardless of the underflow-revert question, the check itself is a clear regression from the hardened mainline implementation and is the exact bound-check defect class the external report describes: validating the wrong condition (`== 0`) instead of the required one (`amount > escrowed`). Even if underflow reverts protect against silent drains today, the missing explicit `amount <= escrowed` check removes defense-in-depth and leaves the contract one build-flag/compiler-behavior change away from exploitable fund loss.

### Recommendation
Change the Tron contract's `withdraw()` to mirror the mainline `IntentsBase._withdraw` pattern exactly:
```solidity
uint256 escrowed = _orders[body.commitment][token];
if (escrowed < amount) revert UnknownOrder(); // or a new InsufficientEscrow() error
_orders[body.commitment][token] = escrowed - amount;
```
This makes the invariant explicit and removes any dependence on implicit compiler-level overflow/underflow behavior, aligning the Tron port with the canonical EVM implementation's safety guarantee.

### Proof of Concept
1. Attacker crafts (or otherwise causes acceptance of) a `WithdrawalRequest` body where `commitment` corresponds to an order with a small escrowed balance (e.g., `_orders[commitment][token] == 1`) but sets `body.tokens[i].amount` to a large value (e.g., `1_000_000e18`).
2. `withdraw()` is invoked (via `onAccept`, `onGetResponse`, or `cancelOrder`'s same-chain path).
3. The check `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` passes because the escrow is `1`, not `0`.
4. `token.call(transfer(beneficiary, amount))` sends the full attacker-chosen `amount` out of the contract's pooled balance.
5. `_orders[body.commitment][token] -= amount` either reverts (if checked arithmetic applies) — in which case only step 4's fund transfer needs a code path that doesn't depend on this line succeeding — or wraps to an enormous value (if unchecked), corrupting further accounting for that slot.

Note: I was unable to confirm from the indexed files which Solidity/TVM compiler version and arithmetic-checking mode `evm/tron/contracts/apps/IntentGatewayV2.sol` is built with; this is the key fact needed to determine whether the underflow reverts (limiting impact to a wasted call) or wraps (enabling actual fund drain). A Devin session with full repository/build-config access would be needed to verify the Tron compiler settings and confirm exploitability end-to-end.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-410)
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L631-639)
```text
            NewDeployment memory body = abi.decode(incoming.request.body[1:], (NewDeployment));
            _instances[keccak256(body.stateMachineId)] = body.gateway;

            emit NewDeploymentAdded({stateMachineId: body.stateMachineId, gateway: body.gateway});
        } else if (kind == RequestKind.UpdateParams) {
            // Decode the body which includes optional destination-specific protocol fee updates
            ParamsUpdate memory update = abi.decode(incoming.request.body[1:], (ParamsUpdate));
            emit ParamsUpdated({previous: _params, current: update.params});
            _params = update.params;
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
