Based on my investigation, I could not identify a valid strong analog in this codebase.

**Analysis of the external report:** The RabbitMQ CVE is specifically about `read_complete_body` checking the accumulated body size *before* reading the final chunk, but never re-validating the *final combined* size after that last chunk is appended — allowing a body larger than `max_http_body_size` to slip through on `with_decode`/`direct_request` paths. This is an HTTP-body-accumulation check-ordering bug tied to chunked reads in a web server component.

**Why it doesn't transfer to Protobuf:** Protobuf has no HTTP server component, but the closer analog would be its own chunked/streaming size-limit enforcement in parsers (`CodedInputStream`, `EpsCopyInputStream`, C# `ParsingPrimitives`). I traced all of these:

- Java `CodedInputStream.readRawBytesSlowPathOneChunk` computes `rawPos = totalBytesRetired + pos` and calls `isBeyondLimit(rawPos, size, sizeLimit)` — this validates the position **plus the incoming chunk size** against the limit *before* allocating/copying, i.e. it checks the final combined size, not just the size accumulated so far. [1](#0-0) 
- The same pattern (position + incoming size compared to limit, prior to consuming) appears in `tryRefillBuffer`. [2](#0-1) 
- C++ `CodedInputStream::Refresh()` and `RecomputeBufferLimits()` recompute `buffer_size_after_limit_` against `min(current_limit_, total_bytes_limit_)` every time the buffer is refilled, including on overflow-safe arithmetic for the final chunk.
<invoke name="codebase_search">
<parameter name="query">placeholder</parameter>
</invoke>

### Citations

**File:** java/core/src/main/java/com/google/protobuf/CodedInputStream.java (L2297-2309)
```java
      // Check whether the size of total message needs to read is bigger than the size limit.
      // We shouldn't throw an exception here as isAtEnd() function needs to get this function's
      // return as the result.
      int rawPos = totalBytesRetired + pos;
      if (isBeyondLimit(rawPos, n, sizeLimit)) {
        return false;
      }

      // Shouldn't throw the exception here either.
      if (isBeyondLimit(rawPos, n, currentLimit)) {
        // Oops, we hit a limit.
        return false;
      }
```

**File:** java/core/src/main/java/com/google/protobuf/CodedInputStream.java (L2456-2460)
```java
      // Integer-overflow-conscious check that the message size so far has not exceeded sizeLimit.
      int rawPos = totalBytesRetired + pos;
      if (isBeyondLimit(rawPos, size, sizeLimit)) {
        throw InvalidProtocolBufferException.sizeLimitExceeded();
      }
```
