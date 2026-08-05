[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** streamer/src/nonblocking/quic.rs (L1-10)
```rust
use {
    crate::{
        nonblocking::{
            connection_rate_limiter::ConnectionRateLimiter,
            qos::{ConnectionContext, OpaqueStreamerCounter, QosController},
        },
        quic::{QuicServerError, QuicStreamerConfig, StreamerStats, configure_server},
        quic_socket::{QuicSocket, QuicXdpSocketParts, QuicXdpTxSocket},
        streamer::StakedNodes,
    },
```

**File:** streamer/src/nonblocking/quic.rs (L54-54)
```rust
pub const DEFAULT_WAIT_FOR_CHUNK_TIMEOUT: Duration = Duration::from_secs(2);
```

**File:** streamer/src/nonblocking/quic.rs (L89-94)
```rust
/// How many RTTs worth of delay can we tolerate on stream reassembly
/// before considering stream to be "too late". 1.5 RTT should be enough
/// for any reasonable fragmentation to be resolved, so the only way
/// a stream reassembly would be delayed more is when something
/// extraordinary has occured (congestion control or flow control blocking)
const LATE_REASSEMBLY_THRESHOLD: f32 = 1.5;
```

**File:** streamer/src/quic.rs (L1-1)
```rust
use {
```
