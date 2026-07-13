### Title
Non-EVM-Native Cosmos Bank Tokens Permanently Orphaned at Self-Destructed Contract Addresses - (File: x/evm/statedb/statedb.go)

### Summary
When an EVM contract that holds non-EVM-native Cosmos bank tokens (e.g., IBC tokens, CosmWasm bridge tokens) executes `SELFDESTRUCT`, Ethermint's `StateDB.Commit()` only burns the EVM-denom balance and deletes the account metadata. Non-EVM-native bank balances at the destroyed address are explicitly left unhandled and become permanently orphaned — inaccessible to any future transaction. This is the direct Ethermint analog of the "stuck funds" class: a user-reachable EVM execution path causes Cosmos bank funds to be permanently locked with no recovery mechanism.

### Finding Description

In `x/evm/statedb/statedb.go`, the `Commit()` function handles self-destructed accounts as follows:

```go
// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
// bridge) held by the destroyed address are not drained and may remain as
// orphaned bank balances.
cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
cacheCtx, writeCache := s.origCtx.CacheContext()
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
```

The code explicitly acknowledges the gap: only `s.evmDenom` is drained; all other Cosmos bank denominations held by the contract address are skipped. After `DeleteAccount` removes the auth/EVM account metadata, the orphaned non-EVM-denom bank balances remain in the bank module at that address indefinitely. Because the account no longer exists, no EVM transaction can spend those balances — they are permanently stuck.

The root cause is the hard-coded single-denom drain in `Commit()` at lines 816–821, with no enumeration of other bank denominations held by the address.

### Impact Explanation

Any non-EVM-native Cosmos bank token (IBC-transferred assets, tokens minted by CosmWasm bridges, or any `sdk.Coin` with a denom other than `evmDenom`) held by a contract at the time of `SELFDESTRUCT` is permanently lost. The bank module retains the balance entry, but the owning account is deleted, so:

- No EVM `CALL` can spend the balance (the EVM only tracks `evmDenom` via `GetBalance`/`SubBalance`).
- No Cosmos `MsgSend` can spend it because the account no longer exists in the auth module.
- The total supply of those tokens is not reduced, creating a permanent supply/balance discrepancy.

This matches the **High** allowed impact: *"EVM state transition … bug that permits … valid user funds/fees to be mis-accounted."* Cosmos bank funds are permanently mis-accounted — present in the bank store but irrecoverable.

### Likelihood Explanation

The trigger path is fully unprivileged:

1. Any EVM contract can receive IBC tokens (e.g., via an IBC `MsgTransfer` to a contract address, or via a precompile that credits non-EVM-denom coins to a contract).
2. Any EVM transaction that causes that contract to execute `SELFDESTRUCT` (e.g., a public `destroy()` function, a one-time-use factory pattern, or EIP-6780 same-transaction destruction) triggers the loss.
3. No validator collusion, governance action, or privileged key is required.

IBC token transfers to contract addresses are a standard cross-chain pattern. Factory contracts that deploy-and-destroy in one transaction (as demonstrated by the in-repo `SelfDestructExploitFactory`) are a known Ethereum pattern. The combination is realistic and reachable on any Ethermint-based chain that enables IBC.

### Recommendation

In `StateDB.Commit()`, before calling `DeleteAccount`, enumerate **all** bank denominations held by the contract address and drain them. The Cosmos SDK `BankKeeper` exposes `GetAllBalances(ctx, addr)` which returns every coin held by an address. Each non-zero balance should be burned (sent to the module account and burned, or sent to the community pool) before the account is deleted:

```go
// Drain ALL bank denominations, not just evmDenom.
allBalances := s.keeper.GetAllBalances(cacheCtx, cosmosAddr)
if !allBalances.IsZero() {
    if err := s.keeper.BurnCoins(cacheCtx, types.ModuleName, allBalances); err != nil {
        return errorsmod.Wrap(err, "failed to burn all balances on selfdestruct")
    }
}
```

Alternatively, redirect orphaned non-EVM-denom balances to the community pool or a designated recovery address, so they remain accessible rather than being silently lost.

### Proof of Concept

**Setup**: Deploy a contract `Vault` that accepts IBC ATOM deposits and has a public `destroy()` function.

1. User sends 100 IBC-ATOM to `Vault`'s address via `MsgTransfer`. The bank module records `cosmos1<vault>: 100ibc/ATOM`.
2. Attacker (or anyone) calls `Vault.destroy()`, which executes `SELFDESTRUCT`.
3. `StateDB.Commit()` runs the self-destruct path:
   - `GetBalance(cacheCtx, vaultAddr, "aevmos")` → 0 (no EVM denom), so the EVM-denom drain is skipped.
   - `DeleteAccount(cacheCtx, vaultAddr)` removes the auth account.
   - `writeCache()` commits the deletion.
4. After the transaction, `bankKeeper.GetAllBalances(ctx, vaultAddr)` still returns `[100ibc/ATOM]`, but `accountKeeper.GetAccount(ctx, vaultAddr)` returns `nil`.
5. No EVM transaction can spend `ibc/ATOM` (EVM only tracks `evmDenom`). No Cosmos `MsgSend` can spend it (no account exists). The 100 IBC-ATOM are permanently orphaned.

The explicit acknowledgment in the production source confirms this is a reachable, unmitigated code path: [1](#0-0) 

The single-denom drain that causes the gap: [2](#0-1) 

The `DeleteAccount` call that removes the account while leaving non-EVM-denom balances behind: [3](#0-2)

### Citations

**File:** x/evm/statedb/statedb.go (L811-825)
```go
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
```
