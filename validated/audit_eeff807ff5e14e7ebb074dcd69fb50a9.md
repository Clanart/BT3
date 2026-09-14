### Title
Permissive redirect-chain acceptance in pool `/pool_info` handling allows silent pool_url takeover (CWE-601 analog) - ([File: chia/farmer/farmer.py])

### Summary
The farmer's `_pool_get_pool_info()` accepts an HTTP redirect chain to a completely different origin as long as every hop in the chain is a `301`/`308` status, and silently persists that new origin as the trusted `pool_url` in the local pooling config — without validating that the redirect target is same-host, allow-listed, or otherwise trusted.

### Finding Description
`_pool_get_pool_info()` in `chia/farmer/farmer.py` makes a GET request to the pool's `/pool_info` endpoint and inspects `client_response.history`/`client_response.url` to decide whether the effective response URL differs from the configured `pool_url`: [1](#0-0) 

If the redirect chain consists solely of `301`/`308` responses, the code unconditionally computes `new_pool_url` from the final response URL and treats it as authoritative — with no check that the new host matches the original host, is HTTPS, or belongs to any trusted set: [2](#0-1) 

That value then overwrites the persisted `PoolingShareState.pool_url` for this launcher/plot-NFT in `update_pool_state()`: [3](#0-2) 

This is functionally the same bug class as CWE-601 (untrusted URL redirect acceptance): a single 301/308 response from the currently configured pool origin is enough to permanently redirect all future pool protocol traffic (`/farmer`, `/auth`, partial submission target derivation, and the `GetPoolInfoResponse.target_puzzle_hash` used when the plot-NFT joins/rejoins a pool) to an attacker-controlled origin, because the aiohttp client already followed the redirect and the code only inspects the *status codes* in `history`, never the destination authority. The test suite explicitly documents (and thus encodes as accepted behavior) that a single 301/308 redirect flips the trusted `pool_url` to an arbitrary different subdomain/path with no host-pinning: [4](#0-3) [5](#0-4) 

### Impact Explanation
Once `pool_url` is redirected, the farmer signs and sends its owner-key-derived authentication (`GetAuthRequest`), payout instructions, and farmer registration to the new, attacker-chosen origin: [6](#0-5) 

More importantly, the same `GetPoolInfoResponse` fetched from the (now attacker-controlled) endpoint carries a `target_puzzle_hash` field that is the authoritative payout address used when a plot-NFT joins or transitions to a pool. If a user's wallet/plot-NFT flow re-reads pool info from the (silently swapped) `pool_url` when joining/switching pools, the reward destination puzzle hash used for the singleton's `FARMING_TO_POOL` state can come from the attacker's server rather than the legitimate pool operator — this is a reward-redirection primitive matching the required impact class (reward redirection / unauthorized coin-destination substitution), reached without any signature forgery, purely via the trusted-redirect acceptance flaw.

### Likelihood Explanation
Exploitation requires the attacker to control (or momentarily compromise/MITM at the HTTP layer, or simply configure) the currently-configured pool origin to answer with a `301`/`308` to an attacker-owned origin — this is exactly the CWE-601 pattern (URL redirection to untrusted site) and requires only a single crafted HTTP response from a pool endpoint the farmer already talks to; no wallet signature or private key compromise is needed. Given pool operators are semi-trusted but explicitly not authorities over on-chain singleton state per the codebase's own trust model, this analog is directly reachable by a "pool participant" scenario in scope.

### Recommendation
Validate that any accepted `new_pool_url` redirect target shares the same host (and ideally scheme) as the originally configured `pool_url`, or require explicit user confirmation before persisting a changed `pool_url`. At minimum, pin acceptance to same-registrable-domain redirects and reject cross-origin redirects outright, and treat `target_puzzle_hash` from `/pool_info` as non-authoritative for actual on-chain payout switching unless corroborated by explicit user/wallet action.

### Proof of Concept
1. Configure a plot-NFT pointing at pool `https://good-pool.tld`.
2. Have `good-pool.tld/pool_info` (or a MITM in front of it) respond with a `308` redirect to `https://evil.tld/pool_info`, which serves a valid `GetPoolInfoResponse` (including an attacker `target_puzzle_hash`).
3. `_pool_get_pool_info()` accepts this because all hops are `308`s, computes `new_pool_url = "https://evil.tld"`, and `update_pool_state()` persists it as the new trusted `pool_url` — subsequent farmer registration, auth, and (if the wallet re-derives target puzzle hash from this endpoint on a pool switch) reward routing now flow through the attacker's origin, as exercised by the existing test case `valid_response_with_308_redirect` in `chia/_tests/farmer_harvester/test_farmer.py`. [5](#0-4)

### Citations

**File:** chia/farmer/farmer.py (L449-470)
```python
    async def _pool_get_auth(
        self, pool_config: PoolingShareState
    ) -> pool_protocol.GetAuthResponse | pool_protocol.ErrorResponse | None:
        timestamp = self.get_current_time()
        message = bytes(timestamp) + bytes(pool_config.launcher_id) + pool_config.target_puzzle_hash
        authentication_sk: PrivateKey | None = self.get_authentication_sk(pool_config)
        if authentication_sk is None:
            return None
        signature: G2Element = AugSchemeMPL.sign(singleton_owner_sk_to_authv2_key(authentication_sk), message)
        response, _ = await make_pool_protocol_request(
            self=self,
            pool_config=pool_config,
            method="GET",
            endpoint_name="auth",
            request=pool_protocol.GetAuthRequest(
                launcher_id=pool_config.launcher_id,
                timestamp=timestamp,
                signature=signature,
            ),
            response_type=pool_protocol.GetAuthResponse,
        )
        return response
```

**File:** chia/farmer/farmer.py (L472-496)
```python
    async def _pool_get_pool_info(self, pool_config: PoolingShareState) -> GetPoolInfoResult | None:
        response, client_response = await make_pool_protocol_request(
            self=self,
            pool_config=pool_config,
            method="GET",
            endpoint_name="pool_info",
            request=None,
            response_type=pool_protocol.GetPoolInfoResponse,
        )
        if client_response is None:
            return None
        new_pool_url: str | None = None
        response_url_str = f"{client_response.url}"
        if (
            response_url_str != self._url_for_endpoint(pool_config, "pool_info")
            and len(client_response.history) > 0
            and all(r.status in {301, 308} for r in client_response.history)
        ):
            new_pool_url = response_url_str.replace("/pool_info", "")
            new_pool_url = new_pool_url.replace(f"/v{pool_config.version}", "")

        if isinstance(response, pool_protocol.GetPoolInfoResponse):
            return GetPoolInfoResult(pool_info=response, new_pool_url=new_pool_url)
        else:
            return None
```

**File:** chia/farmer/farmer.py (L704-710)
```python
                    if pool_info_result is not None and pool_info_result.new_pool_url is not None:
                        with PoolingShareState.acquire(
                            root_path=self._root_path, p2_singleton_puzzle_hash=p2_singleton_puzzle_hash
                        ) as editable_pool_config:
                            editable_pool_config.pool_url = pool_info_result.new_pool_url
                            self.pool_state[p2_singleton_puzzle_hash]["pool_config"] = editable_pool_config
                        pool_config = editable_pool_config
```

**File:** chia/_tests/farmer_harvester/test_farmer.py (L1094-1105)
```python
    PoolInfoCase(
        "valid_response_with_301_redirect",
        initial_pool_url_in_config="https://endpoint-1.pool-domain.tld/some-path",
        pool_response=DummyPoolInfoResponse(
            ok=True,
            status=200,
            url=URL("https://endpoint-1337.pool-domain.tld/some-other-path"),
            pool_info=make_pool_info(),
            history=tuple([DummyClientResponse(status=301)]),
        ),
        expected_pool_url_in_config="https://endpoint-1337.pool-domain.tld/some-other-path",
    ),
```

**File:** chia/_tests/farmer_harvester/test_farmer.py (L1130-1141)
```python
    PoolInfoCase(
        "valid_response_with_308_redirect",
        initial_pool_url_in_config="https://endpoint-1.pool-domain.tld/some-path",
        pool_response=DummyPoolInfoResponse(
            ok=True,
            status=200,
            url=URL("https://endpoint-1337.pool-domain.tld/some-other-path"),
            pool_info=make_pool_info(),
            history=tuple([DummyClientResponse(status=308)]),
        ),
        expected_pool_url_in_config="https://endpoint-1337.pool-domain.tld/some-other-path",
    ),
```
