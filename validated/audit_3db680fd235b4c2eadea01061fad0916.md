### Title
Self-destructed native-ERC20 token pairs are deleted without reconciling outstanding bank-coin supply, permanently freezing escrowed collateral and enabling resurrection-based unbacked minting - (File: `x/erc20/keeper/msg_server.go`)

### Summary
This maps to the same bug class as the reported Solana issuance-index bug: a security-critical decision (whether it is still safe to treat a token-pair mapping / escrow as valid) is made using a **stale, insufficient check** instead of verifying the actual current state that the invariant depends on. In `push-chain-evm--015`, `ConvertERC20`/`ConvertCoin` decide to permanently delete a native ERC20 `TokenPair` purely by checking whether the EVM account for the contract still exists and has code (`acc == nil || !acc.HasCodeHash()`), never checking whether the corresponding native bank-coin denom for that pair still has outstanding circulating supply. This breaks the 1:1 escrow invariant between the ERC20 side and the Cosmos-coin side of a `TokenPair`, exactly like the original bug allowed an outdated/removed price index to be treated as current.

### Finding Description
`ConvertERC20` and `ConvertCoin` in `x/erc20/keeper/msg_server.go` both contain this pattern: [1](#0-0) 

```go
if pair.IsNativeERC20() {
    // Remove token pair if contract is suicided
    acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
    if acc == nil || !acc.HasCodeHash() {
        k.DeleteTokenPair(ctx, pair)
        ...
        // NOTE: return nil error to persist the changes from the deletion
        return nil, nil
    }
    ...
```

The same block exists in `ConvertCoin`. [2](#0-1) 

The token pair's `Denom` for a native ERC20 pair is deterministically derived from the contract address (`erc20/<address>`), and this denom has bank-module coin supply that was minted 1:1 whenever a user previously called `ConvertERC20` (escrowing ERC20 tokens into `types.ModuleAddress` and minting bank coins) — see `convertERC20IntoCoinsForNativeToken`: [3](#0-2) 

When the underlying ERC20 contract self-destructs, `DeleteTokenPair` is invoked with **no check** on:
1. Whether the bank module still has non-zero circulating supply of `pair.Denom` (i.e., whether unredeemed ERC20-backed coins are still held by users), and
2. Whether the ERC20 tokens escrowed in `types.ModuleAddress` for that contract are still recoverable.

Once the account is destroyed and the pair mapping removed, `MintingEnabled` (called at the top of both `ConvertERC20`/`ConvertCoin`) will subsequently return `ErrTokenPairNotFound` for that denom, so any holder of the still-circulating bank coin denom can never convert it back into ERC20 tokens — the escrowed ERC20 collateral becomes **permanently unreachable/frozen**, yet the bank coins remain fully valid, transferable, and spendable everywhere else in the chain (staking, IBC, precompiles, etc.). This is precisely the invariant that x/erc20 is supposed to preserve per the "Asset-representation path" pivot (1:1 accounting between native coins and ERC20 views).

Furthermore, `RegisterERC20` can be **permissionless** depending on chain params: [4](#0-3) 

If a new contract is later deployed/registered at the same address (e.g., via `CREATE2` redeploy within the same tx per EIP-6780 self-destruct semantics, or any future reuse of that address), `RegisterERC20` will create a brand-new `TokenPair` with the exact same deterministic `denom` as the destroyed one. Any user still holding the old, never-reconciled bank coins of that denom can immediately call `ConvertCoin` against the new pair to receive freshly-minted ERC20 tokens on the new contract, extracting real value that was never actually backed by the new contract's escrow — a resurrection/duplication of value analogous to the original report's "old issuance account with a lower bond price" exploit.

### Impact Explanation
This falls under the Critical impact gate for:
- Permanent freezing/locking of escrowed assets: ERC20 tokens escrowed in `types.ModuleAddress` become unreachable once the token pair is deleted, with no burn/reconciliation of the matching bank coin supply.
- Unauthorized minting/duplication/resurrection of spendable user value: if the same contract address is ever reused/re-registered, stale un-reconciled bank coins can be redeemed against a new, unrelated ERC20 contract, minting tokens without actual backing.

### Likelihood Explanation
The freezing half of this issue is trivially triggerable by any unprivileged user: any account holding a "native ERC20" `OWNER_EXTERNAL` contract can self-destruct their own contract (a normal, permitted EVM operation) while other users still hold the corresponding bank-coin denom; the very next `ConvertERC20`/`ConvertCoin` call on that pair (or any subsequent one) silently deletes the mapping without reconciling supply. The resurrection/duplication half additionally requires the contract address to later be reused with new code and (if `PermissionlessRegistration` is disabled) a governance-approved `RegisterERC20`, which raises the bar but does not require any privileged internal state — the exploit is possible purely through the module's own message-handling logic once the address is reused.

### Recommendation
Before calling `k.DeleteTokenPair`, verify `k.bankKeeper.GetSupply(ctx, pair.Denom).IsZero()`; if non-zero, refuse to delete the pair (or provide a governance-gated migration/burn path) so that outstanding bank coins remain redeemable, and prevent `RegisterERC20` from creating a new pair whose deterministic denom collides with an existing non-zero bank supply from a previously deleted pair.

### Proof of Concept
1. Governance/permissionless flow registers `OWNER_EXTERNAL` native ERC20 contract `C` at address `A`, creating `TokenPair{Erc20Address: A, Denom: "erc20/A"}`.
2. User `U1` calls `ConvertERC20` to escrow `100` tokens of `C` into `types.ModuleAddress` and receive `100 erc20/A` bank coins.
3. Contract `C`'s owner calls `selfdestruct()` on `C`.
4. Any subsequent `ConvertERC20`/`ConvertCoin` call referencing pair `A` observes `acc == nil` and calls `k.DeleteTokenPair`, removing the mapping — with no check that `U1` still holds `100 erc20/A` in the bank module.
5. `U1`'s `100 erc20/A` coins remain fully transferable/spendable, but the `100` tokens of `C` escrowed in `types.ModuleAddress` can never be recovered (no live token pair, `MintingEnabled` now fails with `ErrTokenPairNotFound`).
6. If a new contract is later deployed/registered at address `A` (same-tx `CREATE2` redeploy, or address reuse), a new `TokenPair{Erc20Address: A, Denom: "erc20/A"}` is created; `U1` calls `ConvertCoin` and receives freshly minted ERC20 tokens on the new contract for the old, unrelated `100 erc20/A` coins — value duplicated with no real backing.

**Uncertainty / what I could not fully verify:** I was not able to inspect `x/erc20/keeper/token_pairs.go` (`registerERC20`, `DeleteTokenPair`, `CreateNewTokenPair`) in full to confirm whether `registerERC20` has any guard against re-registering an address whose token pair was previously deleted, or whether `DeleteTokenPair` removes denom/ERC20 index entries in a way that would additionally block step 6. I also could not confirm the exact self-destruct/redeploy semantics implemented in this fork's EVM configuration (e.g., whether EIP-6780 same-transaction-only self-destruct is enforced), which affects how easily an attacker can redeploy new code at the same address. The freezing impact (step 1–5) does not depend on this and is confirmed directly from the read code.

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

**File:** x/erc20/keeper/msg_server.go (L63-140)
```go
// convertERC20IntoCoinsForNativeToken handles the erc20 conversion for a native erc20 token
// pair:
//   - escrow tokens on module account
//   - mint coins on bank module
//   - send minted coins to the receiver
//   - check if coin balance increased by amount
//   - check if token balance decreased by amount
//   - check for unexpected `Approval` event in logs
func (k Keeper) convertERC20IntoCoinsForNativeToken(
	ctx sdk.Context,
	pair types.TokenPair,
	msg *types.MsgConvertERC20,
	receiver sdk.AccAddress,
	sender common.Address,
) (*types.MsgConvertERC20Response, error) {
	erc20 := contracts.ERC20MinterBurnerDecimalsContract.ABI
	contract := pair.GetERC20Contract()
	balanceCoin := k.bankKeeper.GetBalance(ctx, receiver, pair.Denom)
	balanceToken := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceToken == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	// Escrow tokens on module account
	transferData, err := erc20.Pack("transfer", types.ModuleAddress, msg.Amount.BigInt())
	if err != nil {
		return nil, err
	}

	res, err := k.evmKeeper.CallEVMWithData(ctx, sender, &contract, transferData, true, nil)
	if err != nil {
		return nil, err
	}

	// Check evm call response
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return nil, err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return nil, err
		}
		if !unpackedRet.Value {
			return nil, sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute transfer")
		}
	}

	// Check expected escrow balance after transfer execution
	// NOTE: coin fields already validated in the ValidateBasic() of the message
	coins := sdk.Coins{sdk.Coin{Denom: pair.Denom, Amount: msg.Amount}}
	tokens := coins[0].Amount.BigInt()
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceTokenAfter == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	expToken := big.NewInt(0).Add(balanceToken, tokens)

	if r := balanceTokenAfter.Cmp(expToken); r != 0 {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v",
			expToken, balanceTokenAfter,
		)
	}

	// Mint coins
	if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil {
		return nil, err
	}

	// Send minted coins to the receiver
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, receiver, coins); err != nil {
		return nil, err
	}
```

**File:** x/erc20/keeper/msg_server.go (L207-220)
```go
	// Check ownership and execute conversion
	switch {
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

**File:** x/erc20/keeper/msg_server.go (L324-345)
```go
// RegisterERC20 implements the gRPC MsgServer interface. Any account can permissionlessly
// register a native ERC20 contract to map to a Cosmos Coin.
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}

	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("registration is currently disabled by governance")
	}

	for _, addr := range req.Erc20Addresses {
		if !common.IsHexAddress(addr) {
			return nil, errortypes.ErrInvalidAddress.Wrapf("invalid ERC20 contract address: %s", addr)
		}
```
