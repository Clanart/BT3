### Title
Unbounded EIP-7702 authorization list triggers costly signature recovery for every authority before any fee/balance check, enabling CheckTx-based CPU DoS - ([File: app/ante/evm_checktx.go])

### Summary
Apache CXF's advisory describes uncontrolled resource consumption because a message's attachment headers were processed without any per-message cap. The analogous pattern in sei-chain is the EIP-7702 `SetCodeTx` authorization list (`AuthList`): the ante pipeline iterates every authority in the list and performs expensive `ecrecover`-based address derivation for each one, and this loop runs *before* the transaction's fee/balance is validated and with no cap on the number of authorizations, in both the `CheckTx` and `DeliverTx` EVM ante paths.

### Finding Description
`EvmCheckTxAnte` calls `AssociateAuthorizationAuthorities(ctx, ek, etx)` immediately after decoding/verifying the outer tx signature but before `EvmCheckAndChargeFees` is invoked to validate the fee cap and buy gas: [1](#0-0) 

`AssociateAuthorizationAuthorities` loops over `etx.SetCodeAuthorizations()` with no upper bound on `len(auths)`, calling `helpers.AuthorityToPreAssociate` for every entry, which recovers the authority's address from its ECDSA signature (secp256k1 recovery) and does a cache-context association lookup: [2](#0-1) 

The only gate that references the number of authorizations is the `IntrinsicGas` sufficiency check in `EvmStatelessChecks`, which merely requires that the attacker-declared `etx.Gas()` field be large enough — this is a self-reported value, not a paid-for resource, and the account balance is never checked until `EvmCheckAndChargeFees`/`BuyGas()` runs afterward: [3](#0-2) [4](#0-3) 

`SetCodeTx.Validate()` (called from `NewSetCodeTx`) validates the access list and auth list structurally (`validateAccessList`, `validateAuthList`) but does not impose any maximum count on the number of authorization entries: [5](#0-4) 

The identical unbounded loop also exists on the `DeliverTx` path, called from `EvmDeliverTxAnte` before `EvmDeliverChargeFees`: [6](#0-5) 

Because a `MsgEVMTransaction`/`SetCodeTx` is fully public-RPC reachable (any client can submit it via `broadcast_tx`/`eth_sendRawTransaction`), and `EvmCheckTxAnte` runs on every node's mempool admission for every gossip/rebroadcast of the transaction, an attacker can submit (or repeatedly gossip) a `SetCodeTx` populated with a very large `AuthList` of syntactically valid but bogus authorization signatures, from an account with zero balance. Every node that runs `CheckTx` on this transaction performs `N` secp256k1 recoveries before the fee/balance check ultimately rejects it (`ErrInsufficientFee`/`BuyGas` failure) — the CPU cost is paid regardless of whether the sender can afford the transaction.

### Impact Explanation
This matches the CXF bug class: no restriction on a per-message list, allowing an attacker to force disproportionate computational work on every processing node. Repeated/gossiped submission of such unfunded transactions could degrade or stall `CheckTx` throughput on validator/full nodes, contributing to mempool congestion or block-production delay — a resource-exhaustion / potential block-delay risk on validators processing transaction gossip.

### Likelihood Explanation
Reachable by any unprivileged client with no funds required (the fee check happens after the expensive work, and CheckTx-only failures cost the attacker nothing since the tx is never included on-chain). The number of authorizations that fit in one transaction is bounded only by the node's max tx/gossip size, not by any per-tx authorization-count limit, so an attacker can maximize the authorization count per transaction.

### Recommendation
Add an explicit maximum count on `SetCodeTx.AuthList` (mirroring go-ethereum's/EIP-7702 recommended practical limits) enforced in `SetCodeTx.Validate()`/`validateAuthList`, and/or move the cheap "does the account have any/enough balance to plausibly pay" check before `AssociateAuthorizationAuthorities` is invoked in both `app/ante/evm_checktx.go` and `app/ante/evm_delivertx.go`, so unfunded/spam transactions with large authorization lists are rejected before performing per-authority signature recovery.

### Proof of Concept
1. Craft an EIP-7702 `SetCodeTx` from an account with 0 balance, with `AuthList` containing the maximum number of syntactically valid (chain-id/nonce satisfying) but otherwise arbitrary authorization tuples that fit under the node's max tx/gossip byte limit, and a declared `Gas` large enough to pass the `IntrinsicGas` check (self-reported, not paid for).
2. Broadcast the transaction to a public RPC node's mempool.
3. Observe `EvmCheckTxAnte` → `AssociateAuthorizationAuthorities` performing one `ecrecover`/address-derivation per authorization entry for the full list before `EvmCheckAndChargeFees` rejects the tx for insufficient fee/balance.
4. Repeat/gossip the same or similarly-shaped unfunded transactions to force repeated CPU-expensive recovery work across nodes, comparable to the unrestricted attachment-header resource consumption in the CXF advisory.

### Citations

**File:** app/ante/evm_checktx.go (L42-44)
```go
	if err := EvmStatelessChecks(ctx, tx, chainID); err != nil {
		return ctx, err
	}
```

**File:** app/ante/evm_checktx.go (L60-68)
```go
	// Mirror the deliver-tx path and pre-associate EIP-7702 authorization authorities so the
	// mempool's check state matches how the transaction will execute. CheckTx runs on a
	// throwaway cache (its writes are never committed) and never applies authorizations, so
	// this cannot itself cause the direct-cast halt; it is here purely for mempool consistency
	// with EvmDeliverTxAnte.
	AssociateAuthorizationAuthorities(ctx, ek, etx)
	if _, err := EvmCheckAndChargeFees(ctx, evmAddr, ek, upgradeKeeper, txData, etx, msg, version, false); err != nil {
		return ctx, err
	}
```

**File:** app/ante/evm_checktx.go (L113-119)
```go
	intrGas, err := core.IntrinsicGas(etx.Data(), etx.AccessList(), etx.SetCodeAuthorizations(), etx.To() == nil, true, true, true)
	if err != nil {
		return err
	}
	if etx.Gas() < intrGas {
		return core.ErrIntrinsicGas
	}
```

**File:** app/ante/evm_checktx.go (L268-284)
```go
func AssociateAuthorizationAuthorities(ctx sdk.Context, ek *evmkeeper.Keeper, etx *ethtypes.Transaction) {
	auths := etx.SetCodeAuthorizations()
	if len(auths) == 0 {
		return
	}
	associateHelper := helpers.NewAssociationHelper(ek, ek.BankKeeper(), ek.AccountKeeper())
	for _, auth := range auths {
		evmAddr, seiAddr, pubkey, ok := helpers.AuthorityToPreAssociate(ctx, ek, auth)
		if !ok {
			continue
		}
		cacheCtx, write := ctx.CacheContext()
		if err := associateHelper.AssociateAddresses(cacheCtx, seiAddr, evmAddr, pubkey, false); err == nil {
			write()
		}
	}
}
```

**File:** x/evm/types/ethtx/set_code_tx.go (L220-225)
```go
	if err := validateAccessList(tx.Accesses); err != nil {
		return err
	}
	if err := validateAuthList(tx.AuthList); err != nil {
		return err
	}
```

**File:** app/ante/evm_delivertx.go (L42-52)
```go
	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Pre-associate each authority the EVM will
	// apply to its true (pubkey-derived) Sei address before execution installs delegation code,
	// otherwise SetCode creates a mutable direct-cast mapping that a later associatePubKey can
	// remap, orphaning staking/distribution state (which can halt the chain via the distribution
	// validator-removal hook).
	AssociateAuthorizationAuthorities(ctx, ek, etx)
	ctx = DecorateNonceCallback(ctx, ek, evmAddr, etx.Nonce())
	if err := EvmDeliverChargeFees(ctx, ek, upgradeKeeper, txData, etx, msg, version, evmAddr); err != nil {
		return ctx, err
	}
```
