### Title
Tokenfactory `DenomAllowList` restriction can be bypassed by attaching allow-listed coins as `Funds` to a CosmWasm `MsgInstantiateContract`/`MsgExecuteContract` - (File: `sei-wasmd/x/wasm/keeper/keeper.go`)

### Summary
The bank module's tokenfactory `DenomAllowList` mechanism (Sei's on-chain analog to StakeWise's `whitelistedAccounts`) is enforced only in the bank `MsgServer.Send`/`MultiSend` handlers, but not in the CosmWasm keeper's internal coin transferrer used to move `Funds` attached to `MsgInstantiateContract`/`MsgExecuteContract`. This allows a user to move allow-listed tokenfactory denom coins to (or from) addresses that were never approved by the denom's allow list, exactly mirroring the StakeWise bug where the whitelist check existed on one transfer path (`deposit()`) but not another (`transfer()`/`transferFrom()`).

### Finding Description
Sei's bank module implements an admin-configurable allow list for tokenfactory denoms, analogous to StakeWise's per-vault whitelist. `SetDenomAllowList`/`GetDenomAllowList` store the allow list [1](#0-0) , and `IsInDenomAllowList` is the enforcement check [2](#0-1) .

This check is only invoked from the bank module's `MsgServer.Send` and `MsgServer.MultiSend`, which verify both sender and receiver against the allow list before calling `SendCoins`: [3](#0-2) [4](#0-3) 

However, the CosmWasm keeper has its own coin-transfer path used to move `Funds` attached to `MsgInstantiateContract`/`MsgExecuteContract` into/out of contract addresses — `BankCoinTransferrer.TransferCoins`. This function only checks `IsSendEnabledCoins` and `BlockedAddr`, and calls the bank keeper's low-level `SendCoins` directly, completely skipping `IsInDenomAllowList`: [5](#0-4) 

Because `TransferCoins` calls `c.keeper.SendCoins` (the raw keeper method) instead of going through `bank.MsgServer.Send`, any allow-list restriction placed on a tokenfactory denom is silently bypassed whenever that denom is sent as `Funds` on a contract instantiate/execute message.

### Impact Explanation
A tokenfactory denom creator can restrict a denom to a fixed set of approved addresses via `SetDenomAllowList` (exposed through `MsgCreateDenom`'s `AllowList` field, per `x/tokenfactory/keeper/msg_server.go`). Any unprivileged user can defeat this restriction by wrapping a transfer of the allow-listed coin as `Funds` on a `MsgExecuteContract`/`MsgInstantiateContract` call to an arbitrary CosmWasm contract (e.g., a simple pass-through/relay contract that forwards the received funds back out via a `BankMsg::Send` submessage, which is itself dispatched as a `MsgSend` and thus would be checked - but the initial hop into the contract, or a hop into a contract that is NOT itself in the allow list, is unchecked). This defeats the denom-level access control the creator/authority intended to enforce, allowing funds to reach addresses that were never approved — the same class of "whitelist enforced on one path but not another" bug as the original StakeWise finding.

### Likelihood Explanation
High reachability: any unprivileged account can submit a standard `MsgExecuteContract` or `MsgInstantiateContract` with `Funds` in an allow-listed tokenfactory denom, targeting any deployed CosmWasm contract address. No special privileges, precompiles, or governance actions are required — this is a plain CW message from a public RPC client.

### Recommendation
In `sei-wasmd/x/wasm/keeper/keeper.go`, `BankCoinTransferrer.TransferCoins` should perform the same `IsInDenomAllowList` checks (for both `fromAddr` and `toAddr`) that `bank.MsgServer.Send`/`MultiSend` perform, mirroring the recommended fix pattern from the original report (enforce the whitelist/allow-list check uniformly across every code path that can move the restricted asset, not just the primary entry point).

### Proof of Concept
1. Denom creator calls `MsgCreateDenom` for `factory/<creator>/restricted` with an `AllowList` containing only `{creator}` (or a small approved set) — enforced via `SetDenomAllowList` in `x/tokenfactory/keeper/msg_server.go` (lines 37-46, see `CreateDenom`).
2. Creator funds their own account with the restricted denom (mint via tokenfactory).
3. Creator submits `MsgExecuteContract{Contract: <any_contract_addr_not_in_allow_list>, Funds: [{denom: "factory/<creator>/restricted", amount: N}]}`.
4. The wasm keeper forwards the funds to the contract using `BankCoinTransferrer.TransferCoins` (`sei-wasmd/x/wasm/keeper/keeper.go:1198-1221`), which only checks `IsSendEnabledCoins`/`BlockedAddr`, not `IsInDenomAllowList` — the transfer succeeds even though `<contract_addr>` is not on the allow list, whereas an equivalent `MsgSend` to the same non-whitelisted address via the bank module would fail with `"... is not allowed to receive funds"` (as demonstrated in `sei-cosmos/x/bank/app_test.go` `TestSendReceiverNotInAllowList`, lines 117-149).

Note: I was not able to fully trace every downstream code path (e.g., whether the receiving contract itself immediately re-exports funds via a `BankMsg::Send` submessage, which would then hit the checked `MsgSend` path) — confirming the complete end-to-end fund-exfiltration scenario (vs. just landing restricted funds on a non-approved contract address) would benefit from a live Devin session to write and run an integration test against `sei-wasmd`.

### Citations

**File:** sei-cosmos/x/bank/keeper/send.go (L478-499)
```go
func (k BaseSendKeeper) SetDenomAllowList(ctx sdk.Context, denom string, allowList types.AllowList) {
	store := ctx.KVStore(k.storeKey)
	denomAllowListStore := prefix.NewStore(store, types.DenomAllowListKey(denom))

	m := k.cdc.MustMarshal(&allowList)
	denomAllowListStore.Set([]byte(denom), m)
}

func (k BaseSendKeeper) GetDenomAllowList(ctx sdk.Context, denom string) types.AllowList {
	store := ctx.KVStore(k.storeKey)
	store = prefix.NewStore(store, types.DenomAllowListKey(denom))

	bz := store.Get([]byte(denom))
	if bz == nil {
		return types.AllowList{}
	}

	var allowList types.AllowList
	k.cdc.MustUnmarshal(bz, &allowList)

	return allowList
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

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-54)
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

	err = k.SendCoins(ctx, from, to, msg.Amount)
	if err != nil {
		return nil, err
	}
```

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L74-94)
```go
func (k msgServer) MultiSend(goCtx context.Context, msg *types.MsgMultiSend) (*types.MsgMultiSendResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	denomToAllowListCache := make(map[string]AllowedAddresses)
	// NOTE: totalIn == totalOut should already have been checked
	for _, in := range msg.Inputs {
		if err := k.IsSendEnabledCoins(ctx, in.Coins...); err != nil {
			return nil, err
		}
		accAddr := sdk.MustAccAddressFromBech32(in.Address)
		if !k.IsInDenomAllowList(ctx, accAddr, in.Coins, denomToAllowListCache) {
			return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to send funds", accAddr)
		}
	}

	for _, out := range msg.Outputs {
		accAddr := sdk.MustAccAddressFromBech32(out.Address)

		if k.BlockedAddr(accAddr) || !k.IsInDenomAllowList(ctx, accAddr, out.Coins, denomToAllowListCache) {
			return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", out.Address)
		}
	}
```

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L1186-1221)
```go
// BankCoinTransferrer replicates the cosmos-sdk behaviour as in
// https://github.com/cosmos/cosmos-sdk/blob/v0.41.4/x/bank/keeper/msg_server.go#L26
type BankCoinTransferrer struct {
	keeper types.BankKeeper
}

func NewBankCoinTransferrer(keeper types.BankKeeper) BankCoinTransferrer {
	return BankCoinTransferrer{
		keeper: keeper,
	}
}

// TransferCoins transfers coins from source to destination account when coin send was enabled for them and the recipient
// is not in the blocked address list.
func (c BankCoinTransferrer) TransferCoins(parentCtx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amount sdk.Coins) error {
	em := sdk.NewEventManager()
	ctx := parentCtx.WithEventManager(em)
	if err := c.keeper.IsSendEnabledCoins(ctx, amount...); err != nil {
		return err
	}
	if c.keeper.BlockedAddr(toAddr) {
		return sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", toAddr.String())
	}

	sdkerr := c.keeper.SendCoins(ctx, fromAddr, toAddr, amount)
	if sdkerr != nil {
		return sdkerr
	}
	for _, e := range em.Events() {
		if e.Type == sdk.EventTypeMessage { // skip messages as we talk to the keeper directly
			continue
		}
		parentCtx.EventManager().EmitEvent(e)
	}
	return nil
}
```
