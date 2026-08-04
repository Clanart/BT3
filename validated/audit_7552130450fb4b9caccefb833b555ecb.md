## Finding [1](#0-0) 

### Title
Postdispatch/predispatch calls execute under a single shared `CallDispatcher` identity, letting any order's calldata capture value that an external stateful protocol attributed to a prior order — (File: `evm/src/utils/CallDispatcher.sol`, `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentGatewayV2`'s postdispatch/predispatch calldata feature routes value through one shared `CallDispatcher` instance (`_params.dispatcher`) for *every* order the gateway ever processes. `CallDispatcher.dispatch()` executes target calls in its own context via `.call`, not `delegatecall`, so any external protocol invoked this way sees `msg.sender == address(CallDispatcher)`. This is the same broken-invariant class as the SiloGateway/FraxLend report: an external protocol that books positions (collateral, deposits, credit) to the caller rather than to a designated beneficiary will book them to the gateway's shared intermediary contract instead of to the order's actual beneficiary — except here the intermediary identity is reused across *every* order, not scoped per user or per order.

### Finding Description
`_execute()` in `IntentsBase.sol` dispatches an order's `output.call` through the shared dispatcher and afterward only sweeps the *specific output-asset token balances* it expects back: [2](#0-1) 

It never verifies that the calldata's side effects were fully reversed — it merely reclaims whatever ERC20/native balance happens to sit on the dispatcher for the order's declared output tokens: [3](#0-2) 

The docs confirm this is an intended usage pattern — routing solver-delivered output tokens "through a DeFi protocol before reaching the beneficiary": [4](#0-3) 

If the target DeFi protocol (a lending market, vault, staking contract, etc. — the same class of protocol implicated in the original FraxLend example) attributes the resulting position to `msg.sender` rather than to a caller-supplied beneficiary parameter, that position becomes owned by `address(CallDispatcher)` — a single contract address shared by the entire `IntentGatewayV2` deployment across all orders, all beneficiaries, and all solvers. Unlike a per-user gateway (SiloGateway), this shared identity means:

1. A first order's postdispatch call can leave value/position permanently attributed to `CallDispatcher` in an external protocol (fund lock, matching the original report).
2. Because the identity is shared and stateless from the gateway's perspective, *any subsequent order* whose postdispatch/predispatch calldata targets the same external protocol executes under the exact same `msg.sender` identity. If that protocol's accounting is keyed purely by caller address (as in the FraxLend example — `userCollateralBalance[_borrower]` credited to `msg.sender`), a later order's attacker-controlled calldata (e.g., a withdraw/borrow call against that protocol) can access or drain the position left behind by an unrelated, earlier order — turning a fund-lock bug into a fund-theft primitive, since `CallDispatcher.dispatch()` places no restriction on which calls can be chained and performs no isolation between unrelated orders' interactions with the same external contract.

`CallDispatcher` itself enforces no allowlisting beyond "target must have code": [1](#0-0) 

### Impact Explanation
Any external protocol reachable via `order.output.call`/`order.predispatch.call` that books positions to `msg.sender` will attribute funds to the shared `CallDispatcher` rather than the order's beneficiary. Because the dispatcher's identity is not isolated per order, value left behind by one order (locked collateral, deposited principal, accrued yield) is reachable by calldata from a different, later order targeting the same protocol — an unprivileged party (any solver/user who can shape `output.call`/`predispatch.call`) can potentially withdraw or redirect funds that rightfully belong to a different beneficiary. This matches the bounty's "stealing or loss of funds" / "wrong beneficiary" impact class.

### Likelihood Explanation
Likelihood depends on the specific external protocol integrated via calldata attributing ownership to `msg.sender` instead of an explicit beneficiary parameter — the same precondition Sturdy already acknowledged exists in real integrations (FraxLend). Given the intent-gateway calldata feature is explicitly documented as a general-purpose "route through DeFi protocol" mechanism (not restricted to a fixed allowlist of safe protocols), and `CallDispatcher` is shared across the entire gateway's order flow rather than deployed per-order or per-beneficiary, the precondition is realistic for any integrator building on this feature.

### Recommendation
- Deploy or derive a per-order (or per-beneficiary) `CallDispatcher` instance/proxy instead of one shared singleton, so any position an external protocol attributes to `msg.sender` is at least isolated per order rather than commingled across the entire gateway's history.
- Alternatively, require postdispatch/predispatch integrations to pass an explicit beneficiary parameter recognized by the target protocol, and document/enforce that only protocols supporting "act on behalf of" semantics may be targeted.
- Add an explicit balance-invariant check after `_execute()` confirming no unexpected residual liabilities/positions were created under the dispatcher's identity, or restrict `Call.to` to a governance-curated allowlist of protocols verified to route ownership correctly.

### Proof of Concept
1. Governance configures `_params.dispatcher` to a single `CallDispatcher` instance (as is standard, per `evm/src/apps/intentsv2/IntentsBase.sol`).
2. User A places an order whose `output.call` deposits solver-delivered output tokens into an external lending market that credits `userCollateralBalance[msg.sender]` (i.e., credits `CallDispatcher`), intending to later borrow on behalf of the beneficiary. `_execute()` runs the call, then sweeps only the declared output-asset balances back to the gateway — the collateral position remains booked to `CallDispatcher` in the external protocol, invisible to the sweep.
3. User B (or a colluding solver) places a subsequent order whose `output.call` targets the same lending market and calls `borrow()`/`withdrawCollateral()`, again executed with `msg.sender == CallDispatcher`. Because the lending market's accounting is keyed on caller identity, User B's calldata can draw against the collateral position left behind by User A's order — funds intended for User A are captured by an unrelated order.

### Citations

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-443)
```text
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L444-473)
```text
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

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L101-110)
```text
### Postdispatch

The `call` field in `PaymentInfo` contains calldata to execute *after* the order is filled. This enables fill-then-act patterns — for example, output tokens received from the solver are routed through a DeFi protocol before reaching the beneficiary.

Execution timing differs by mode:

- **Same-chain**: Calldata executes only after the order is **fully filled**. Partial fills do not trigger calldata — only the final fill that completes the order executes it. This ensures all output tokens are available when the calls run.
- **Cross-chain**: Calldata executes **immediately** after the solver delivers output tokens to the beneficiary, before the settlement message is dispatched back to the source chain.

After execution, any tokens remaining in the `CallDispatcher` are swept back to the gateway and collected as dust (emitting `DustCollected` for each token). When postdispatch calldata is present, 100% of any surplus (solver overpayment) goes to the protocol rather than being split with the beneficiary — this prevents manipulation of surplus distribution through calldata side effects.
```
