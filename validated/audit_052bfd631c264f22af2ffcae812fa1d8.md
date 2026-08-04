Confirmed: the `CallDispatcher` at `0xE2C7e576E26E0bE7aC97c6fE925bcDAbD87c4bEd` (mainnet) / `0x2b332088275bc9e3c26d81b2975de2483320c181` (testnet) is a **single, permanently deployed, chain-wide singleton** shared by every `IntentGatewayV2` order and every `HyperFungibleToken`/`WrappedHyperFungibleToken` instance's calldata execution — exactly the kind of persistent, unrestricted "hardcoded operator" the external report warns about, except here the operator address is attacker-chosen rather than OpenSea's.

### Title
Attacker-planted infinite ERC20 approvals on the shared, persistent `CallDispatcher` let anyone drain unswept token balances left behind by unrelated HyperFungibleToken/IntentGatewayV2 flows - ([File: evm/src/utils/CallDispatcher.sol])

### Summary
`CallDispatcher` is a single, permanently deployed contract shared across an entire chain by `IntentGatewayV2` (predispatch/postdispatch execution) and every `HyperFungibleToken`/`WrappedHyperFungibleToken` deployment (calldata execution on `onAccept`). Its `dispatch()` function executes fully attacker-supplied `Call[]` arrays with the dispatcher itself as `msg.sender`, with no restriction on target or selector [1](#0-0) . Any unprivileged user can embed an `IERC20.approve(attacker, type(uint256).max)` call in that array — this is the intended, documented workflow for swaps ("approve then swap") [2](#0-1) . That approval is a normal ERC20 allowance stored on the token contract keyed by `owner = CallDispatcher`, and nothing in the protocol ever revokes it. Since the same `CallDispatcher` address is reused by every order and every HFT transfer on the chain [3](#0-2) , any token balance that later lands on the dispatcher and is not swept back out (from a different, unrelated flow) becomes a standing target for that attacker-granted allowance.

### Finding Description
`_execute()` in `IntentsBase.sol` only sweeps the tokens explicitly listed in `order.output.assets` back to the gateway after running `order.output.call` [4](#0-3) . The predispatch path in `IntentGatewayV2.sol` similarly only sweeps tokens listed in `order.inputs` [5](#0-4) . Neither sweep is exhaustive over "whatever the dispatcher happens to hold" — only over the specific token addresses the current order declares. `HyperFungibleTokenUpgradeable.onAccept()` is worse: it forwards `message.data` to the dispatcher with **no sweep-back at all** [6](#0-5) ; the docs explicitly instruct integrators to mint tokens directly `to` the `CallDispatcher` address so the embedded calls can spend them [7](#0-6) , with recovery of any leftover entirely dependent on the calldata author remembering to move funds back out.

Because the dispatcher is one shared, long-lived contract for the whole chain, an attacker can, in one transaction, plant `token.approve(attacker, type(uint256).max)` from the dispatcher for any token of interest (USDC, the fee token, WETH, etc.) at essentially zero cost — it only requires a trivial HFT `send()` or a cheap `IntentGatewayV2` order carrying that call in `predispatch.call` or `output.call`. From then on, whenever any *other, unrelated* user's flow through the same dispatcher (swap slippage residue, an intermediate swap-hop token not listed in `order.output.assets`, a partially-consumed approval target, or any HFT transfer that mints tokens directly to the dispatcher for calldata execution but whose calldata doesn't fully clear the balance) leaves so much as dust of that token on the dispatcher, the attacker can call `token.transferFrom(dispatcher, attacker, balance)` directly and pull it out — no proof, no relayer, no admin action required. The documentation itself acknowledges the dispatcher "holds tokens temporarily during execution" and recommends exact-amount approvals as a *best practice* for the calldata author [8](#0-7) , but this is advisory only — the protocol enforces nothing on-chain to prevent a malicious actor from planting an approval to themselves rather than to the intended DEX router.

### Impact Explanation
This is a direct, unauthorized-execution / fund-theft path against a shared bridge-custody contract: an unprivileged attacker can convert any residual balance that transiently touches the singleton `CallDispatcher` — money that in general belongs to unrelated users' orders or transfers — into their own funds, with no cooperation from a relayer, prover, or admin. Because `CallDispatcher` is reused across every `IntentGatewayV2` order and every HFT instance on a given chain, the blast radius is chain-wide rather than scoped to a single order.

### Likelihood Explanation
Planting the malicious approval requires only a single cheap, unprivileged transaction (a minimal HFT `send()` with crafted `data`, or an `IntentGatewayV2` order with a crafted `predispatch`/`output.call`) — no special permissions. Triggering the actual theft is opportunistic: the attacker must wait for some other flow to leave a nonzero, unswept balance of the same token on the dispatcher (e.g., a multi-hop swap producing an intermediate token not in the sweep list, slippage dust, or an integrator following the documented "mint to CallDispatcher" HFT pattern without a fully-consuming call array). Given how common multi-step DEX calldata is in the documented use cases, and that the dispatcher's sweep logic is scoped only to the current order's declared asset list, such residues are a realistic, recurring occurrence rather than a contrived edge case.

### Recommendation
- Have `CallDispatcher.dispatch()` never persist state across calls: either (a) make the dispatcher a fresh per-call proxy (e.g., deployed via `CREATE2`/minimal-proxy per invocation and self-destructed or rendered unreusable after use) so no allowance can outlive a single dispatch, or (b) after every `dispatch()` call, force-revoke (`approve(spender, 0)`) any approvals it granted during that call by tracking touched `(token, spender)` pairs, or (c) restrict callable targets/selectors on `to` (e.g., disallow `approve`/`increaseAllowance` selectors entirely and require the calling contract to grant scoped approvals itself before invoking the dispatcher).
- In `IntentsBase._execute()` and `IntentGatewayV2`'s predispatch sweep, sweep the dispatcher's *entire* balance of every token actually touched by the executed calls (not just the tokens declared in `order.inputs`/`order.output.assets`), or disallow calldata that interacts with tokens outside the declared set.
- In `HyperFungibleTokenUpgradeable.onAccept()`, add a mandatory sweep-back step after `ICallDispatcher.dispatch()` for the transferred token, mirroring the intent-gateway dust-collection pattern, rather than relying entirely on integrator-authored calldata to clear the dispatcher's balance.

### Proof of Concept
1. Attacker calls `HyperFungibleToken.send()` (or places a trivial `IntentGatewayV2` order) with `data` encoding a single `Call{ to: USDC, value: 0, data: abi.encodeWithSelector(IERC20.approve.selector, attacker, type(uint256).max) }`. This is forwarded verbatim to `ICallDispatcher(dispatcher).dispatch(data)` [9](#0-8) , which executes `USDC.approve(attacker, max)` with `msg.sender == CallDispatcher`. USDC's allowance mapping now shows `allowance[CallDispatcher][attacker] = type(uint256).max`.
2. At any later point, an unrelated `IntentGatewayV2` order's `output.call` performs a multi-hop swap ending in USDC as an intermediate/leftover token that is not listed in that order's `output.assets` (so `_execute()`'s sweep loop never transfers it out) [10](#0-9) , or an HFT integrator mints tokens directly to the `CallDispatcher` per the documented pattern [7](#0-6)  and its calldata does not fully drain USDC out.
3. Attacker calls `USDC.transferFrom(CallDispatcher, attacker, USDC.balanceOf(CallDispatcher))` directly — no interaction with `IntentGatewayV2` or `HyperFungibleToken` needed — and receives the unrelated user's residual USDC.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L44-62)
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
    }
```

**File:** docs/content/developers/evm/hyper-fungible-token/overview.mdx (L86-98)
```text
## Calldata Execution

Both contracts support optional calldata execution on the destination chain via the `CallDispatcher`. By passing a non-empty `data` field in `SendParams`, the sender can trigger arbitrary contract calls on the destination chain immediately after tokens are minted or unlocked. This enables composable cross-chain workflows like transfer-and-swap (e.g., bridge USDC then swap to WETH via UniswapV2), transfer-and-stake, or transfer-and-deposit into a lending protocol — all in a single cross-chain operation.

The `data` field is an ABI-encoded `Call[]` array, where each `Call` specifies a target contract, a native value to forward, and the calldata to execute. On the destination chain, the HFT contract mints or unlocks tokens to the `to` address, then forwards the entire `data` payload to the `CallDispatcher`, which executes each call sequentially. If the calls need to spend the bridged tokens (e.g., approve then swap), set `to` to the `CallDispatcher` address so tokens are delivered directly to it.

For code examples, see the [HyperFungibleToken](/developers/evm/hyper-fungible-token/hyper-fungible-token#calldata-execution) and [WrappedHyperFungibleToken](/developers/evm/hyper-fungible-token/wrapped-hyper-fungible-token#calldata-execution) pages.

### Security

The `CallDispatcher` executes calls in its own context (not via `delegatecall`), so the HFT contract's storage is never at risk. If any call in the array reverts, the entire `onAccept` handler reverts — including the token mint/unlock. The request can then be retried by any relayer until the timeout expires. If no successful execution occurs before the timeout, the request times out and the sender is eligible for a refund on the source chain. Token approvals in the `Call[]` should use exact amounts rather than unlimited allowances, since the dispatcher contract holds tokens temporarily during execution.

Existing `CallDispatcher` deployments are listed on the [contract addresses](/developers/evm/contract-addresses/mainnet) page.
```

**File:** docs/content/developers/evm/contract-addresses/mainnet.mdx (L42-43)
```text
| `CallDispatcher` | [`0xE2C7e576E26E0bE7aC97c6fE925bcDAbD87c4bEd`](https://arbiscan.io/address/0xE2C7e576E26E0bE7aC97c6fE925bcDAbD87c4bEd) |
| `IntentGatewayV2` | [`0xAe041F7B0CB581876832830baeB6a2Aa2a3C9716`](https://arbiscan.io/address/0xAe041F7B0CB581876832830baeB6a2Aa2a3C9716) |
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-473)
```text
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

        Call[] memory sweepCalls = new Call[](outputsLen);
        uint256 sweepCount = 0;

        for (uint256 i; i < outputsLen;) {
            address token = address(uint160(uint256(order.output.assets[i].token)));

            if (token == address(0)) {
                uint256 balance = dispatcher.balance;
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({to: address(this), value: balance, data: ""});
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            } else {
                uint256 balance = IERC20(token).balanceOf(dispatcher);
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            }

            unchecked {
                ++i;
            }
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L227-240)
```text
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

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
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol (L320-336)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        Message memory message = abi.decode(request.body, (Message));
        address beneficiary = _toAddr(message.to);
        _mint(beneficiary, message.amount);

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }

        emit Received({from: message.from, to: beneficiary, source: string(request.source), amount: message.amount});
    }
```

**File:** docs/content/developers/evm/hyper-fungible-token/hyper-fungible-token.mdx (L149-159)
```text
IHyperFungibleToken(tokenAddress).send{value: nativeFee}(
    IHyperFungibleToken.SendParams({
        dest: StateMachine.evm(42161),
        // mint to the CallDispatcher so the swap can spend the tokens
        to: abi.encodePacked(CALL_DISPATCHER),
        amount: amount,
        timeout: 3600,
        relayerFee: relayerFee,
        data: abi.encode(calls)
    })
);
```
