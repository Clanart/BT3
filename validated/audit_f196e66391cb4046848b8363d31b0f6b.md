### Title
Permanent Locking of Escrowed Native Coins After Self-Destruct-Triggered Deletion of a Native ERC20 Token Pair - (File: x/erc20/keeper/msg_server.go)

### Summary
This finding is analogous to the ENS report's core pattern: a user-controlled, unprivileged action creates a state transition that is irreversible and cannot be cleaned up or reclaimed by the protocol, resulting in permanently orphaned value. In the ENS case, an invalid subnode's descendants become permanently unslashable. In `x/erc20`, when a native-ERC20 `TokenPair` is registered (permissionlessly or via governance) for an externally owned/deployed contract, users can convert native Cosmos coins into that ERC20 via `ConvertCoinNativeERC20`, which escrows the Cosmos coin in the `x/erc20` module account [1](#0-0) . If the ERC20 contract owner later self-destructs the contract, the next `ConvertERC20`/`ConvertCoin` call detects the missing code and permanently deletes the `TokenPair` record instead of allowing recovery [2](#0-1) . `DeleteTokenPair` removes the ERC20↔denom mapping, and there is no remaining mechanism to move funds out of the module account under that denom once the pair record is gone.

### Finding Description
1. A user (an unprivileged `OWNER_EXTERNAL` contract deployer) deploys an ERC20 contract and registers it as a token pair — this can occur even permissionlessly, gated only by `SetPermissionlessRegistration` [3](#0-2) .
2. Other users call `ConvertCoin`, which escrows their native Cosmos coins into the `types.ModuleName` account before unescrowing (transferring) ERC20 tokens to the receiver: [1](#0-0) .
3. The contract owner (who fully controls the arbitrary bytecode of their own registered ERC20 contract) self-destructs it.
4. On the next attempted conversion, both `ConvertERC20` and `ConvertCoin` check the contract's code hash and, finding it gone, call `k.DeleteTokenPair(ctx, pair)`, deleting the `TokenPair`, `ERC20Map`, `DenomMap`, and allowances entries: [2](#0-1) [4](#0-3) .
5. `DeleteTokenPair` never refunds/burns or otherwise returns the coins previously escrowed under `pair.Denom` in the `x/erc20` module account back to users or issues a corresponding un-escrow. Once the `TokenPair` mapping is gone, no `Msg` handler in `x/erc20` can look up that denom/contract pair anymore (all lookups go through `GetTokenPairID`/`GetTokenPair`, which now return not-found), so the only code paths capable of moving module-account balances for that denom (`ConvertCoinNativeERC20`, `convertERC20IntoCoinsForNativeToken`) become permanently unreachable for the escrowed coins tied to that denom.

This mirrors the ENS bug class precisely: an unprivileged, ordinary action (self-destructing a contract you own, which is a completely normal/legal EVM operation) triggers an irreversible protocol-level cleanup (`DeleteTokenPair`) that leaves behind orphaned value with no compensating "erasure"/recovery path — analogous to the ENS report's "irrevocable" and "unslashable" subnode that has no counterpart mechanism (like the report's proposed `eraseNode()`) to reclaim or unwind the state.

### Impact Explanation
If confirmed, this results in a Critical, permanent freezing/locking of user (or module-account-held) spendable value: coins that legitimate users escrowed as part of the intended ERC20↔Coin round-trip become permanently stuck in the `x/erc20` module account with no code path to move, burn, or reclaim them, once the pair is deleted. This falls squarely within the allowed impact category "Critical permanent freezing, locking, theft, or unauthorized extraction of user funds, contract balances ... or token-pair-backed balances."

### Likelihood Explanation
Likelihood depends on two conditions I could not fully verify given tool-call limits:
- Whether `ConvertCoinNativeERC20`/`convertERC20IntoCoinsForNativeToken` are the only paths that ever move funds tied to `pair.Denom` out of the `x/erc20` module account (I did not locate a generic "reclaim escrowed coins for deleted pair" or migration function, but did not exhaustively search all keeper files for one).
- Whether the escrow step happens meaningfully in the "native ERC20" `ConvertCoin` direction (Coin → ERC20) as opposed to only in the reverse (ERC20 → Coin, `convertERC20IntoCoinsForNativeToken`, which mints coins fresh from `bankKeeper.MintCoins` rather than moving pre-escrowed value) — if the escrow only occurs transiently within a single atomic transaction (escrow then immediately unescrow to the ERC20 contract) and never persists across blocks, the "stuck" value would not exist. I was not able to confirm within the remaining budget whether escrowed coins can be left non-atomically resident in the module account across the self-destruct event, i.e., whether there is a window where the coin is escrowed in the module account without atomically completing the ERC20 transfer in the very same call.

Given this, the likelihood of this specific analog being materially critical is uncertain without deeper tracing of `ConvertCoinNativeERC20`'s escrow/unescrow atomicity and whether any residual balance can be produced through a partial-success/retry pattern (e.g., IBC timeout/ack-based re-attempts referenced in `ibc_callbacks.go`'s `ConvertCoinToERC20FromPacket`).

### Recommendation
- Before calling `k.DeleteTokenPair`, sweep any remaining `pair.Denom` balance held by the `x/erc20` module account and refund it pro-rata to affected holders, or leave the `TokenPair` in a permanently "disabled" (not deleted) state so that a recovery/burn path can still reference it.
- Add an explicit invariant/migration path (analogous to the recommended `eraseNode()`/slashing function in the ENS report) that allows governance to reconcile or sweep orphaned escrowed balances tied to deleted or disabled token pairs.

### Proof of Concept
I was unable to run code in this environment. A concrete PoC would need to:
1. Register a native ERC20 token pair for a maliciously-controlled contract (permissionless or governance).
2. Execute `MsgConvertCoin` to escrow coins to the module account and mint/transfer ERC20 tokens to a receiver, confirming intermediate escrow balances via `bankKeeper.GetBalance(ctx, moduleAcc, pair.Denom)`.
3. Self-destruct the ERC20 contract.
4. Trigger another `MsgConvertERC20`/`MsgConvertCoin` call to invoke the `DeleteTokenPair` path in [2](#0-1) .
5. Verify whether the module account still holds a nonzero balance in `pair.Denom` and confirm no remaining `x/erc20` `Msg` can retrieve/move it.

This last verification step (step 5) is exactly what I could not complete without code execution access, so I cannot state with certainty that residual escrowed value survives to become permanently orphaned versus being fully consumed within the same atomic conversion call. I recommend a Devin session with code execution access to build and run this PoC against the `evmd` test network to confirm or refute the residual-balance claim before treating this as a confirmed Critical finding.

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

**File:** x/erc20/keeper/msg_server.go (L256-261)
```go
	// Escrow Coins on module account
	coins := sdk.Coins{{Denom: pair.Denom, Amount: amount}}
	if err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, sender, types.ModuleName, coins); err != nil {
		return sdkerrors.Wrap(err, "failed to escrow coins")
	}

```

**File:** tests/integration/x/erc20/test_proposals.go (L128-141)
```go
		{
			"ok - governance, permissionless false",
			func() {
				s.network.App.GetErc20Keeper().SetPermissionlessRegistration(ctx, false)
			},
			authtypes.NewModuleAddress(govtypes.ModuleName).String(),
			true,
		},
		{
			"ok - governance, permissionless true",
			func() {},
			authtypes.NewModuleAddress(govtypes.ModuleName).String(),
			true,
		},
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
