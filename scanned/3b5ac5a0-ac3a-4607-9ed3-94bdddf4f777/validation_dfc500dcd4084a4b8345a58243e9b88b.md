## Title
Solana Faucet TCP server binds to `0.0.0.0` and grants unauthenticated airdrops with no default rate caps, allowing any network-reachable client to drain the faucet's keypair balance - (File: `faucet/src/faucet.rs`, `faucet-cli/src/main.rs`)

## Summary
The reported router bug is a class of "unauthenticated network service that spends the service's own funds on request from any reachable client, because it binds to all interfaces and enforces no authentication." The direct Agave analog is `solana-faucet`: the CLI binds its TCP airdrop server to `Ipv4Addr::UNSPECIFIED` (`0.0.0.0`) by default, and the wire protocol (`FaucetRequest::GetAirdrop`) has no authentication whatsoever. Any process that can reach the bound port can request the faucet keypair to sign and send a `Transfer` transaction to an attacker-controlled address, draining the faucet's lamport balance, and by default there is no cap limiting how much can be taken.

## Finding Description
`faucet-cli/src/main.rs` constructs the listen address unconditionally as the unspecified/all-interfaces address and default `FAUCET_PORT`: [1](#0-0) 

That address is handed to `run_faucet`, which does a plain `TcpListener::bind` and, for every accepted connection, calls `process`, which reads a raw `FaucetRequest::GetAirdrop` and hands it straight to `Faucet::process_faucet_request` — there is no handshake, token, signature, or credential check on the connection at all: [2](#0-1) [3](#0-2) 

The only guard that exists is an optional rate limiter (`per_time_cap` / `per_request_cap`), and these are `Option<u64>` that default to `None` (unbounded) unless the operator explicitly passes `--per-time-cap`/`--per-request-cap` on the CLI: [4](#0-3) [5](#0-4) 

Even when caps are configured, `build_airdrop_transaction` explicitly *skips* the IP-based time-limit check for any request originating from a loopback address or an "allowed" IP, and only applies the address-based cap otherwise — the request is still granted and signed with the faucet's private keypair, it is never rejected purely for lack of authorization: [6](#0-5) 

This mirrors the router bug precisely: a privileged signer (the faucet keypair, holding the "liquidity") is reachable over a socket bound to all interfaces, with no authentication, and the only protection is an optional, caller-configurable spending cap that is `None` by default.

## Impact Explanation
Any unprivileged party with network access to the faucet's TCP port (9900 by default) can repeatedly send `GetAirdrop` requests specifying an arbitrary recipient pubkey and lamport amount. With no caps configured (the default), the faucet will keep signing and returning `Transfer` transactions from `faucet_keypair` until its balance is exhausted, i.e., complete theft of the faucet's funds by an unprivileged remote/local actor — functionally identical to the router's `remove-liquidity` drain via `req.body.recipientAddress`.

## Likelihood Explanation
Likelihood is high for any deployment that runs `solana-faucet`/`faucet-cli` with default arguments (as documented/used for devnet-style airdrop services): the binding to `0.0.0.0` and the unauthenticated protocol are the out-of-the-box behavior, not an edge-case misconfiguration. No caps need to be misconfigured — they are absent by default; an operator must deliberately opt in to `--per-time-cap`/`--per-request-cap` to get any protection, and even then loopback/allow-listed IPs bypass the IP-based check.

## Recommendation
- Require the operator to explicitly pass a bind address, and default to `127.0.0.1` instead of `Ipv4Addr::UNSPECIFIED` in `faucet-cli/src/main.rs`.
- Add mandatory, sane default `per_request_cap`/`per_time_cap` values in `Faucet::new`/`Faucet::new_with_allowed_ips` instead of `None` so an unconfigured faucet cannot be fully drained.
- Add an authentication mechanism (shared secret/token, TLS client cert, etc.) to the `FaucetRequest` wire protocol handled in `faucet/src/faucet.rs::process`, rather than relying solely on best-effort IP-based rate limiting.

## Proof of Concept
1. Start the faucet with default arguments: `solana-faucet --keypair funded-faucet.json` (no `--per-time-cap`/`--per-request-cap`). This binds to `0.0.0.0:9900` per `faucet-cli/src/main.rs`.
2. From any host that can reach port 9900, open a raw TCP connection and send a wincode-serialized `FaucetRequest::GetAirdrop { lamports: <faucet_balance>, to: <attacker_pubkey>, blockhash: <recent_blockhash> }`, matching the format consumed in `faucet/src/faucet.rs::process` / `process_faucet_request`.
3. The faucet signs and returns a `Transaction` transferring the requested lamports from `faucet_keypair` to the attacker's pubkey; broadcasting it drains the faucet's balance in a single unauthenticated request, with no cap preventing the full amount from being requested at once.

### Citations

**File:** faucet-cli/src/main.rs (L45-59)
```rust
        .arg(
            Arg::with_name("per_time_cap")
                .long("per-time-cap")
                .alias("cap")
                .value_name("NUM")
                .takes_value(true)
                .help("Request limit for time slice, in SOL"),
        )
        .arg(
            Arg::with_name("per_request_cap")
                .long("per-request-cap")
                .value_name("NUM")
                .takes_value(true)
                .help("Request limit for a single request, in SOL"),
        )
```

**File:** faucet-cli/src/main.rs (L85-97)
```rust
    #[allow(deprecated)]
    let faucet_addr = socketaddr!(Ipv4Addr::UNSPECIFIED, FAUCET_PORT);

    let faucet = Arc::new(Mutex::new(
        #[allow(deprecated)]
        Faucet::new_with_allowed_ips(
            faucet_keypair,
            time_slice,
            per_time_cap,
            per_request_cap,
            allowed_ips,
        ),
    ));
```

**File:** faucet/src/faucet.rs (L118-144)
```rust
    pub fn new_with_allowed_ips(
        faucet_keypair: Keypair,
        time_input: Option<u64>,
        per_time_cap: Option<u64>,
        per_request_cap: Option<u64>,
        allowed_ips: HashSet<IpAddr>,
    ) -> Self {
        let time_slice = Duration::new(time_input.unwrap_or(TIME_SLICE), 0);
        if let Some((per_request_cap, per_time_cap)) = per_request_cap.zip(per_time_cap)
            && per_time_cap < per_request_cap
        {
            warn!(
                "per_time_cap {} SOL < per_request_cap {} SOL; maximum single requests will fail",
                build_balance_message(per_time_cap, false, false),
                build_balance_message(per_request_cap, false, false),
            );
        }
        Self {
            faucet_keypair,
            ip_cache: HashMap::new(),
            address_cache: HashMap::new(),
            time_slice,
            per_time_cap,
            per_request_cap,
            allowed_ips,
        }
    }
```

**File:** faucet/src/faucet.rs (L215-227)
```rust
                if !ip.is_loopback() && !self.allowed_ips.contains(&ip) {
                    self.check_time_request_limit(lamports, ip)?;
                }
                self.check_time_request_limit(lamports, to)?;

                let transfer_instruction = transfer(&mint_pubkey, &to, lamports);
                let message = Message::new(&[transfer_instruction], Some(&mint_pubkey));
                Ok(FaucetTransaction::Airdrop(Transaction::new(
                    &[&self.faucet_keypair],
                    message,
                    blockhash,
                )))
            }
```

**File:** faucet/src/faucet.rs (L396-444)
```rust
pub async fn run_faucet(
    faucet: Arc<Mutex<Faucet>>,
    faucet_addr: SocketAddr,
    sender: Option<Sender<Result<SocketAddr, String>>>,
) {
    let listener = TcpListener::bind(&faucet_addr).await;
    if let Some(sender) = sender {
        sender
            .send(
                listener
                    .as_ref()
                    .map(|listener| listener.local_addr().unwrap())
                    .map_err(|err| {
                        format!(
                            "Unable to bind faucet to {faucet_addr:?}, check the address is not \
                             already in use: {err}"
                        )
                    }),
            )
            .unwrap();
    }

    let listener = match listener {
        Err(err) => {
            error!("Faucet failed to start: {err}");
            return;
        }
        Ok(listener) => listener,
    };
    info!("Faucet started. Listening on: {faucet_addr}");
    info!(
        "Faucet account address: {}",
        faucet.lock().unwrap().faucet_keypair.pubkey()
    );

    loop {
        let faucet = faucet.clone();
        match listener.accept().await {
            Ok((stream, _)) => {
                tokio::spawn(async move {
                    if let Err(e) = process(stream, faucet).await {
                        info!("failed to process request; error = {e:?}");
                    }
                });
            }
            Err(e) => debug!("failed to accept socket; error = {e:?}"),
        }
    }
}
```

**File:** faucet/src/faucet.rs (L446-489)
```rust
async fn process(
    mut stream: TokioTcpStream,
    faucet: Arc<Mutex<Faucet>>,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut request = vec![
        0u8;
        serialized_size(&FaucetRequest::GetAirdrop {
            lamports: u64::default(),
            to: Pubkey::default(),
            blockhash: Hash::default(),
        })
        .unwrap() as usize
    ];
    while stream.read_exact(&mut request).await.is_ok() {
        trace!("{request:?}");

        let response = {
            match stream.peer_addr() {
                Err(e) => {
                    info!("{:?}", e.into_inner());
                    ERROR_RESPONSE.to_vec()
                }
                Ok(peer_addr) => {
                    let ip = peer_addr.ip();
                    info!("Request IP: {ip:?}");

                    match faucet.lock().unwrap().process_faucet_request(&request, ip) {
                        Ok(response_bytes) => {
                            trace!("Airdrop response_bytes: {response_bytes:?}");
                            response_bytes
                        }
                        Err(e) => {
                            info!("Error in request: {e}");
                            ERROR_RESPONSE.to_vec()
                        }
                    }
                }
            }
        };
        stream.write_all(&response).await?;
    }

    Ok(())
}
```
