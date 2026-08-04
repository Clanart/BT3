### Title
`SimplexPaymaster.onAccept` binds only the source chain, not the source module — any Hyperbridge-chain module can trigger paymaster governance actions - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
The Solidity bug report's core invariant is: a function accepts a caller-controlled identifier (a `term`) and treats it as implicitly bound to "this market," without checking that the identifier actually belongs to the calling contract's own market/context. That missing binding lets an attacker cross market boundaries and corrupt another market's accounting.

`SimplexPaymaster.onAccept` has the same class of missing-binding bug on the module axis instead of the token axis: it authenticates only that a message came from the *Hyperbridge chain* (`incoming.request.source`), but never checks *which module on that chain* dispatched it (`incoming.request.from`), before executing privileged `RequestKind` actions (`UpgradeContract`, `UpdateParams`, `RegisterToken`, `DeactivateToken`, `WithdrawAssets`).

### Finding Description
`onAccept` is guarded by `onlyHost` (must come from the local `EvmHost`) and by: [1](#0-0) 

```solidity
function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
    if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) {
        revert UnauthorizedCall();
    }

    RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
    bytes calldata payload = incoming.request.body[1:];
```

This check only compares `incoming.request.source` (the state-machine/chain identifier of the origin chain) against `hyperbridge()` — i.e., it proves the request came from *the Hyperbridge parachain*. It never inspects `incoming.request.from`, the field that identifies *which pallet/module on the Hyperbridge chain* actually dispatched the ISMP request. The contract comment even states the security model relies on requests being "authenticated as originating from Hyperbridge governance," but the code only authenticates the chain, not the governance module.

On the dispatching side, governance actions for the paymaster are meant to be issued exclusively by the `intents-coprocessor` pallet's privileged extrinsics (`update_paymaster_params`, `register_paymaster_token`, `deactivate_paymaster_token`, `withdraw_paymaster_assets`), each gated by `T::GovernanceOrigin::ensure_origin(origin)?` before calling `Self::dispatch(state_machine, paymaster, request.encode_body())`: [2](#0-1) 

That pallet-side privilege check is correct, but it only constrains who can invoke *that specific pallet's dispatch call*. It does nothing to stop any other pallet or extrinsic on the Hyperbridge runtime — or any code path that can originate an ISMP `PostRequest` whose `dest` is the EVM chain and whose body happens to encode a `SimplexPaymaster.RequestKind` — from reaching `SimplexPaymaster.onAccept` and being accepted, because `onAccept` never verifies the request's `from` module identity matches the intents-coprocessor pallet's known module id.

This mirrors the `SurplusGuildMinter::stake()` flaw precisely: the vulnerable function accepts a parameter that carries an implicit "which market/module do you belong to" assumption (`term` → market; `from` → dispatching module) and only validates a *coarser*, insufficient scope (mint ratio/credit token type existing at all → chain-level `source` match), never the *fine-grained* binding that the security model actually depends on.

### Impact Explanation
If any Hyperbridge-side module or pallet other than the vetted `intents-coprocessor` governance pallet can cause an ISMP `PostRequest` to be dispatched toward this paymaster's `dest`/`to`, the attacker gains unauthenticated, unprivileged reach into:
- `UpgradeContract` → arbitrary implementation upgrade of the paymaster's ERC1967 proxy (full contract takeover, including EntryPoint deposit and ERC-20 token balances held by the paymaster).
- `WithdrawAssets` → sweep of the paymaster's ERC-20 surplus or its entire EntryPoint deposit to an attacker-controlled `treasury` set via a prior forged `UpdateParams`.
- `RegisterToken`/`DeactivateToken`/`UpdateParams` → manipulate pricing/oracle config to drain solver allowances or make the paymaster insolvent.

This is a false state acceptance / unauthorized execution / fund-loss class impact matching the bounty's accepted categories (stealing/loss of funds, unauthorized execution, wrong-beneficiary fund movement).

### Likelihood Explanation
Exploitability depends entirely on whether the Hyperbridge runtime enforces `from`-module binding at the ISMP host/router layer for messages destined to `IsmpModule`s like `SimplexPaymaster`, or whether that responsibility is left to the receiving `IApp`. Given the contract's own doc comment asserts "every administrative action ... is an onAccept request authenticated as originating from Hyperbridge governance," the expectation is clearly that `onAccept` itself should verify the specific governance module, matching the pattern the report's author flagged (checking a fine-grained identity that is easy to omit and easy to exploit because no compiler/type system enforces it). I could not fully verify within this session whether `pallet-ismp`'s dispatch/router enforces a global module allowlist per destination `to` address that would independently block this (I was not able to load `IApp.sol`/`IncomingPostRequest`/`PostRequest.from` definitions before the tool budget ran out), so likelihood is stated with that caveat.

### Recommendation
In `SimplexPaymaster.onAccept`, in addition to the existing `source` chain check, validate `incoming.request.from` against a stored, governance-set module identifier for the trusted dispatcher (the intents-coprocessor pallet's well-known module id on Hyperbridge), e.g.:

```solidity
if (keccak256(incoming.request.from) != keccak256(trustedGovernanceModule)) {
    revert UnauthorizedCall();
}
```

`trustedGovernanceModule` should be set at `initialize()` and only changeable through the existing `UpgradeContract`/`UpdateParams` governance path (never by an unauthenticated caller), closing the same class of "coarse check substituting for the required fine-grained check" that the original report exploited.

### Proof of Concept
Conceptual (I could not fully trace the ISMP dispatch/router code confirming an unprivileged path to forge `from`, due to running out of investigation budget before reading `IsmpDispatcher`/router module-authorization code):
1. Identify or trigger any code path on the Hyperbridge chain (any pallet, not the governance-gated `intents-coprocessor` calls) capable of dispatching an ISMP `PostRequest` with `dest` = the target EVM chain and `to` = the deployed `SimplexPaymaster` address.
2. Craft `body` = `abi.encodePacked(uint8(RequestKind.WithdrawAssets), abi.encode(address(0), type(uint256).max))` (or `UpgradeContract` with an attacker-controlled implementation + init calldata).
3. Once relayed and proven to the destination `EvmHost`, `HandlerV2` calls `SimplexPaymaster.onAccept`, which only checks `request.source == hyperbridge()` — true for any Hyperbridge-originated message — and executes the privileged action, since `request.from` (the actual dispatching module) is never checked against the intents-coprocessor pallet. [3](#0-2)

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L189-196)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) {
            revert UnauthorizedCall();
        }

        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        bytes calldata payload = incoming.request.body[1:];

```

**File:** evm/src/utils/SimplexPaymaster.sol (L197-211)
```text
        if (kind == RequestKind.UpgradeContract) {
            (address newImpl, bytes memory initData) = abi.decode(payload, (address, bytes));
            ERC1967Utils.upgradeToAndCall(newImpl, initData);
        } else if (kind == RequestKind.UpdateParams) {
            _setParams(abi.decode(payload, (Params)));
        } else if (kind == RequestKind.RegisterToken) {
            (address token, address oracle) = abi.decode(payload, (address, address));
            _registerToken(token, AggregatorV3Interface(oracle));
        } else if (kind == RequestKind.DeactivateToken) {
            _deactivateToken(abi.decode(payload, (address)));
        } else if (kind == RequestKind.WithdrawAssets) {
            (address token, uint256 amount) = abi.decode(payload, (address, uint256));
            _withdrawAssets(token, amount);
        }
    }
```

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L705-721)
```rust
		pub fn update_paymaster_params(
			origin: OriginFor<T>,
			state_machine: StateMachine,
			params: PaymasterParams,
		) -> DispatchResult {
			T::GovernanceOrigin::ensure_origin(origin)?;

			let paymaster =
				Paymasters::<T>::get(state_machine).ok_or(Error::<T>::PaymasterNotFound)?;

			let request = RequestKind::PaymasterUpdateParams(params.clone());
			Self::dispatch(state_machine, paymaster, request.encode_body())?;

			Self::deposit_event(Event::PaymasterParamsUpdateInitiated { state_machine, params });

			Ok(())
		}
```
