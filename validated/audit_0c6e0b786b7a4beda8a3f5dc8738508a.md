### Title
Farmer Blindly Merges Unauthenticated Pool `/pool_info` Redirect URL Into Persisted Config, Bypassing Mainnet HTTPS Enforcement - (File: `chia/farmer/farmer.py`)

### Summary
`Farmer.update_pool_state()` enforces HTTPS pool URLs on mainnet only once, at the top of each per-pool loop iteration, by checking the *currently stored* `pool_config.pool_url`. It then calls `_pool_get_pool_info()`, which performs a GET to `/pool_info` and, if the HTTP response history shows 301/308 redirects, derives a `new_pool_url` directly from the final response URL with no scheme validation. That unvalidated URL is written into the persisted `PoolingShareState` YAML mirror and immediately reused as `pool_config` for the rest of the same update cycle (`/farmer` GET/POST/PUT calls, which carry signed authentication and payout data). This closely mirrors the reported RustDesk bug class: an external server response (an unauthenticated "strategy"/config payload) is merged into local trusted state without validating it against the local security policy that is supposed to gate it.

### Finding Description
- `_pool_get_pool_info()` computes `new_pool_url` purely from the observed response URL and redirect status codes, without checking that the resulting URL is `https://`: [1](#0-0) 

- `update_pool_state()` performs the mainnet HTTPS enforcement check only against the *pre-update* `pool_config.pool_url`, before calling `_pool_get_pool_info()`: [2](#0-1) 

- Immediately after, if a redirect occurred, the new (unchecked) URL is written to the on-disk `PoolingShareState` mirror and substituted as the active `pool_config` for the remainder of this cycle, with no re-validation of scheme/HTTPS: [3](#0-2) 

- That mutated `pool_config` is then used to build and send `/farmer` GET/POST/PUT requests carrying signed authentication tokens, owner/authentication public keys, and payout instructions: [4](#0-3) 

- The persisted `PoolingShareState.pool_url` value survives across restarts and future update cycles (`PoolingShareState.acquire()` rewrites the YAML file), so the HTTPS bypass is not a one-shot flaw — once flipped it becomes the new "trusted" baseline the farmer keeps using and re-checks against itself (`enforce_https and not pool_config.pool_url.startswith("https://")` after the value has already been silently downgraded).

Root cause: the pool server's redirect response (an unauthenticated, network-observable/MITM-manipulable HTTP artifact) is treated as authoritative configuration data and merged into the farmer's durable pool configuration without validating it against the very security policy (`enforce_https` on mainnet) that the code elsewhere claims to enforce. This is the same bug class as the RustDesk report: unauthenticated strategy/config payloads from a remote peer are blindly merged into local state, bypassing local security settings (there, arbitrary strategy fields; here, the HTTPS-only policy for pool communication).

### Impact Explanation
If a pool server (or a network-position attacker able to manipulate the pool's HTTP redirect chain) returns a 301/308 redirect to a non-HTTPS endpoint, the farmer will:
1. Silently downgrade its trusted pool URL to plaintext HTTP and persist this to disk, defeating the mainnet HTTPS-only policy that exists specifically to protect this traffic.
2. Continue, within the same call, to send `/farmer` requests containing the owner authentication public key, computed authentication signatures, and the farmer's payout instructions over the now-unencrypted channel.
3. Keep using the downgraded URL for all future pool communication cycles, since the mirror is durable and the same code path re-validates only the value it already corrupted.

This does not directly move coins, but it exposes farmer authentication/payout data to network tampering/interception and defeats an explicit security control (mainnet HTTPS enforcement), which is a meaningful confidentiality/integrity impact on the farmer-pool trust relationship (potential to intercept/tamper `PostFarmerPayload`/`PutFarmerPayload` payout-instruction data, redirecting farming rewards to an attacker-controlled payout address if combined with subsequent tampering of payout instruction updates).

### Likelihood Explanation
Exploitation requires either a malicious/compromised pool operator (which a farmer explicitly chooses to join, so plausible via a rogue or later-compromised pool) or a network attacker able to manipulate the HTTP redirect response for a pool the farmer already trusts. No cryptographic break of the underlying TLS session is needed — the check is purely a string-prefix comparison performed against a value that itself gets overwritten. This is a config-integrity logic bug rather than a cryptographic bypass, making it comparatively easy to trigger once network position or pool control is available.

### Recommendation
Validate the redirect target (`new_pool_url`) against the same `enforce_https` policy before accepting or persisting it in `_pool_get_pool_info()`/`update_pool_state()`, and reject/ignore redirects that would downgrade an HTTPS pool URL to HTTP on mainnet. Additionally, disable automatic cross-scheme redirect following in the underlying `aiohttp` request (or explicitly re-check the final resolved scheme) so the security policy cannot be silently bypassed through response manipulation.

### Proof of Concept
1. Configure a farmer on mainnet joined to `https://pool.example/pool_info`.
2. Have the pool server (or an attacker positioned to alter its redirect response) reply to `GET /pool_info` with a `301`/`308` status and a `Location` header pointing to `http://pool.example/pool_info` (or any non-HTTPS host).
3. Observe `_pool_get_pool_info()` returning `new_pool_url = "http://pool.example"` (scheme not checked) at: [1](#0-0) 
4. Observe `update_pool_state()` persisting this URL into `PoolingShareState` and using it for the remainder of the cycle without re-checking `enforce_https`: [3](#0-2) 
5. Confirm subsequent `/farmer` GET/POST/PUT calls (carrying signed authentication tokens and payout instructions) are sent to the plaintext `http://pool.example` endpoint, and that this URL remains in the YAML mirror across future update cycles, permanently bypassing the mainnet HTTPS-only guard.

### Citations

**File:** chia/farmer/farmer.py (L483-492)
```python
        new_pool_url: str | None = None
        response_url_str = f"{client_response.url}"
        if (
            response_url_str != self._url_for_endpoint(pool_config, "pool_info")
            and len(client_response.history) > 0
            and all(r.status in {301, 308} for r in client_response.history)
        ):
            new_pool_url = response_url_str.replace("/pool_info", "")
            new_pool_url = new_pool_url.replace(f"/v{pool_config.version}", "")

```

**File:** chia/farmer/farmer.py (L685-688)
```python
                enforce_https = config["full_node"]["selected_network"] == "mainnet"
                if enforce_https and not pool_config.pool_url.startswith("https://"):
                    self.log.error(f"Pool URLs must be HTTPS on mainnet {pool_config.pool_url}")
                    continue
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

**File:** chia/farmer/farmer.py (L712-758)
```python
                if time.time() >= pool_state["next_farmer_update"]:
                    pool_state["next_farmer_update"] = time.time() + UPDATE_POOL_FARMER_INFO_INTERVAL
                    authentication_token_timeout = pool_state["authentication_token_timeout"]

                    async def update_pool_farmer_info() -> tuple[
                        pool_protocol.GetFarmerResponse | None, pool_protocol.PoolErrorCode | None
                    ]:
                        # Run a GET /farmer to see if the farmer is already known by the pool
                        response = await self._pool_get_farmer(pool_config, authentication_token_timeout)
                        if response is not None:
                            if not isinstance(response, pool_protocol.ErrorResponse):
                                pool_state["current_difficulty"] = response.current_difficulty
                                pool_state["current_points"] = response.current_points
                                return response, None
                            else:
                                try:
                                    error_code = pool_protocol.PoolErrorCode(response.error_code)
                                    return None, error_code
                                except ValueError:
                                    self.log.error(f"Invalid error code received from the pool: {response.error_code}")
                                    return None, None
                        return None, None

                    if authentication_token_timeout is not None:
                        farmer_info, error_code = await update_pool_farmer_info()
                        if error_code == pool_protocol.PoolErrorCode.FARMER_NOT_KNOWN:
                            post_response = await self._pool_post_farmer(pool_config, authentication_token_timeout)
                            if post_response is not None and not isinstance(post_response, pool_protocol.ErrorResponse):
                                self.log.info(
                                    f"Welcome message from {pool_config.pool_url}: {post_response.welcome_message}"
                                )
                                # Now we should be able to update the local farmer info
                                (farmer_info, farmer_is_known) = await update_pool_farmer_info()
                                if farmer_info is None and not farmer_is_known:
                                    self.log.error("Failed to update farmer info after POST /farmer.")

                        # Update the farmer information on the pool if the payout instructions changed or if the
                        # signature is invalid (latter to make sure the pool has the correct auth public key).
                        payout_instructions_update_required: bool = (
                            farmer_info is not None
                            and pool_config.payout_instructions.lower() != farmer_info.payout_instructions.lower()
                        )
                        if (
                            payout_instructions_update_required
                            or error_code == pool_protocol.PoolErrorCode.INVALID_SIGNATURE
                        ):
                            await self._pool_put_farmer(pool_config, authentication_token_timeout)
```
