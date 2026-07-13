### Title
Non-EVM-Denom (IBC/Cosmos) Tokens Permanently Locked on EVM Contract Self-Destruct - (File: x/evm/statedb/statedb.go)

### Summary
When an EVM contract self-destructs, Ethermint's `StateDB.Commit()` only burns the EVM-native denom balance of the destroyed address. Any non-EVM-denom Cosmos bank tokens (IBC assets, CosmWasm bridge tokens, etc.) held by the contract are explicitly left as orphaned bank balances with no recovery path. This is a direct analog to the external report's "locked assets" class: funds enter a contract but cannot exit.

### Finding Description
In `x/evm/statedb/statedb.go`, the `Commit()` function handles self-destructed objects as follows: [1](#0-0) 

The code explicitly drains only the EVM denom: [2](#0-1) 

The inline comment at line 813–815 reads:
> "Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm bridge) held by the destroyed address are not drained and may remain as orphaned bank balances."

After `DeleteAccount` removes the account metadata, the non-EVM bank balances at that address persist in the Cosmos bank module with no owner and no sweep/recovery mechanism anywhere in the EVM keeper or stateDB.

The `keeper.SubBalance` path used for the EVM denom burn is: [3](#0-2) 

No equivalent call is made for non-EVM denoms. There is no `sweepNonEvmTokens`, `approveThis`-style governance escape hatch, or any other recovery function in the EVM module for these orphaned balances. [4](#0-3) 

The `BankKeeper` interface exposes `MintCoins`, `BurnCoins`, and `SendCoins*` but none of these are invoked for non-EVM denoms during self-destruct commit.

### Impact Explanation
Any Cosmos bank-module tokens (IBC assets, staking derivatives, CosmWasm-bridged tokens) held by an EVM contract at the time of self-destruct are permanently locked. The account metadata is deleted by `DeleteAccount`, but the bank balances remain at the address with no account to claim them and no module-level sweep. This constitutes a direct, irreversible loss of Cosmos bank funds triggered through Ethermint's stateDB/native action logic — matching the "valid user funds to be mis-accounted" High impact category.

Additionally, because `DeleteAccount` removes auth metadata while leaving bank balances intact, a subsequent CREATE2 re-deployment to the same address creates a new account that inherits those orphaned balances. If the chain has any precompile or native-action hook that allows an EVM contract to transfer its own bank-module balance (common in Evmos-derived chains), the re-deployer can drain the previously locked IBC tokens — escalating to Critical unauthorized transfer of Cosmos bank funds.

### Likelihood Explanation
- IBC tokens are routinely sent to EVM contract addresses on Ethermint-based chains (Evmos, Cronos, etc.).
- Self-destruct is a standard EVM opcode callable by any contract; no privilege is required.
- CREATE2 re-deployment to the same address is a well-known pattern requiring only knowledge of the original deployer, salt, and init-code hash — all of which are on-chain.
- The code comment explicitly acknowledges the gap, confirming the team is aware but has not addressed it.

### Recommendation
In the self-destruct branch of `Commit()`, iterate over all non-zero bank balances held by the destroyed address (not just the EVM denom) and either burn them or transfer them to a designated recovery module account. A minimal fix mirrors the existing EVM-denom drain:

```go
// After burning EVM denom, drain all remaining bank denoms
allBalances := s.keeper.GetAllBalances(cacheCtx, cosmosAddr)
for _, coin := range allBalances {
    if coin.Denom == s.evmDenom {
        continue // already handled
    }
    if err := s.keeper.BurnOrSweepCoin(cacheCtx, cosmosAddr, coin); err != nil {
        return errorsmod.Wrap(err, "failed to drain non-evm token on selfdestruct")
    }
}
```

Alternatively, add a governance-controlled sweep function (analogous to `approveThis` in Compound's Comet) that allows recovery of stranded non-EVM tokens from the EVM module account.

### Proof of Concept
1. Deploy contract `Vault` at address `X` via CREATE2 (salt `S`, deployer `D`).
2. Send 1000 `ibc/ATOM` to address `X` via a Cosmos `MsgSend`.
3. Call `Vault.destroy()` which executes `SELFDESTRUCT`.
4. `StateDB.Commit()` burns the EVM-denom balance of `X` and calls `DeleteAccount(X)`.
5. Query `bank.Balance(X, "ibc/ATOM")` — returns 1000; the tokens are orphaned.
6. On a chain with an IBC precompile: re-deploy `Vault2` at `X` using the same CREATE2 parameters. `Vault2` calls the IBC precompile's `transfer` method using its own address as sender, draining the 1000 `ibc/ATOM` to an attacker-controlled address. [5](#0-4)

### Citations

**File:** x/evm/statedb/statedb.go (L800-826)
```go
		if obj.selfDestructed {
			// Burn any balance that arrived after SelfDestruct was called (e.g., via a
			// value-bearing CALL to the destroyed address within the same transaction).
			// SelfDestruct already burned the balance present at destruction time, but
			// subsequent AddBalance calls write to the bank without a matching burn.
			// DeleteAccount only removes auth metadata and storage; it never touches the
			// bank balance, so we must drain it here before removing the account.
			//
			// Both operations run inside a single CacheContext so that if DeleteAccount
			// fails after SubBalance, the partial burn is rolled back and the bank is
			// left consistent.
			cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
			cacheCtx, writeCache := s.origCtx.CacheContext()
			// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
			// bridge) held by the destroyed address are not drained and may remain as
			// orphaned bank balances.
			if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
				coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
				if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
					return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
				}
			}
			if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
				return errorsmod.Wrap(err, "failed to delete account")
			}
			writeCache()
		} else {
```

**File:** x/evm/keeper/statedb.go (L92-101)
```go
func (k *Keeper) SubBalance(ctx sdk.Context, addr sdk.AccAddress, coin sdk.Coin) (uint256.Int, error) {
	coins := sdk.NewCoins(coin)
	prevBalance := k.GetBalance(ctx, addr, coin.Denom)
	if err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, addr, types.ModuleName, coins); err != nil {
		return uint256.Int{}, err
	}
	if err := k.bankKeeper.BurnCoins(ctx, types.ModuleName, coins); err != nil {
		return uint256.Int{}, err
	}
	return prevBalance, nil
```

**File:** x/evm/types/interfaces.go (L46-55)
```go
type BankKeeper interface {
	authtypes.BankKeeper
	GetBalance(ctx context.Context, addr sdk.AccAddress, denom string) sdk.Coin
	SendCoinsFromModuleToAccount(ctx context.Context, senderModule string, recipientAddr sdk.AccAddress, amt sdk.Coins) error
	SendCoinsFromModuleToAccountVirtual(ctx context.Context, senderModule string, recipientAddr sdk.AccAddress, amt sdk.Coins) error
	SendCoinsFromAccountToModuleVirtual(ctx context.Context, senderAddr sdk.AccAddress, recipientModule string, amt sdk.Coins) error
	MintCoins(ctx context.Context, moduleName string, amt sdk.Coins) error
	BurnCoins(ctx context.Context, moduleName string, amt sdk.Coins) error
	BlockedAddr(addr sdk.AccAddress) bool
}
```
