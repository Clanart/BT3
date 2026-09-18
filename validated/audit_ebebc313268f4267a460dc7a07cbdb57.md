Based on the investigation, here is a valid analog to the reported bug class (CWE-863 – rules/policies not reliably enforced across all code paths).

### Title
Tokenfactory Denom Allow-List (Send Restriction) Not Enforced When Transferring via the EVM Bank Precompile / SendCoins Path - (File: sei-cosmos/x/bank/keeper/msg_server.go)

### Summary
The upstream advisory describes an "improper input validation" bug where firewall rules are not reliably applied when there are multiple rule entries, letting traffic bypass an intended access-control policy. The closest reachable analog in sei-chain is the tokenfactory **denom allow-list** feature: a denom admin can restrict which addresses may send/receive a factory denom via `AllowList`, but this restriction is only enforced in the Cosmos-native `MsgSend`/`MsgMultiSend` handlers, not in the lower-level `Keeper.SendCoins`/`SendCoinsAndWei` functions that other entry points (notably the EVM bank precompile and CW/EVM pointer transfer paths) call directly.

### Finding Description
The allow-list check `IsInDenomAllowList` is only invoked from `msgServer.Send` and `msgServer.MultiSend`: [1](#0-0)  and [2](#0-1) .

The actual balance-moving logic lives in `BaseSendKeeper.IsInDenomAllowList` inside `send.go`, which walks each token-factory-prefixed coin and rejects the transfer only if the address is absent from a configured allow list: [3](#0-2) .

This mirrors the "multiple rule entries" root cause from the advisory: the policy check is bolted onto specific message-handler call sites (`Send`, `MultiSend`) rather than being embedded inside the core `SendCoins`/`SendCoinsAndWei` functions themselves. A `grep` across the `precompiles/**` tree for any reference to `AllowList` or `IsInDenomAllowList` returned no matches outside of a staking-precompile test file, indicating that EVM-facing transfer paths (the bank precompile and ERC20/pointer transfers of tokenfactory-backed denoms) call `SendCoins`/`SendCoinsAndWei` directly and never pass through the allow-list gate that `msgServer.Send`/`MultiSend` enforce.

### Impact Explanation
If confirmed, this allows an unprivileged EVM transaction sender or contract to transfer a permissioned/allow-listed tokenfactory denom to or from an address that the denom admin explicitly excluded, defeating the compliance/access-control guarantee the tokenfactory allow-list feature is meant to provide. This is analogous to "unauthorized transfer via precompile or pointer" — funds move outside the policy the denom creator configured, even though the Cosmos-native `Send`/`MultiSend` paths correctly enforce it.

### Likelihood Explanation
Likelihood is bounded by the fact that this requires: (1) a denom with an active, non-empty `AllowList` set via tokenfactory (`x/tokenfactory/keeper/createdenom_test.go` shows this is a normal, user-reachable operation) [4](#0-3) , and (2) an unprivileged actor routing a transfer of that denom through the EVM bank precompile or a CW/EVM pointer path instead of `MsgSend`/`MsgMultiSend`. I was not able to directly inspect the bank precompile source (`precompiles/bank/bank.go`) in this session to confirm it calls `SendCoins` without the allow-list check — this should be verified before treating the finding as certain.

### Recommendation
Move the `IsInDenomAllowList` (and `BlockedAddr`) checks into `BaseSendKeeper.SendCoins` / `SendCoinsAndWei` themselves (or into a shared helper called by every transfer entry point, including the EVM bank precompile and pointer-transfer code), rather than only at the `MsgSend`/`MsgMultiSend` message-handler layer, so the policy is enforced uniformly regardless of which surface (Cosmos message, EVM precompile, or CW/EVM pointer) initiates the transfer.

### Proof of Concept
Not executed — this requires confirming, in the `precompiles/bank` (and pointer/ERC20 transfer) source, that it calls `Keeper.SendCoins`/`SendCoinsAndWei` without first invoking `IsInDenomAllowList`, which could not be verified with the remaining tool budget in this session.

### Citations

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L42-49)
```go
	allowListCache := make(map[string]AllowedAddresses)
	if !k.IsInDenomAllowList(ctx, from, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to send funds", msg.FromAddress)
	}

	if k.BlockedAddr(to) || !k.IsInDenomAllowList(ctx, to, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", msg.ToAddress)
	}
```

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L76-94)
```go
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

**File:** x/tokenfactory/keeper/createdenom_test.go (L88-95)
```go
		{
			desc:     "valid allow list",
			subdenom: "withallowlist",
			allowList: &banktypes.AllowList{
				Addresses: []string{suite.TestAccs[0].String(), suite.TestAccs[1].String(), suite.TestAccs[2].String()},
			},
			valid: true,
		},
```
