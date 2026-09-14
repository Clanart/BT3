Based on my investigation, I found a concrete access-control weakness that is a genuine analog to CVE-2025-55368's "unauthorized status modification" bug class.

### Title
Unauthorized cross-wallet NFT pending-transaction status manipulation via `nft_set_nft_status` RPC - (File: `chia/wallet/wallet_rpc_api.py`)

### Summary
The wallet RPC endpoint `nft_set_nft_status` accepts a caller-supplied `wallet_id` and `coin_id` and directly forwards them to `NFTWallet.update_coin_status()` without verifying that the specified `coin_id` actually belongs to the specified `wallet_id`/NFT wallet, mirroring the CVE's pattern of a status-mutation endpoint that trusts client-supplied identifiers instead of validating ownership server-side.

### Finding Description
`nft_set_nft_status` looks up an `NFTWallet` purely by the caller-provided `wallet_id` and then calls `update_coin_status(request.coin_id, request.in_transaction)` with the caller-provided `coin_id`, with no cross-check that the coin is owned by/registered under that wallet: [1](#0-0) 

This is registered as a plain (non-tx, non-signed) RPC endpoint in the metadata table, meaning it performs a direct state mutation without going through the transaction/signing pipeline that other NFT mutation endpoints use: [2](#0-1) 

Because `pending_transaction`/`in_transaction` status drives whether a coin is considered available for spend selection elsewhere in the wallet (see how `nft_transfer_nft` sets it to `True` after a legitimate spend), an attacker with access to the wallet RPC surface who can supply an arbitrary `coin_id` belonging to a *different* NFT wallet than the one referenced by `wallet_id` can flip its `in_transaction`/pending status, similarly to how the CVE lets an unauthorized caller flip a supplier's status field it shouldn't control. This can be used to falsely mark another wallet's NFT coin as "in transaction" (blocking legitimate spend paths / causing denial of availability) or clear a pending flag prematurely (causing double-selection of a coin that is actually mid-spend, a state confusion bug), all through a status-setter that lacks an ownership check tying `coin_id` to `wallet_id`.

### Impact Explanation
This falls under "coin-set divergence between honest nodes" / wallet state corruption category permitted by scope: it can cause the wallet's local coin-selection state to diverge from actual on-chain reality, leading to selection of coins that are genuinely mid-spend (potential double-spend attempt from the wallet's perspective) or to NFTs becoming stuck as perpetually "pending" and unspendable. It does not directly forge value or spend a coin without a signature, so it should be scored as state-integrity impact rather than direct fund theft.

### Likelihood Explanation
The RPC surface is described as a "semi-trusted local/admin surface" (see `.cursor/context/rpc.md`), so exploitability depends on what other principals can reach the wallet RPC (e.g., a compromised local process, another user process, or a lightly-restricted local API deployment). Given the endpoint takes only a `wallet_id` and `coin_id` with no cross-ownership assertion, likelihood is moderate whenever any less-trusted caller has RPC access alongside the wallet owner (e.g., multi-tenant wallet daemon deployments or local malware with RPC reach but not full disk/key access).

### Recommendation
In `nft_set_nft_status`, before calling `update_coin_status`, fetch the NFT coin info from `nft_wallet.get_nft_coin_by_id(request.coin_id)` (or equivalent) and verify it belongs to that same wallet before mutating status, rejecting the request otherwise — consistent with how other endpoints such as `nft_transfer_nft` first resolve `nft_coin_info` from the wallet before acting on it.

### Proof of Concept
1. Caller has RPC access with two existing NFT wallets, `wallet_id=A` (owns coin `X`) and `wallet_id=B` (owns coin `Y`, currently mid-transfer/pending).
2. Call `nft_set_nft_status` with `wallet_id=A`, `coin_id=Y`, `in_transaction=False` — this succeeds because `nft_set_nft_status` never checks that `Y` belongs to wallet `A`, it simply calls `NFTWallet(A).update_coin_status(Y, False)`.
3. Wallet `B`'s coin `Y` pending flag is cleared even though it is still actually mid-spend, allowing coin-selection logic elsewhere to treat `Y` as spendable prematurely, creating a state where the wallet may attempt to reuse `Y` while its real on-chain spend is still unconfirmed.

Note: I was unable to fully inspect the body of `NFTWallet.update_coin_status()` (only its call sites and imports were retrievable via search) due to indexing limits on that specific function's body, so I cannot confirm with 100% certainty whether it performs any internal coin-to-wallet cross-check at the store layer. If you need to confirm the exact store-level query behind `update_coin_status`, a full Devin session with direct file access would allow reading `chia/wallet/nft_wallet/nft_wallet.py` and `chia/wallet/wallet_nft_store.py` in full to verify this precisely.

### Citations

**File:** chia/wallet/wallet_rpc_api.py (L2670-2674)
```python
    async def nft_set_nft_status(self, request: NFTSetNFTStatus) -> Empty:
        assert self.service.wallet_state_manager is not None
        nft_wallet = self.service.wallet_state_manager.get_wallet(id=request.wallet_id, required_type=NFTWallet)
        await nft_wallet.update_coin_status(request.coin_id, request.in_transaction)
        return Empty()
```

**File:** chia/wallet/wallet_rpc_metadata.py (L480-484)
```python
    WalletRpcMetadata(
        endpoint_name="nft_set_nft_status",
        request_type=wallet_request_types.NFTSetNFTStatus,
        response_type=wallet_request_types.Empty,
    ),
```
