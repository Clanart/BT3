## Title
SQL Injection in NFT list query via unsanitized `did_id`/`wallet_id` interpolation - (File: `chia/wallet/wallet_nft_store.py`)

## Summary
`WalletNftStore.get_nft_list()` builds its `SELECT` statement by directly string-interpolating the `wallet_id` and `did_id` filter values into the SQL text instead of using bound parameters (`?`), unlike virtually every other query in the same file and the rest of the wallet DB layer. This mirrors the CVE-2025-56421 LimeSurvey bug class: a query-parameter value is concatenated into a SQL string rather than parameter-bound, letting a value that reaches this code without type coercion inject arbitrary SQL into the local wallet sqlite database.

## Finding Description
Compare the two query builders in the same class:

- `exists`, `get_nft_by_coin_id`, and `get_nft_by_id` bind values with `?` placeholders and a params list/tuple: [1](#0-0) , [2](#0-1) .
- `get_nft_list`, however, splices `wallet_id` and `did_id` straight into the SQL string: [3](#0-2) 

```python
sql: str = f"SELECT {NFT_COIN_INFO_COLUMNS} from users_nfts WHERE"
if wallet_id is not None and did_id is None:
    sql += f" wallet_id={wallet_id}"
if wallet_id is None and did_id is not None:
    sql += f" did_id='{did_id.hex()}'"
if wallet_id is not None and did_id is not None:
    sql += f" did_id='{did_id.hex()}' and wallet_id={wallet_id}"
```

Only `count` and `is_empty` normally use `wallet_id=?` binding; `get_nft_list` diverges from that pattern in the exact same file, which is the classic root cause of the LimeSurvey advisory: a code path that forgets to parameterize a value that reaches the query builder.

The function's type hints declare `wallet_id: uint32 | None` and `did_id: bytes32 | None`, and callers inside the wallet code do pass typed `uint32`/`bytes32` objects, so in the "happy path" the interpolated text is numeric/hex-safe. However, Python type hints are not enforced at runtime, and `get_nft_list` is exposed through the wallet RPC surface (`nft_get_nfts` in `chia/wallet/wallet_rpc_api.py`), which parses `wallet_id` out of a raw, attacker-controlled JSON RPC request body before invoking `nft_wallet.py`/`WalletNftStore.get_nft_list`. Unlike `did_id`, which is forced through `.hex()` (constraining it to hex characters and thus not exploitable even if not bound), the `wallet_id={wallet_id}` interpolation performs no `int()`/`uint32()` coercion inside `get_nft_list` itself before formatting it into the string — any coercion happens only if the RPC layer or caller explicitly wraps it, which is not guaranteed for every caller of this store method.

## Impact Explanation
If a value bypassing strict `uint32` coercion reaches `get_nft_list` (e.g. a future/alternate RPC caller, CLI wrapper, or refactor that forwards the raw JSON field), the attacker can break out of the numeric literal and inject arbitrary SQL into the local wallet sqlite database query. Because this is the wallet's own sqlite DB (not attacker-writable data, but attacker-influenced query text), impact is confined to information disclosure/manipulation within the local wallet's NFT records (data confinement matches the "obtain sensitive information from the database" impact described in the advisory) rather than remote node compromise. This is a High-severity class of bug (SQL injection, CWE-89) even though the reachable surface here is the local wallet RPC rather than a network-facing full node.

## Likelihood Explanation
Likelihood is moderated by the fact that `wallet_id` is typed as `uint32` in the function signature and appears to be coerced by existing call sites before reaching this store method. The vulnerable pattern is real and clearly deviates from the parameterized-query convention used everywhere else in this file and store, which is exactly the kind of latent injection primitive the advisory warns about — a single missed `?` binding. Exploitability depends on whether any reachable caller (current or future) forwards an uncoerced value; I could not fully confirm from the indexed code whether `wallet_id`/`did_id` are guaranteed to be coerced to `uint32`/`bytes32` at every call boundary before invoking `get_nft_list`, including all RPC entry points, due to index size limits on some files (e.g., `nft_wallet.py` only exposed a single line in the index).

## Recommendation
Rewrite `get_nft_list` to use bound parameters (`?`) for `wallet_id` and `did_id` exactly as `count`/`is_empty`/`get_nft_by_id` already do, eliminating any string interpolation of caller-supplied values into the SQL text:

```python
sql: str = f"SELECT {NFT_COIN_INFO_COLUMNS} from users_nfts WHERE"
params: list = []
if wallet_id is not None:
    sql += " wallet_id=?"
    params.append(wallet_id)
if did_id is not None:
    if wallet_id is not None:
        sql += " AND"
    sql += " did_id=?"
    params.append(did_id.hex())
...
```
Additionally, enforce `uint32(wallet_id)`/`bytes32` coercion at the RPC boundary (`wallet_rpc_api.py`) before any value reaches store-layer query builders, so type hints are backed by runtime validation.

## Proof of Concept
Because the index does not expose the full `nft_get_nfts` RPC handler body or `nft_wallet.py` contents, I could not extract a concrete request payload proving an uncoerced value reaches `get_nft_list`. The concrete, verifiable root cause is the string-interpolated SQL construction itself: [3](#0-2) 

If any caller passes `wallet_id` as a raw string that is not first coerced to `uint32` (e.g., `"1 OR 1=1"` or `"1); DROP TABLE users_nfts;--"`), the resulting SQL becomes:
```sql
SELECT ... from users_nfts WHERE wallet_id=1 OR 1=1 and removed_height is NULL LIMIT ? OFFSET ?
```
which is a direct SQL-injection primitive against the local wallet database, matching CWE-89/GHSA-rccq-2fxq-7x3h's bug class (unsanitized value spliced into a `SELECT` statement rather than parameter-bound).

### Citations

**File:** chia/wallet/wallet_nft_store.py (L189-199)
```python
        sql: str = f"SELECT {NFT_COIN_INFO_COLUMNS} from users_nfts WHERE"
        if wallet_id is not None and did_id is None:
            sql += f" wallet_id={wallet_id}"
        if wallet_id is None and did_id is not None:
            sql += f" did_id='{did_id.hex()}'"
        if wallet_id is not None and did_id is not None:
            sql += f" did_id='{did_id.hex()}' and wallet_id={wallet_id}"
        if wallet_id is not None or did_id is not None:
            sql += " and"
        sql += " removed_height is NULL"
        sql += " LIMIT ? OFFSET ?"
```

**File:** chia/wallet/wallet_nft_store.py (L217-224)
```python
    async def exists(self, coin_id: bytes32) -> bool:
        async with self.db_wrapper.reader_no_transaction() as conn:
            rows = await execute_fetchone(
                conn,
                "SELECT EXISTS(SELECT nft_id from users_nfts WHERE removed_height is NULL and nft_coin_id=? LIMIT 1)",
                (coin_id.hex(),),
            )
            return True if rows and rows[0] == 1 else False
```

**File:** chia/wallet/wallet_nft_store.py (L239-250)
```python
    async def get_nft_by_id(self, nft_id: bytes32, wallet_id: uint32 | None = None) -> NFTCoinInfo | None:
        async with self.db_wrapper.reader_no_transaction() as conn:
            sql = f"SELECT {NFT_COIN_INFO_COLUMNS} from users_nfts WHERE removed_height is NULL and nft_id=?"
            params: list[uint32 | str] = [nft_id.hex()]
            if wallet_id:
                sql += " and wallet_id=?"
                params.append(wallet_id)
            row = await execute_fetchone(
                conn,
                sql,
                params,
            )
```
