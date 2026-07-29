### Title
Premature Token-Pair Deletion on Self-Destruct Permanently Freezes Outstanding Native Coin Balances - (File: x/erc20/keeper/msg_server.go)

### Summary
The `ConvertERC20` and `ConvertCoin` handlers use a superficial "is the contract gone" heuristic (`acc == nil || !acc.HasCodeHash()`) to decide whether an ERC20 token pair should be permanently deleted from state. This mirrors the Radiant `_checkNoLiquidity` bug class: a proxy check (contract code presence) stands in for the real invariant (no outstanding claims against the pair), so a token pair can be deleted from state while native Cosmos coin balances of that pair's denom are still held by other users, permanently orphaning their value.

### Finding Description
`ConvertERC20` and `ConvertCoin` both contain the same pattern: [1](#0-0) [2](#0-1) 

When the underlying ERC20 contract account is `nil` or lacks a code hash, the keeper immediately calls `k.DeleteTokenPair(ctx, pair)`, which removes the ERC20↔denom mapping entirely: [3](#0-2) 

This deletion is irreversible and unconditional — it does not check whether other accounts still hold native Cosmos coins of `pair.Denom` (minted previously via `ConvertERC20`/`convertERC20IntoCoinsForNativeToken`, which mints bank coins 1:1 against escrowed ERC20 tokens). Since `RegisterERC20` is permissionless when `params.PermissionlessRegistration` is enabled: [4](#0-3) 

any user can deploy an arbitrary ERC20 contract (including one with a self-destruct path), register it as a token pair, invite other users to convert their ERC20 balances into native coins of that denom (escrowing real ERC20 value and minting bank coins), then self-destruct the contract. The very next `ConvertERC20`/`ConvertCoin` call against that pair (which can be triggered by anyone, including the attacker themselves with a trivial dust amount) deletes the token pair mapping — `deleteERC20Map` and `deleteDenomMap` both fire — permanently severing any path to redeem the outstanding native coin balance back into ERC20 value, since `GetTokenPairID`/`GetTokenPair` will report "not found" for that denom going forward. The IBC timeout/ack path comment acknowledges the self-destruct scenario exists but doesn't address the case of *other* users' already-converted balances being orphaned by the deletion.

### Impact Explanation
Any native Cosmos coin balance of the deleted pair's denom held by users other than the contract-destroyer becomes permanently unredeemable — it can never again be converted back into the ERC20 representation, nor is there any compensating burn/refund mechanism. This is a permanent freezing of token-pair-backed value triggered by an unprivileged action (self-destructing a permissionlessly-registered contract), matching the Critical "permanent freezing... of token-pair-backed balances" impact category.

### Likelihood Explanation
Requires `PermissionlessRegistration` to be enabled and requires the malicious registrant to control a self-destructible contract and successfully attract other users to convert into that specific pair before destroying it — a moderately involved but fully unprivileged and permissionless attack path requiring no governance or validator privilege.

### Recommendation
Before calling `DeleteTokenPair` on detecting a destroyed/missing contract, check the total supply of `pair.Denom` outstanding in the bank module (`bankKeeper.GetSupply`). If nonzero, either refuse to delete the mapping (leave it registered so ordinary bank transfers keep working, only conversion into ERC20 remains impossible) or provide a governance/refund path to make holders whole before removing the mapping.

### Proof of Concept
1. Enable/assume `params.PermissionlessRegistration = true`.
2. Attacker deploys `EvilERC20`, a minter/burner ERC20 with an admin-only `kill()` function that calls `SELFDESTRUCT`.
3. Attacker calls `MsgRegisterERC20` for `EvilERC20` → token pair `(EvilERC20, denom=D)` is created.
4. Attacker mints tokens to Victim; Victim calls `MsgConvertERC20` to convert N tokens into N native coins of denom `D` (escrowing N ERC20 tokens in the module account, minting N coins of `D` to Victim).
5. Attacker calls `kill()` on `EvilERC20`, invoking `x/vm` `DeleteAccount`/`SelfDestruct`.
6. Attacker (or anyone) submits `MsgConvertERC20`/`MsgConvertCoin` referencing the pair with a trivial amount; `k.evmKeeper.GetAccountWithoutBalance` returns `nil`/no code hash, so `k.DeleteTokenPair` is called, removing the `erc20↔denom` mapping.
7. Victim still holds N coins of denom `D` in their bank balance, but `GetTokenPairID(ctx, D)` now returns empty — any future `ConvertCoin` for denom `D` fails with `ErrTokenPairNotFound`. Victim's N coins are permanently stranded with no redemption path.

### Citations

**File:** x/erc20/keeper/msg_server.go (L42-53)
```go
	if pair.IsNativeERC20() {
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
		}
```

**File:** x/erc20/keeper/msg_server.go (L209-220)
```go
	case pair.IsNativeERC20():
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
		}
```

**File:** x/erc20/keeper/msg_server.go (L326-336)
```go
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}

```

**File:** x/erc20/keeper/token_pairs.go (L110-117)
```go
// DeleteTokenPair removes a token pair.
func (k Keeper) DeleteTokenPair(ctx sdk.Context, tokenPair types.TokenPair) {
	id := tokenPair.GetID()
	k.deleteTokenPair(ctx, id)
	k.deleteERC20Map(ctx, tokenPair.GetERC20Contract())
	k.deleteDenomMap(ctx, tokenPair.Denom)
	k.deleteAllowances(ctx, tokenPair.GetERC20Contract())
}
```
