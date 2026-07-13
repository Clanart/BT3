### Title
Non-EVM-native (IBC/CosmWasm bridge) token balances permanently locked with no recovery mechanism when a contract self-destructs — (File: `x/evm/statedb/statedb.go`)

---

### Summary

When an EVM contract self-destructs, `StateDB.Commit()` burns only the EVM-native denom balance and then deletes the account. Any non-EVM-native tokens (IBC tokens, CosmWasm bridge tokens, etc.) held at the contract's Cosmos bank address are left as orphaned bank balances. The account is deleted, so those tokens are permanently inaccessible and there is no recovery path — a direct structural analog to the SpiceAuction "no-bids auction token lock" pattern.

---

### Finding Description

In `x/evm/statedb/statedb.go`, the `Commit()` function handles self-destructed accounts as follows:

```go
if obj.selfDestructed {
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
}
``` [1](#0-0) 

The logic:
1. Queries and burns only `s.evmDenom` (e.g., `aevmos`/`aphoton`).
2. Calls `DeleteAccount`, which removes the auth-module account record (nonce, code hash, etc.).
3. **Never touches non-EVM-native bank balances** (IBC denoms, CosmWasm bridge tokens, etc.).

In Cosmos SDK, bank balances are stored in the bank module's KV store, keyed by `(address, denom)`, completely independently of the auth-module account record. `DeleteAccount` removes the auth record but leaves every non-EVM-native bank balance intact at the now-deleted address. Because the account no longer exists in the auth module, no standard transaction (bank send, IBC transfer, etc.) can be signed from that address, and no governance or admin recovery path exists in the EVM module. The tokens are permanently orphaned.

The code itself acknowledges this in the comment: *"Non-EVM-native tokens (IBC, CosmWasm bridge) held by the destroyed address are not drained and may remain as orphaned bank balances."* — but no recovery mechanism is implemented. [2](#0-1) 

---

### Impact Explanation

Any non-EVM-native tokens held by a self-destructed contract are permanently lost. The bank module retains the balance at the contract's Cosmos address, but:
- The auth-module account is gone, so no key can sign a spend from that address.
- The EVM module has no `recoverOrphanedBalance` or equivalent function.
- There is no governance sweep or admin recovery path.

This constitutes permanent, irrecoverable loss of valid user funds held in the Cosmos bank module — matching the allowed High impact: *"valid user funds/fees to be mis-accounted."*

---

### Likelihood Explanation

On any IBC-enabled Ethermint chain (the primary deployment target):

- IBC tokens can be transferred to any Cosmos address, including EVM contract addresses, via standard `MsgTransfer` or `MsgSend`. No special privilege is required.
- Contracts routinely receive tokens as part of normal DeFi operations (liquidity pools, bridges, vaults).
- `SELFDESTRUCT` is a standard EVM opcode available to any contract author.
- A user can accidentally send IBC tokens to a contract that later self-destructs (e.g., an upgradeable proxy pattern).
- A malicious contract can deliberately accept IBC tokens and then self-destruct, permanently locking depositors' funds.

The entry path is fully unprivileged: deploy contract → receive IBC tokens → execute `SELFDESTRUCT`. No validator cooperation, governance action, or key compromise is required.

---

### Recommendation

Before calling `DeleteAccount` on a self-destructed contract, enumerate **all** bank module balances at the contract's Cosmos address and handle each non-EVM-native denom. Options:

1. **Redirect to fee collector / community pool**: Transfer all non-EVM-native balances to `authtypes.FeeCollectorName` or the community pool before deleting the account.
2. **Burn if burnable**: For tokens with a registered burn path, burn them.
3. **Revert self-destruct if non-EVM-native tokens are present**: Return an error from `Commit()` (surfaced as `ErrStateConflict` / `VmError`) if the contract holds non-EVM-native tokens, preventing the self-destruct from completing until those tokens are moved out.

The fix must be applied inside the `obj.selfDestructed` branch of `Commit()` in `x/evm/statedb/statedb.go`, before `writeCache()` is called. [3](#0-2) 

---

### Proof of Concept

```
1. Deploy contract C on an IBC-enabled Ethermint chain.
   Contract address (EVM): 0xABCD...
   Cosmos address:          cosmos1... (sdk.AccAddress(0xABCD...))

2. Send 1000 ATOM (IBC denom ibc/...) to cosmos1... via:
     MsgSend { from: alice, to: cosmos1..., amount: [1000ibc/...] }
   Bank module now holds: cosmos1... → 1000ibc/...

3. Call SELFDESTRUCT on contract C (any tx from C's deployer or
   a self-destruct path in C's logic).

4. StateDB.Commit() executes:
   - Burns aevmos balance at cosmos1... ✓
   - Calls DeleteAccount(cosmos1...)   ✓  (auth record removed)
   - Does NOT touch 1000ibc/...        ✗

5. Result:
   - bank.GetBalance(cosmos1..., "ibc/...") == 1000  (still there)
   - auth.GetAccount(cosmos1...)        == nil        (deleted)
   - No signer exists for cosmos1...; no EVM module recovery fn exists.
   - 1000 ATOM permanently locked.
```

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
