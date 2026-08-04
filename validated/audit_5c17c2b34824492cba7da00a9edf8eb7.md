This confirms `_params.dispatcher` is a single, persistent `CallDispatcher` instance shared across every `placeOrder` call that uses a predispatch flow [1](#0-0) . The `dispatch()` function has a `receive()` and executes arbitrary calls but keeps no per-order accounting itself; balance bookkeeping is entirely the caller's responsibility [2](#0-1) .

### Title
Missing before/after balance delta in Tron `IntentGatewayV2.placeOrder` predispatch sweep lets stray dispatcher balance be misattributed as escrow-exempt "dust" - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The main EVM `IntentGatewayV2.placeOrder` predispatch path correctly measures *actual received* tokens by snapshotting the gateway's own balance before the sweep and taking the post-sweep delta, so any pre-existing balance already on the shared `CallDispatcher` never gets attributed to the current order [3](#0-2) [4](#0-3) . The Tron variant of the same contract drops this before/after snapshot entirely: it reads the dispatcher's raw `balanceOf(dispatcher)` (or `.balance` for native) after the predispatch call and sweeps that whole amount, regardless of how much of it actually originated from the current order's inputs [5](#0-4) .

### Finding Description
`_params.dispatcher` is one persistent `CallDispatcher` contract reused by *every* order that uses `predispatch` [6](#0-5) , and its `dispatch()` implementation has no owner/order isolation — it just executes whatever `Call[]` it's handed and can receive ETH via `receive()` [2](#0-1) .

In the Tron sweep loop, `balance` is read directly from the dispatcher with no "before" snapshot:
```solidity
balance = IERC20(token).balanceOf(dispatcher);
if (balance < requiredAmount) revert InvalidInput();
transferCalls[i] = Call({to: token, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)});
...
uint256 dust = balance - requiredAmount;
if (dust > 0) emit DustCollected(token, dust);
_orders[commitment][token] += reducedInputs[i].amount;
``` [7](#0-6) 

Any tokens sitting on the shared `dispatcher` address *before* this order's predispatch call — from a griefer directly transferring dust to the well-known dispatcher address, from a fee-on-transfer/rebasing token quirk, from ETH sent via `receive()`, or from any residual left by an earlier order's predispatch `call` that didn't fully consume its balance — get swept and counted as this order's `balance`, then emitted/settled as "dust" that is not tied to any user's escrow. This is the structural analog of the reported `OwnerProxy_V1.depositOnBehalf` bug: the code assumes the contract's balance reflects only the current caller's just-transferred funds, when in reality a shared, persistently-funded contract's balance can already be non-zero before the operation runs, corrupting the delta the code relies on. The correct fix (before/after delta) is already implemented in the sibling EVM contract but is absent here.

### Impact Explanation
Impact is confined to protocol-level fund misattribution rather than a stolen user escrow: since `_orders[commitment][token]` is credited with the fixed `reducedInputs[i].amount` (not the swept `balance`), a user's own escrow amount is not shortchanged as long as `balance >= requiredAmount`. However, this breaks the "funds move exactly once and only to the rightful beneficiary and amount" invariant for the *dust* portion: value belonging to no one (or to a prior/foreign depositor) is unconditionally swept into the gateway and emitted as `DustCollected`, i.e. permanently absorbed as protocol surplus without any check on provenance. Because the dispatcher is a single shared, externally address-known contract that anyone can pre-fund, this also opens a path where an attacker can force the `balance < requiredAmount` / `balance` amount to unexpectedly satisfy or exceed the check by donating tokens directly to the dispatcher — inflating swept `dust` under an unrelated order's commitment and desynchronizing on-chain dust accounting from actual per-order deposits.

### Likelihood Explanation
The dispatcher address is deployed once and referenced in `_params.dispatcher`, a public/queryable parameter, so any unprivileged actor can pre-fund it via a plain `transfer`/native send at any time before a victim's `placeOrder(predispatch)` call lands, with no special access or race condition beyond ordinary transaction ordering [6](#0-5) . No relayer, prover, or admin involvement is required.

### Recommendation
Mirror the main EVM `IntentGatewayV2.sol` fix: snapshot the gateway's own balance (or the dispatcher's) immediately before the predispatch sweep and use the post-sweep delta as `balance`/`received`, exactly as done at `evm/src/apps/IntentGatewayV2.sol:229-280`, instead of reading the dispatcher's raw balance after the fact.

### Proof of Concept
1. Note the immutable `dispatcher` address in a deployed `IntentGatewayV2` (Tron variant).
2. Before any victim order executes, attacker directly `transfer`s 1 wei (or more) of the input token to `dispatcher`.
3. Victim calls `placeOrder` with a `predispatch` flow for that token; the predispatch call transfers the intended `order.inputs[i].amount` to `dispatcher` as usual.
4. In the sweep loop, `balance = IERC20(token).balanceOf(dispatcher)` now includes the attacker's pre-funded wei on top of the victim's legitimate transfer.
5. `dust = balance - requiredAmount` is emitted and the entire `balance` (including the attacker's donated wei) is swept to the gateway, while `_orders[commitment][token]` is only credited `reducedInputs[i].amount` — the attacker's donated tokens are absorbed as unaccounted "dust" tied to the victim's order commitment, with no record of the true depositor.

**Note:** I was unable to independently confirm whether the Tron contract is deployed/active on production networks or is a maintained fork, since the index doesn't provide deployment manifests for the Tron variant; a Devin session with full repo/deployment access should verify deployment status before treating this as exploitable in production.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L19-24)
```text
/**
 * @title The CallDispatcher
 * @author Polytope Labs (hello@polytope.technology)
 *
 * @notice This contract is used to dispatch calls to other contracts.
 */
```

**File:** evm/src/utils/CallDispatcher.sol (L36-62)
```text
    /**
     * @dev Receive function to accept ETH transfers
     */
    receive() external payable {}

    /**
     *  @dev reverts if the target is not a contract or if any of the calls reverts.
     */
    function dispatch(bytes memory encoded) external {
        Call[] memory calls = abi.decode(encoded, (Call[]));
        uint256 callsLen = calls.length;
        for (uint256 i = 0; i < callsLen; ++i) {
            Call memory call = calls[i];
            uint32 size;
            address to = call.to;
            assembly {
                size := extcodesize(to)
            }

            if (size == 0) {
                revert NotContract(to);
            }

            (bool success, bytes memory result) = to.call{value: call.value}(call.data);
            if (!success) revert CallFailed(to, result);
        }
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L229-256)
```text
            // Build sweep calls and snapshot gateway balances before the sweep.
            Call[] memory transferCalls = new Call[](inputsLen);
            uint256[] memory balancesBefore = new uint256[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;

                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
                } else {
                    uint256 balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    balancesBefore[i] = IERC20(token).balanceOf(address(this));
                }

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L260-280)
```text
            // Measure actual received, emit dust for excess, update order.inputs.
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 received;
                if (token == address(0)) {
                    received = address(this).balance - balancesBefore[i];
                } else {
                    received = IERC20(token).balanceOf(address(this)) - balancesBefore[i];
                }

                if (received > order.inputs[i].amount) {
                    uint256 dust = received - order.inputs[i].amount;
                    emit DustCollected(token, dust);
                } else {
                    order.inputs[i].amount = received;
                }

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L383-386)
```text
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            // Transfer all predispatch assets to the call dispatcher
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L412-440)
```text
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
