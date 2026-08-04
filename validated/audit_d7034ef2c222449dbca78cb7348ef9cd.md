### Title
Escrow withdrawal in `IntentGatewayV2.withdraw` treats a call to a non‑existent/self‑destructed token contract as a successful transfer, allowing fund theft from fillers — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2` releases escrowed order inputs to a beneficiary (filler or user) with a raw low-level `.call` to the ERC20 `token` address and only checks the boolean `success` return value, without first verifying the target has deployed code. The EVM returns `success = true` (with empty returndata) for any call — including `transfer(...)` calldata — sent to an address with no contract code. Since the order's input `token` is chosen by the order creator (an unprivileged user) at `placeOrder` time, this reproduces exactly the "lack of contract existence check" bug class from the external report, but here it corrupts escrow accounting and lets a user extract real value from a filler for a worthless/no-op "token".

### Finding Description
In the withdrawal path: [1](#0-0) 

```solidity
if (_orders[body.commitment][token] == 0) revert UnknownOrder();

if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    if (!sent) revert InsufficientNativeToken();
} else {
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();
}

_orders[body.commitment][token] -= amount;
```

There is no `extcodesize(token) > 0` check before the `.call`. Per documented EVM semantics (the same fact cited in the external report), a low-level call to an address without code always returns `success = true` and empty `returndata`, regardless of the calldata supplied. The codebase demonstrates it is aware of this exact class of bug and normally guards against it — `CallDispatcher.dispatch` explicitly checks `extcodesize` before making an external call: [2](#0-1) 

and `EvmHost.dispatchIncoming(PostRequest, ...)` similarly checks `extcodesize(destination)` before dispatching to a module: [3](#0-2) 

However, this same protection is missing in `IntentGatewayV2`'s `withdraw` when releasing/refunding an order's `token`, since `token` is fully attacker-supplied at order creation (`TokenInfo.token` in `Order.inputs`), an order creator can name a token address that either never had code or is a self-destructing contract deployed by them.

### Impact Explanation
An order's `user` (an unprivileged, ordinary caller of `placeOrder`) controls the `token` value used for escrow accounting. If they use a contract that self-destructs (or point to a precomputed address that they never deploy code to, if escrow deposit itself can be bypassed/short-circuited similarly), a filler who fills the order and transfers real value on the output leg will find that `withdraw()`'s `token.call(...)` for the input leg "succeeds" without moving any real tokens, while `_orders[commitment][token] -= amount` still executes as if the transfer happened. This is a direct fund-loss vector for fillers/solvers who are unrelated, honest actors, matching the bounty's "stealing or loss of funds" and "false ... acceptance" categories — the contract falsely accepts a no-op transfer as a genuine settlement of escrowed value.

### Likelihood Explanation
The path only requires an unprivileged user to place an order using a self-destructible or address-with-no-code as the input token — no relayer, prover, admin, or governance compromise is needed. The bug is directly reachable through the public `placeOrder` → `fillOrder`/`onAccept` → `withdraw` flow that every order goes through.

### Recommendation
Add an `extcodesize(token) > 0` check (mirroring `CallDispatcher.dispatch` and `EvmHost.dispatchIncoming`) before any low-level `.call` to `token` in `withdraw`, and consider using OpenZeppelin's `SafeERC20`, which internally performs this existence check, instead of raw `.call` with `IERC20.transfer.selector`.

### Proof of Concept
1. Attacker deploys a minimal ERC20-like contract `FakeToken` with a `selfdestruct` function callable by the attacker.
2. Attacker calls `placeOrder` with `inputs[0].token = address(FakeToken)`, escrowing `amount` of `FakeToken` (real transfer succeeds since the contract has code at this point).
3. A filler observes the order, calls `fillOrder`, and sends real `output` assets to the user's beneficiary, expecting to later claim the escrowed `FakeToken` amount via `withdraw`.
4. Before the filler calls `withdraw`, the attacker calls `FakeToken.selfdestruct()`, removing its code (viable on chains without EIP-6780 restrictions such as this Tron deployment target).
5. Filler (or a relayer processing a cross-chain response) triggers `withdraw`; `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` returns `success = true` trivially since `token` has no code, `_orders[commitment][token] -= amount` proceeds, and `EscrowReleased` is emitted — but the filler never actually receives any tokens, resulting in a net loss of the real output assets they already sent.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L691-705)
```text
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

**File:** evm/src/utils/CallDispatcher.sol (L44-61)
```text
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
```

**File:** evm/src/core/EvmHost.sol (L794-803)
```text
    function dispatchIncoming(PostRequest memory request, address relayer) external restrict(_hostParams.handler) {
        address destination = _bytesToAddress(request.to);
        uint256 size;
        assembly {
            size := extcodesize(destination)
        }
        if (size == 0) {
            // instead of reverting the entire batch, early return here.
            return;
        }
```
