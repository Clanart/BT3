### Title
Tokenfactory denom admin can frontrun a pending `MsgSend`/precompile transfer by installing an `AllowList` mid-flight, griefing or permanently blocking counterparties' funds - ([File: x/tokenfactory/keeper/msg_server.go])

### Summary
The Teller finding is a case of a resource-scoped, non-governance "owner" role (marketplace owner) observing a pending transaction in the mempool and mutating parameters that the transaction's execution depends on, before the victim's transaction lands, in order to cause fund loss or freezing. The closest reachable analog in sei-chain is `x/tokenfactory`'s per-denom "admin" role, which is not governance and not a node operator - it is simply whoever created (or was later assigned as admin of) a `factory/{creator}/{subdenom}` denom via the permissionless `MsgCreateDenom`/`MsgChangeAdmin` messages [1](#0-0) . This admin can call `MsgUpdateDenom` at any time to install a bank `AllowList` on their denom [2](#0-1) , and the bank keeper enforces that allow list on every `MsgSend`/`MsgMultiSend` (and by extension any precompile/wasm path that routes through `SendCoins`) for both the sender and the receiver [3](#0-2) .

### Finding Description
`IsInDenomAllowList` is evaluated at the moment the transfer executes, not at the moment it is signed/broadcast [4](#0-3) :
```go
func (k BaseSendKeeper) IsInDenomAllowList(ctx sdk.Context, addr sdk.AccAddress, coins sdk.Coins, cache map[string]AllowedAddresses) bool {
	for _, coin := range coins {
		if !strings.HasPrefix(coin.Denom, TokenFactoryPrefix) { continue }
		allowedAddresses := k.getAllowedAddresses(ctx, cache, coin.Denom)
		if len(allowedAddresses.set) == 0 { continue }
		if !allowedAddresses.contains(addr) { return false }
	}
	return true
}
```
`MsgSend`'s handler calls this for both the `from` and `to` address and rejects the transfer if either fails the check [5](#0-4) . Because a denom's `AllowList` can be replaced wholesale at any time by `MsgUpdateDenom`, an attacker who is the admin of `factory/{attacker}/{subdenom}` can watch the mempool for a victim's pending transfer of that denom (e.g. a swap settlement, an escrow release, a payment to a specific counterparty) and submit a `MsgUpdateDenom` with a new `AllowList` that deliberately excludes the victim's receiving/sending address, landing in an earlier position in the same block. The victim's transfer then reverts with `"... is not allowed to send/receive funds"` even though it was valid and fully funded when it was constructed and broadcast.

This mirrors the Teller root cause precisely: a resource-scoped, non-governance owner (marketplace owner / here, tokenfactory denom admin) is able to race-condition a counterparty's pending transaction by mutating parameters that the transaction implicitly (not explicitly) commits to, with no way for the victim to bind their transaction to the parameter state they observed when signing.

### Impact Explanation
Because the AllowList admin controls both restriction *and* subsequent selective admission, this is not just griefing:
- The admin can time the exclusion to fail only the specific counterparty's leg of a multi-party flow (e.g., a swap/settlement contract that already released the other side of a trade, or an escrow contract that has already paid out its portion based on an expected inbound transfer), permanently freezing the victim's expected proceeds while the admin's own address (already in or re-added to the allow list) can continue to move the denom freely.
- Because `AllowList` is denom-scoped and enforced identically for a permissionless, self-service `factory/...` denom, this attack requires no governance action, no validator collusion, and no operator privilege — any account that created the denom (or was later handed admin rights via `MsgChangeAdmin`) can execute it against any counterparty transacting in that denom, satisfying the "unprivileged tx sender ... tokenfactory denom creator" reachability requirement.
- The affected transfer path is generic (`bank.MsgSend`/`MsgMultiSend`), so any dApp, precompile-based EVM caller, or CW contract that moves a tokenfactory denom is exposed whenever it interacts with a counterparty-supplied tokenfactory denom it does not fully trust.

### Likelihood Explanation
Likelihood is high for any protocol built on tokenfactory denoms where the denom admin and a counterparty are not the same trusted entity (e.g., permissionless swap/DEX/escrow flows accepting arbitrary `factory/...` denoms). The admin only needs to observe the counterparty's pending transaction and submit a single `MsgUpdateDenom` ahead of it in the same block — no special access, no cross-chain timing, and no cost beyond a normal transaction fee.

### Recommendation
- Require that a transaction referencing a tokenfactory denom for a specific counterparty commit to (hash and validate) the `AllowList` state it was built against, similarly to the Teller fix's suggested "commit to market parameters at submission time and revert if they conflict" pattern, or
- Rate-limit / delay `AllowList` changes (e.g., only take effect after a cooldown of N blocks) so pending transactions cannot be invalidated by an admin update landing in the same or next block, or
- Make `AllowList` additive-only during a single block (union of old and new set) rather than a full replace, preventing an admin from *removing* an address's send/receive rights within a block where a transfer targeting that address is already pending.

### Proof of Concept
1. Attacker calls `MsgCreateDenom` to create `factory/{attacker}/foo`, becoming its admin [6](#0-5) .
2. Attacker mints `foo` and sends it to Victim as payment for some off-chain/on-chain consideration (e.g., in exchange for goods, or as a swap leg in a contract that Victim then needs to move onward to a third party or withdraw).
3. Victim broadcasts `MsgSend` (or a wasm/precompile path that calls `SendCoins`) moving `foo` onward to a Recipient it has already committed to.
4. Attacker observes Victim's pending transaction and, in the same block, submits `MsgUpdateDenom` for `foo` setting `AllowList.Addresses` to a set that excludes Recipient (or excludes Victim itself) [2](#0-1) .
5. Victim's `MsgSend` executes after the admin's update and is rejected by `IsInDenomAllowList` with `"... is not allowed to receive funds"` / `"... is not allowed to send funds"` [5](#0-4) , even though the transaction was fully valid when Victim signed and broadcast it, and any value Victim had already committed elsewhere in reliance on this transfer succeeding (e.g., a corresponding leg already executed in a swap/escrow contract) is now unrecoverable or frozen.

Note: I was not able to fully verify, within the available tooling, whether any in-repo DEX/escrow/swap module composes with tokenfactory denoms in a way that guarantees a concrete "fund loss" beyond griefing (a reverted transfer with no state change) versus a scenario where a counterparty has already irreversibly committed value elsewhere. This composition would need to be confirmed against the specific dApp/module using tokenfactory denoms to fully establish "concrete fund loss" versus reversible griefing; I'd recommend starting a full Devin session with repository access to trace all callers of `bankKeeper.SendCoins`/`InputOutputCoins` that combine tokenfactory denoms with escrow-like semantics to confirm severity.

### Citations

**File:** x/tokenfactory/README.md (L1-18)
```markdown
# Token Factory

The tokenfactory module allows any account to create a new token with
the name `factory/{creator address}/{subdenom}`. Because tokens are
namespaced by creator address, this allows token minting to be
permissionless, due to not needing to resolve name collisions. A single
account can create multiple denoms, by providing a unique subdenom for each
created denom. Once a denom is created, the original creator is given
"admin" privileges over the asset. This allows them to:

- Mint their denom to any account
- Burn their denom from any account
- Create a transfer of their denom between any two accounts
- Change the admin. In the future, more admin capabilities may be added. Admins
  can choose to share admin privileges with other accounts using the authz
  module. The `ChangeAdmin` functionality, allows changing the master admin
  account, or even setting it to `""`, meaning no account has admin privileges
  of the asset.
```

**File:** x/tokenfactory/keeper/msg_server.go (L23-55)
```go
func (server msgServer) CreateDenom(goCtx context.Context, msg *types.MsgCreateDenom) (*types.MsgCreateDenomResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	denom, err := server.Keeper.CreateDenom(ctx, msg.Sender, msg.Subdenom)
	if err != nil {
		return nil, err
	}

	createDenomEvent := sdk.NewEvent(
		types.TypeMsgCreateDenom,
		sdk.NewAttribute(types.AttributeCreator, msg.Sender),
		sdk.NewAttribute(types.AttributeNewTokenDenom, denom),
	)

	if msg.AllowList != nil {
		err = server.validateAllowList(ctx, msg.AllowList)
		if err != nil {
			return nil, err
		}
		server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)
		createDenomEvent = createDenomEvent.AppendAttributes(
			sdk.NewAttribute(types.AttributeAllowList, strings.Join(msg.AllowList.Addresses, ",")),
		)
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		createDenomEvent,
	})

	return &types.MsgCreateDenomResponse{
		NewTokenDenom: denom,
	}, nil
}
```

**File:** x/tokenfactory/keeper/msg_server.go (L57-92)
```go
func (server msgServer) UpdateDenom(goCtx context.Context, msg *types.MsgUpdateDenom) (*types.MsgUpdateDenomResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	denom, err := server.validateUpdateDenom(ctx, msg)
	if err != nil {
		return nil, err
	}

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, denom)
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	updateDenomEvent := sdk.NewEvent(
		types.TypeMsgUpdateDenom,
		sdk.NewAttribute(types.AttributeCreator, msg.Sender),
		sdk.NewAttribute(types.AttributeUpdatedTokenDenom, denom),
	)

	if msg.AllowList != nil {
		server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)
		updateDenomEvent = updateDenomEvent.AppendAttributes(
			sdk.NewAttribute(types.AttributeAllowList, strings.Join(msg.AllowList.Addresses, ",")),
		)
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		updateDenomEvent,
	})

	return &types.MsgUpdateDenomResponse{}, nil
}
```

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-49)
```go
func (k msgServer) Send(goCtx context.Context, msg *types.MsgSend) (*types.MsgSendResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	if err := k.IsSendEnabledCoins(ctx, msg.Amount...); err != nil {
		return nil, err
	}

	from, err := sdk.AccAddressFromBech32(msg.FromAddress)
	if err != nil {
		return nil, err
	}
	to, err := sdk.AccAddressFromBech32(msg.ToAddress)
	if err != nil {
		return nil, err
	}

	allowListCache := make(map[string]AllowedAddresses)
	if !k.IsInDenomAllowList(ctx, from, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to send funds", msg.FromAddress)
	}

	if k.BlockedAddr(to) || !k.IsInDenomAllowList(ctx, to, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", msg.ToAddress)
	}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L501-524)
```go
// IsInDenomAllowList checks if the given address is allowed to send the given coins.
// The check is performed only fot token factory denoms. For each token factory denom,
// it checks if there is allow list for the given denom. If there is no allow list,
// the address is allowed to send the coins. If there is an allow list, the address is
// allowed to send the coins only if it is in the allow list.
func (k BaseSendKeeper) IsInDenomAllowList(ctx sdk.Context, addr sdk.AccAddress, coins sdk.Coins, cache map[string]AllowedAddresses) bool {
	for _, coin := range coins {
		// Skip if denom does not contain the token factory prefix
		if !strings.HasPrefix(coin.Denom, TokenFactoryPrefix) {
			continue
		}

		allowedAddresses := k.getAllowedAddresses(ctx, cache, coin.Denom)
		// skip if there is no allow list for the denom
		if len(allowedAddresses.set) == 0 {
			continue
		}

		if !allowedAddresses.contains(addr) {
			return false
		}
	}
	return true
}
```
