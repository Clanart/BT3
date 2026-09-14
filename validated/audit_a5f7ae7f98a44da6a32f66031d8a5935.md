### Title
Pool-Protocol Bearer Secret (`authentication_token_v2`) Sent as GET Query Parameter, Exposing It to Access-Log Disclosure and Enabling Reward Redirection - (`chia/farmer/farmer.py`)

### Summary
The farmer's pool-protocol client places sensitive, reusable authentication material — including the v2 bearer secret `authentication_token_v2` and the raw BLS `signature` used for `GET /auth` — directly into the HTTP query string of `GET` requests sent to the pool server, instead of in the request body or an authorization header. This is the same bug class as CVE-2023-31207: transmitting a credential via query parameters causes it to be written into the receiving web server's access log, from which it can later be recovered by anyone with log read access and reused to impersonate the farmer.

### Finding Description
`make_pool_protocol_request()` branches request encoding by HTTP method: `POST`/`PUT` bodies are sent as JSON, but `GET` requests are serialized into the URL query string via `params=request.to_json_dict()`: [1](#0-0) 

Two call sites feed secret material through this `GET` path:

- `_pool_get_auth()` issues `GET /auth` with a `GetAuthRequest` containing `launcher_id`, `timestamp`, and the BLS `signature` — all placed in `params`: [2](#0-1) 

- `_pool_get_farmer()` issues `GET /farmer` with `GetFarmerRequestV2`/`GetFarmerRequestV1`, whose `authentication_token_v2` field is the reusable bearer secret returned by `GET /auth` and cached for reuse until expiration: [3](#0-2) [4](#0-3) 

The token type definitions confirm both fields are transmitted as request parameters, not headers: [5](#0-4) [6](#0-5) 

Because `authentication_token_v2` is a bearer credential valid until `expiration` (cached client-side in `self.authentication_tokens`), any party who can read the pool server's HTTP access log — which by default records the full request line including the query string — can extract this token and replay it against the pool API for the remainder of its validity window, with no further proof of possession of the farmer's private key required.

### Impact Explanation
`PUT /farmer` (payout-instruction updates) for pool-protocol v2 is authorized purely by the cached bearer token embedded in the payload, with no signature attached (`signature = None`): [7](#0-6) 

An attacker who recovers a leaked `authentication_token_v2` from pool access logs can therefore call `PUT /farmer` on behalf of the victim farmer and redirect that farmer's pool payout instructions to an attacker-controlled address — a concrete reward-redirection impact reachable by any pool participant whose token is disclosed via this logging side channel. The `GET /auth` signature leak is comparatively lower-value on its own (single timestamp-bound signature), but the reusable v2 token leak directly threatens payout control.

### Likelihood Explanation
Any pool operator's standard web/reverse-proxy access logging (nginx, Apache, aiohttp access logs, CDN/WAF logs) records full request URIs by default, so this secret exposure happens automatically on every `GET /auth` and `GET /farmer` v2 call without any additional attacker action — mirroring exactly the CVE-2023-31207 scenario where credentials transmitted via query parameters ended up in the site's access log. Exploitation only requires read access to those logs (log aggregation systems, shared hosting, misconfigured log retention/exports, or a compromised/curious pool operator), which is a common enough exposure vector for this to be a realistic Medium-severity issue.

### Recommendation
Do not place secrets (`signature`, `authentication_token_v2`) in the URL query string for pool-protocol requests. Send authentication material via the request body (even for logically "GET" semantics, use `POST` with a JSON body) or via a dedicated `Authorization` header, consistent with how `POST`/`PUT` requests already carry payloads in `chia/farmer/farmer.py`'s `make_pool_protocol_request()`. Additionally, require a per-request signature (not just the bearer token) for `PUT /farmer` under protocol v2 so that a leaked bearer token alone is insufficient to change payout instructions.

### Proof of Concept
1. Farmer calls `_pool_get_auth()` → pool responds with `authentication_token_v2`.
2. Farmer calls `_pool_get_farmer()`, which triggers `make_pool_protocol_request(method="GET", ..., request=GetFarmerRequestV2(...))`; the token is serialized into the request URL as `?authentication_token=...&launcher_id=...&authentication_token_v2=<secret>` per `chia/farmer/farmer.py:119-132`.
3. The pool's web server access log records this full URL, including `<secret>`.
4. An entity with read access to that log (log aggregator, backup, or the pool operator itself) extracts `<secret>` and calls `PUT /v2/farmer` with a new `payout_instructions`, per the unsigned v2 path in `_pool_put_farmer()` (`chia/farmer/farmer.py:579-612`), redirecting the victim's mining rewards without needing the farmer's private key.

### Citations

**File:** chia/farmer/farmer.py (L119-132)
```python
) -> tuple[_T_Response | pool_protocol.ErrorResponse | None, aiohttp.ClientResponse | None]:
    self.log.debug("%s /%s request %s", method, endpoint_name, request)
    try:
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.request(
                method,
                self._url_for_endpoint(pool_config, endpoint_name),
                ssl=ssl_context_for_root(get_mozilla_ca_crt(), log=self.log),
                **(
                    # the POST and PUT requests always are not None
                    {"json": request.to_json_dict()}  # type: ignore[union-attr]
                    if method in {"POST", "PUT"}
                    else {"params": request.to_json_dict() if request else None}
                ),
```

**File:** chia/farmer/farmer.py (L423-447)
```python
    async def _get_current_authentication_token(
        self, pool_config: PoolingShareState, authentication_token_timeout: uint8
    ) -> str | pool_protocol.ErrorResponse | None:
        if pool_config.version == 1:
            return str(pool_protocol.get_current_authentication_token(authentication_token_timeout))
        elif pool_config.version == 2:
            cached_auth_token = self.authentication_tokens.get(pool_config.launcher_id, None)
            if cached_auth_token is None or datetime.fromtimestamp(
                cached_auth_token[1], tz=timezone.utc
            ) < datetime.fromtimestamp(self.get_current_time(), tz=timezone.utc):
                auth_response = await self._pool_get_auth(pool_config)
                if isinstance(auth_response, pool_protocol.GetAuthResponse):
                    self.authentication_tokens[pool_config.launcher_id] = (
                        auth_response.authentication_token,
                        auth_response.expiration,
                    )
                    return auth_response.authentication_token
                else:
                    return auth_response
            else:
                # seems sketchy because in theory we should check for non-None here but
                # the auth token can't be None AND expired so semantics guarantee a non-None, non-expired token here
                return cached_auth_token[0]
        else:
            raise ValueError("Unknown pool protocol version specified in pooling config")
```

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

**File:** chia/farmer/farmer.py (L498-540)
```python
    async def _pool_get_farmer(
        self, pool_config: PoolingShareState, authentication_token_timeout: uint8
    ) -> pool_protocol.GetFarmerResponse | pool_protocol.ErrorResponse | None:
        authentication_token = await self._get_current_authentication_token(pool_config, authentication_token_timeout)
        if not isinstance(authentication_token, str):
            self.log.error(f"Failed to authenticate to pool before aquiring farmer details: {pool_config.pool_url}")
            return authentication_token
        if pool_config.version == 1:
            message: bytes32 = std_hash(
                pool_protocol.AuthenticationPayloadV1(
                    "get_farmer",
                    pool_config.launcher_id,
                    pool_config.target_puzzle_hash,
                    uint64(authentication_token),
                )
            )
            authentication_sk = self.get_authentication_sk(pool_config)
            if authentication_sk is None:
                return None
            get_farmer_params: pool_protocol.GetFarmerRequestV1 | pool_protocol.GetFarmerRequestV2 = (
                pool_protocol.GetFarmerRequestV1(
                    authentication_token=uint64(authentication_token),
                    launcher_id=pool_config.launcher_id,
                    signature=AugSchemeMPL.sign(authentication_sk, message),
                    authentication_token_v2="",
                )
            )
        else:
            get_farmer_params = pool_protocol.GetFarmerRequestV2(
                authentication_token=uint64(0),
                launcher_id=pool_config.launcher_id,
                authentication_token_v2=authentication_token,
            )

        response, _ = await make_pool_protocol_request(
            self=self,
            pool_config=pool_config,
            method="GET",
            endpoint_name="farmer",
            request=get_farmer_params,
            response_type=pool_protocol.GetFarmerResponse,
        )
        return response
```

**File:** chia/farmer/farmer.py (L579-612)
```python
    async def _pool_put_farmer(
        self, pool_config: PoolingShareState, authentication_token_timeout: uint8
    ) -> pool_protocol.PutFarmerResponse | pool_protocol.ErrorResponse | None:
        auth_sk: PrivateKey | None = self.get_authentication_sk(pool_config)
        if auth_sk is None:
            return None
        authentication_token = await self._get_current_authentication_token(pool_config, authentication_token_timeout)
        if not isinstance(authentication_token, str):
            self.log.error(f"Attempting to PUT farmer details without being logged into {pool_config.pool_url}")
            return authentication_token
        put_farmer_payload = pool_protocol.PutFarmerPayload(
            pool_config.launcher_id,
            uint64(authentication_token) if pool_config.version == 1 else uint64(0),
            auth_sk.get_g1(),
            pool_config.payout_instructions,
            None,
            authentication_token_v2=authentication_token,
        )
        if pool_config.version == 1:
            # impossible for this to fail when get_authentication_sk above succeeds
            owner_sk = find_owner_sk(self.all_root_sks, pool_config.owner_public_key)[0]  # type: ignore[index]
            signature = AugSchemeMPL.sign(owner_sk, put_farmer_payload.get_hash())
        else:
            signature = None
        put_farmer_request = pool_protocol.PutFarmerRequest(put_farmer_payload, signature)
        response, _ = await make_pool_protocol_request(
            self=self,
            pool_config=pool_config,
            method="PUT",
            endpoint_name="farmer",
            request=put_farmer_request,
            response_type=pool_protocol.PutFarmerResponse,
        )
        return response
```

**File:** chia/protocols/pool_protocol.py (L45-60)
```python
# GET /auth (only v2)


@streamable
@dataclass(frozen=True)
class GetAuthRequest(Streamable):
    launcher_id: bytes32
    timestamp: uint64
    signature: G2Element


@streamable
@dataclass(frozen=True)
class GetAuthResponse(Streamable):
    authentication_token: str
    expiration: uint64
```

**File:** chia/protocols/pool_protocol.py (L108-120)
```python
# GET /farmer
@streamable
@dataclass(frozen=True, kw_only=True)
class GetFarmerRequestV2(Streamable):
    authentication_token: uint64
    launcher_id: bytes32
    authentication_token_v2: str


@streamable
@dataclass(frozen=True, kw_only=True)
class GetFarmerRequestV1(GetFarmerRequestV2):
    signature: G2Element | None = None
```
