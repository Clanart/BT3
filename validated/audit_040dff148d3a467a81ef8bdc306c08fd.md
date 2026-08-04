## Finding: Unrestricted `CallDispatcher.dispatch()` allows anyone to drain funds held by the dispatcher

### Title
Missing access control on `CallDispatcher.dispatch()` allows anyone to execute arbitrary calls and drain the dispatcher's held funds - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher.dispatch(bytes memory encoded)` is a fully public, unauthenticated entrypoint that executes an arbitrary array of `Call{to, value, data}` structs with `CallDispatcher`'s own identity (`msg.sender == CallDispatcher`) and forwards any ETH `value` it specifies. This mirrors the `RollerPeriphery.approve()` bug: a shared utility/periphery contract exposes a state-changing call with no restriction on the caller, so anyone can make the contract itself act as the "spender"/"caller" for arbitrary targets, and any funds sitting in that contract can be stolen.

### Finding Description
`CallDispatcher` is a shared utility contract referenced as `_dispatcher` by `HyperFungibleToken`, `WrappedHyperFungibleToken`, and their upgradeable variants, invoked from within `onAccept` to execute cross-chain calldata after tokens are minted/transferred: [1](#0-0) 

The intent is clearly that `dispatch()` should only ever be invoked as a trusted callback from the app's `onAccept` flow, itself gated by `onlyHost`. However, `CallDispatcher.dispatch()` has **no caller restriction whatsoever**: [2](#0-1) 

The contract also explicitly accepts and holds ETH via `receive() external payable {}`: [3](#0-2) 

Because `dispatch()` is callable by anyone directly (not only through an app's `onAccept`), any external actor can bypass the entire ISMP/host authentication path and directly instruct `CallDispatcher` to:
- Call `ERC20.approve(attacker, amount)` on any token the dispatcher holds a balance of (dust from a partially-executed cross-chain calldata batch, accidental transfers, or leftover ETH refunds), letting the attacker then pull those tokens via `transferFrom`.
- Call `target.call{value: ...}(...)` forwarding any ETH balance the dispatcher currently holds to an attacker-chosen address, since the dispatcher's own ETH balance funds `call.value`.

This is architecturally identical to the M-1 report: a periphery/utility contract's state-changing function (`approve`, here generalized to "execute arbitrary call as this contract") has no access control, so anyone can spend/move whatever the contract owns.

### Impact Explanation
Any ETH or ERC20 balance temporarily or permanently held by `CallDispatcher` (via its `receive()` fallback or token transfers left as dust from calldata execution during cross-chain deliveries) can be stolen by an unprivileged attacker who directly calls `dispatch()` with a crafted `Call[]` targeting `approve`/`transfer`/self-executing withdrawal logic. This is a direct loss-of-funds vector matching the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" categories, without requiring a malicious relayer, prover, or governance actor — a plain EOA can call the public function.

### Likelihood Explanation
`dispatch()` is `external` with zero modifiers or `msg.sender` checks, and `CallDispatcher` explicitly declares a payable `receive()`, indicating the authors expect it to sometimes hold ETH. Any accidental or transient balance (e.g., leftover native value from a multi-call batch, unclaimed refunds, or ERC20 sent to it by mistake since its address is referenced on-chain by multiple apps) is immediately exposed. The likelihood of the dispatcher holding at least dust value is non-trivial given it's a shared singleton wired into every `HyperFungibleToken`/`WrappedHyperFungibleToken` deployment.

### Recommendation
Restrict `CallDispatcher.dispatch()` to only be callable by the authorized app contracts (or the `Host`) that are meant to invoke it, e.g. via an allowlist of registered callers set at construction/configuration time, or by making `dispatch()` an internal library-style call inlined into each app rather than an externally callable singleton. At minimum, `CallDispatcher` should never hold a residual ETH/token balance between calls — sweep any leftover value back to the original caller/app at the end of `dispatch()`, and remove the unconditional payable `receive()` if it is not required by the intended call flow.

### Proof of Concept
1. Assume `CallDispatcher` at address `D` has accumulated some ERC20 token `T` balance (e.g., dust left after a partially successful cross-chain calldata execution during `HyperFungibleToken.onAccept`, or an accidental transfer).
2. Attacker (no special role) crafts `calls = [Call({to: T, value: 0, data: abi.encodeWithSelector(IERC20.approve.selector, attacker, type(uint256).max)})]`.
3. Attacker calls `CallDispatcher(D).dispatch(abi.encode(calls))` directly — no `onlyHost` or any check blocks this since `dispatch()` has no access control: [4](#0-3) 
4. `T.approve(attacker, max)` executes with `msg.sender == D`, granting the attacker unlimited allowance over `D`'s token balance.
5. Attacker calls `T.transferFrom(D, attacker, balance)` to drain the tokens, or for ETH, crafts a `Call` with nonzero `value` to move `D`'s native balance to an attacker-controlled contract.

### Citations

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L291-304)
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
```

**File:** evm/src/utils/CallDispatcher.sol (L36-39)
```text
    /**
     * @dev Receive function to accept ETH transfers
     */
    receive() external payable {}
```

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
